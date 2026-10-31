from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.db import require, session
from app.errors import AppError
from app.models import Project
from app.schemas import ChatCreate
from app.services.evaluator import prepare, require_backend
from app.services.chat import events

router = APIRouter(prefix="/v1/projects", tags=["Chat"])


@router.post(
    "/{id}/chat",
    response_class=EventSourceResponse,
    summary="Stream evaluator chat events and result cards",
)
async def chat(
    id: UUID,
    data: ChatCreate,
    request: Request,
    # Chat streams while the response is open, so its session must stay request-scoped.
    db: AsyncSession = Depends(session, scope="request"),
):
    backend = require_backend(request.app.state)
    if not callable(getattr(backend, "stream_chat", None)):
        raise AppError(
            "chat_not_configured", "Evaluator does not implement stream_chat", 503
        )
    await require(db, Project, id)
    prepared = await prepare(db, request.app.state, "chat", data, project_id=id)
    return EventSourceResponse(events(request.app.state, db, prepared), send_timeout=20)
