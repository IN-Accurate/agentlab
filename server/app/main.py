import logging
from contextlib import asynccontextmanager

from cryptography.fernet import Fernet

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.config import Settings
from app.db import connect
from app.errors import AppError, error_body
from app.security import Redactor, RedactionFilter, rate_limit
from app.store import CredentialStore, JobStore


def create_app(settings=None):
    settings = settings or Settings()
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
            yield
        finally:
            for handler, f in filters:
                handler.removeFilter(f)
            await app.state.engine.dispose()

    app = FastAPI(title="TestLab API", version="0.1.0", lifespan=lifespan)
    app.state.settings, app.state.sessions = settings, sessions
    app.state.engine = engine
    app.state.redactor = Redactor()
    app.state.credential_store = CredentialStore()
    app.state.job_store = JobStore()
    app.state.rate_limits = {}
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[x.strip() for x in settings.cors_origins.split(",")],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def guard(request, call_next):
        if any(
            word in request.url.query.lower()
            for word in ("api_key", "apikey", "authorization")
        ):
            return reply(
                request, "key_in_url", "Credentials must not be placed in URLs", 400
            )
        if request.method == "POST" and (
            request.url.path == "/v1/credentials" or request.url.path.endswith("/chat")
        ):
            try:
                if settings.app_env == "production" and request.url.scheme != "https":
                    raise AppError("https_required", "HTTPS is required", 400)
                rate_limit(
                    request,
                    settings.credential_rate_limit
                    if request.url.path == "/v1/credentials"
                    else settings.chat_rate_limit,
                )
            except AppError as exc:
                return reply(request, exc.code, exc.message, exc.status)
        return await call_next(request)

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

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        return reply(
            request, "internal_error", "An internal server error occurred", 500
        )

    @app.get(
        "/health", tags=["Health"], summary="Check service health", response_model=dict
    )
    async def health():
        return {"status": "ok"}

    @app.get(
        "/v1/meta",
        tags=["Health"],
        summary="Read API capabilities",
        response_model=dict,
    )
    async def meta():
        return {
            "version": "0.1.0",
            "providers": ["openrouter", "meta", "custom"],
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

    return app


app = create_app()
