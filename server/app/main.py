import hmac
import logging
from contextlib import asynccontextmanager

from cryptography.fernet import Fernet

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError
from starlette.exceptions import HTTPException

from app.config import Settings
from app.db import connect
from app.errors import AppError, error_body
from app.security import (
    CREDENTIAL_PATHS,
    Redactor,
    RedactionFilter,
    is_huggingface_path,
    rate_limit,
)
from app.store import CredentialStore, JobStore
from app.schemas import HealthResponse, MetaResponse
from app.services.credentials import SavedCredentials
from app.routers import (
    chat,
    credentials,
    dashboard,
    fixloop,
    huggingface,
    jobs,
    mvp,
    projects,
    runs,
    tests,
    traces,
)

MAX_BODY_BYTES = 10_000_000


def create_app(settings=None, evaluator=None):
    settings = (settings or Settings()).model_copy()
    persistent_credentials = bool(settings.app_encryption_key)
    if not settings.app_encryption_key:
        settings.app_encryption_key = Fernet.generate_key().decode()
        logging.getLogger(__name__).warning(
            "Ephemeral encryption key: credentials do not survive restart"
        )
    engine, sessions = connect(settings)

    @asynccontextmanager
    async def lifespan(app):
        filters = []
        for logger in [
            logging.getLogger(),
            *[
                v
                for v in logging.Logger.manager.loggerDict.values()
                if isinstance(v, logging.Logger)
            ],
        ]:
            for handler in logger.handlers:
                f = RedactionFilter(app.state.redactor)
                handler.addFilter(f)
                filters.append((handler, f))
        try:
            if app.state.saved_credentials is not None:
                await app.state.saved_credentials.restore()
            yield
        finally:
            for handler, f in filters:
                handler.removeFilter(f)
            await app.state.engine.dispose()

    app = FastAPI(title="AgentLab API", version="0.1.0", lifespan=lifespan)
    app.state.settings, app.state.sessions = settings, sessions
    app.state.engine = engine
    app.state.evaluator = evaluator
    app.state.redactor = Redactor()
    app.state.credential_store = CredentialStore(on_evict=app.state.redactor.discard)
    app.state.saved_credentials = (
        SavedCredentials(app.state) if persistent_credentials else None
    )
    app.state.job_store = JobStore()
    app.state.rate_limits = {}

    @app.middleware("http")
    async def guard(request, call_next):
        if request.url.path.startswith("/v1/"):
            transfer = request.headers.get("transfer-encoding", "")
            if "chunked" in transfer.lower() and not request.headers.get(
                "content-length"
            ):
                return reply(
                    request,
                    "length_required",
                    "Content-Length is required",
                    411,
                )
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
                return reply(
                    request,
                    "body_too_large",
                    "Request body exceeds the 10MB limit",
                    413,
                )
            key = settings.agentlab_api_key
            if key and not hmac.compare_digest(
                request.headers.get("x-api-key", "").encode(), key.encode()
            ):
                return reply(request, "unauthorized", "Valid API key required", 401)
        if any(
            word in request.url.query.lower()
            for word in ("api_key", "apikey", "authorization")
        ):
            return reply(
                request, "key_in_url", "Credentials must not be placed in URLs", 400
            )
        if request.method == "POST" and (
            request.url.path in CREDENTIAL_PATHS or request.url.path.endswith("/chat")
        ):
            try:
                if settings.app_env == "production" and request.url.scheme != "https":
                    raise AppError("https_required", "HTTPS is required", 400)
                rate_limit(
                    request,
                    settings.credential_rate_limit
                    if request.url.path in CREDENTIAL_PATHS
                    else settings.chat_rate_limit,
                )
            except AppError as exc:
                return reply(request, exc.code, exc.message, exc.status)
        if is_huggingface_path(request.url.path):
            try:
                rate_limit(request, settings.huggingface_rate_limit)
            except AppError as exc:
                return reply(request, exc.code, exc.message, exc.status)
        return await call_next(request)

    # Registered after guard so CORS runs outermost: preflights are answered
    # before auth, and guard errors leave with CORS headers attached.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[x.strip() for x in settings.cors_origins.split(",")],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Api-Key"],
    )

    def reply(request, code, message, status, details=None):
        body = error_body(code, message, details)
        redactor = getattr(request.app.state, "redactor", None)
        return JSONResponse(
            redactor.scrub(body) if redactor else body, status_code=status
        )

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return reply(request, exc.code, exc.message, exc.status, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return reply(request, "validation_error", "Invalid request", 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return reply(request, "http_error", str(exc.detail), exc.status_code)

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError):
        return reply(request, "conflict", "Database constraint violation", 409)

    @app.exception_handler(DBAPIError)
    async def db_error(request: Request, exc: DBAPIError):
        if "immutable_app_version" in str(exc):
            return reply(
                request,
                "immutable_version",
                "Create a new version instead of updating an existing one",
                409,
            )
        return reply(
            request, "internal_error", "An internal server error occurred", 500
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        return reply(
            request, "internal_error", "An internal server error occurred", 500
        )

    @app.get(
        "/health",
        tags=["Health"],
        summary="Check service health",
        response_model=HealthResponse,
    )
    async def health():
        return {"status": "ok"}

    @app.get(
        "/v1/meta",
        tags=["Health"],
        summary="Read API capabilities",
        response_model=MetaResponse,
    )
    async def meta():
        return {
            "version": "0.1.0",
            "providers": ["openrouter", "meta", "custom"],
            "evaluator_configured": app.state.evaluator is not None,
            "agent_types": ["hosted", "openai_compatible"],
            "sse_events": [
                "message.delta",
                "step.started",
                "step.finished",
                "tool.result",
                "error",
                "done",
            ],
        }

    app.include_router(credentials.router)
    app.include_router(projects.router)
    app.include_router(traces.router)
    app.include_router(tests.router)
    app.include_router(huggingface.router)
    app.include_router(runs.router)
    app.include_router(jobs.router)
    app.include_router(mvp.router)
    app.include_router(fixloop.router)
    app.include_router(dashboard.router)
    app.include_router(chat.router)
    return app


app = create_app()
