from ipaddress import ip_address
from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.errors import AppError
from app.schemas import (
    CredentialCreate,
    CredentialResponse,
    DefaultCredentialResponse,
)
from app.security import (
    create_credential,
    credential_metadata,
    revoke_credential,
    validate_url,
)

router = APIRouter(prefix="/v1/credentials", tags=["Credentials"])


FORWARD_HEADERS = ("forwarded", "x-forwarded-for", "x-real-ip")


def is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


def default_allowed(request):
    """Without an API key guarding /v1, only this machine may spend the key."""
    settings = request.app.state.settings
    if settings.agentlab_api_key:
        return True
    if not settings.agentlab_unauthenticated_default_key:
        return False
    # A proxy on this machine connects from loopback on behalf of remote users.
    if any(name in request.headers for name in FORWARD_HEADERS):
        return False
    # Both ends must be this machine; a proxy that strips forwarding headers
    # still passes on the public Host the remote user asked for.
    if "host" not in request.headers or not is_loopback(request.url.hostname):
        return False
    return is_loopback(getattr(request.client, "host", None))


def configured_default(settings):
    key = settings.meta_api_key
    if (
        not key
        or key.get_secret_value() == "REPLACE_ME"
        or not settings.meta_base_url
        or not settings.meta_model
    ):
        return None
    return key


@router.post(
    "",
    response_model=CredentialResponse,
    status_code=201,
    summary="Encrypt and store a temporary credential",
)
async def create(data: CredentialCreate, request: Request):
    if data.base_url:
        await validate_url(
            data.base_url, request.app.state.settings.agentlab_allow_insecure_local
        )
    state = request.app.state
    return create_credential(
        data, state.settings, state.credential_store, state.redactor
    )


@router.get(
    "/default",
    response_model=DefaultCredentialResponse,
    summary="Check for a server-side default Meta key",
)
async def default_status(request: Request):
    settings = request.app.state.settings
    if not configured_default(settings) or not default_allowed(request):
        return {"available": False}
    return {
        "available": True,
        "base_url": settings.meta_base_url,
        "model": settings.meta_model,
    }


@router.post(
    "/default",
    response_model=CredentialResponse,
    status_code=201,
    summary="Store a temporary credential from the server-side default Meta key",
)
async def create_default(request: Request):
    state = request.app.state
    settings = state.settings
    key = configured_default(settings)
    if key and not default_allowed(request):
        raise AppError(
            "default_credential_forbidden",
            "Set AGENTLAB_API_KEY to use the default key from other machines",
            403,
        )
    if not key:
        raise AppError(
            "default_credential_unavailable",
            "No default Meta key is configured on the server",
            404,
        )
    await validate_url(settings.meta_base_url, settings.agentlab_allow_insecure_local)
    data = CredentialCreate(
        provider="custom",
        base_url=settings.meta_base_url,
        model=settings.meta_model,
        api_key=key,
    )
    return create_credential(data, settings, state.credential_store, state.redactor)


@router.get(
    "/{id}", response_model=CredentialResponse, summary="Read credential metadata"
)
async def metadata(id: UUID, request: Request):
    return credential_metadata(str(id), request.app.state.credential_store)


@router.delete("/{id}", status_code=204, summary="Revoke a credential immediately")
async def revoke(id: UUID, request: Request):
    revoke_credential(str(id), request.app.state.credential_store)
    return Response(status_code=204)
