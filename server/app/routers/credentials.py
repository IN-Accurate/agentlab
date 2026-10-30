from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.schemas import CredentialCreate, CredentialResponse
from app.security import (
    create_credential,
    credential_metadata,
    revoke_credential,
    validate_url,
)

router = APIRouter(prefix="/v1/credentials", tags=["Credentials"])


@router.post(
    "",
    response_model=CredentialResponse,
    status_code=201,
    summary="Encrypt and store a temporary credential",
)
async def create(data: CredentialCreate, request: Request):
    if data.base_url:
        await validate_url(
            data.base_url, request.app.state.settings.testlab_allow_insecure_local
        )
    state = request.app.state
    return create_credential(
        data, state.settings, state.credential_store, state.redactor
    )


@router.get(
    "/{id}", response_model=CredentialResponse, summary="Read credential metadata"
)
async def metadata(id: UUID, request: Request):
    return credential_metadata(str(id), request.app.state.credential_store)


@router.delete("/{id}", status_code=204, summary="Revoke a credential immediately")
async def revoke(id: UUID, request: Request):
    revoke_credential(str(id), request.app.state.credential_store)
    return Response(status_code=204)
