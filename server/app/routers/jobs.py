import asyncio
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.errors import AppError, error_body
from app.schemas import JobResponse

router = APIRouter(prefix="/v1/jobs", tags=["Jobs"])


def get_job(state, identifier):
    value = state.job_store.get(str(identifier))
    if value is None:
        raise AppError("job_not_found", "Job expired or unknown", 404)
    return value


async def submit(state, background, operation):
    identifier = str(uuid4())
    value = {
        "job_id": identifier,
        "status": "queued",
        "progress": 0,
        "result": None,
        "error": None,
    }
    state.job_store.put(identifier, value, 86400)

    async def run():
        state.job_store.update(identifier, status="running")

        async def progress(fraction):
            state.job_store.update(identifier, progress=max(0, min(float(fraction), 1)))

        try:
            async with state.sessions() as db, db.begin():
                db.info["redactor"] = state.redactor
                result = await operation(db, progress)
            state.job_store.update(
                identifier,
                status="completed",
                progress=1,
                result=state.redactor.scrub(result),
            )
        except AppError as exc:
            state.job_store.update(
                identifier,
                status="failed",
                error=state.redactor.scrub(
                    error_body(exc.code, exc.message, exc.details)
                ),
            )
        except Exception:
            state.job_store.update(
                identifier,
                status="failed",
                error=error_body("job_failed", "Operation failed"),
            )

    background.add_task(run)
    return value


@router.get("/{id}", response_model=JobResponse, summary="Read job progress and result")
async def status(id: UUID, request: Request):
    return get_job(request.app.state, id)


@router.get(
    "/{id}/events", summary="Stream job progress", response_class=EventSourceResponse
)
async def events(id: UUID, request: Request):
    get_job(request.app.state, id)

    async def generate():
        previous = None
        while True:
            value = get_job(request.app.state, id)
            encoded = json.dumps(value, default=str)
            if encoded != previous:
                yield {"event": "progress", "data": encoded}
                previous = encoded
            if value["status"] in {"completed", "failed"}:
                yield {"event": "done", "data": "{}"}
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.2)

    return EventSourceResponse(generate(), send_timeout=20)
