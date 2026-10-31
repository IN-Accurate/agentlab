from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import page, record, require, session
from app.errors import AppError
from app.models import AppVersion, RetrievedChunk, Span, Trace
from app.schemas import Page, TraceCreate, TraceDetail

router = APIRouter(prefix="/v1/traces", tags=["Traces"])


async def details(db, identifier):
    trace = await require(db, Trace, identifier)
    spans = list(
        await db.scalars(
            select(Span)
            .where(Span.trace_id == identifier)
            .order_by(Span.started_at.asc().nulls_last(), Span.created_at, Span.id)
        )
    )
    chunks = list(
        await db.scalars(
            select(RetrievedChunk)
            .where(RetrievedChunk.span_id.in_([span.id for span in spans]))
            .order_by(RetrievedChunk.rank)
        )
    )
    values = {span.id: {**record(span), "chunks": [], "children": []} for span in spans}
    for chunk in chunks:
        values[chunk.span_id]["chunks"].append(record(chunk))
    roots = []
    for span in spans:
        if span.parent_span_id in values:
            values[span.parent_span_id]["children"].append(values[span.id])
        else:
            roots.append(values[span.id])
    flat = [
        {k: v for k, v in values[span.id].items() if k != "children"} for span in spans
    ]
    return {**record(trace), "spans": flat, "span_tree": roots}


async def ingest(db, data, redactor):
    await require(db, AppVersion, data.app_version_id)
    safe = redactor.scrub(data.model_dump(mode="json"))
    await db.execute(
        insert(Trace)
        .values(
            id=data.id,
            app_version_id=data.app_version_id,
            input=safe["input"],
            output=safe["output"],
            status=data.status,
        )
        .on_conflict_do_nothing(index_elements=[Trace.id])
    )
    # Serialize duplicate SDK submissions before checking existing span IDs.
    trace = await db.scalar(select(Trace).where(Trace.id == data.id).with_for_update())
    if trace.app_version_id != data.app_version_id:
        raise AppError(
            "trace_conflict", "Trace ID already belongs to another version", 409
        )
    existing = {
        row.sdk_span_id: row
        for row in await db.scalars(select(Span).where(Span.trace_id == data.id))
    }
    pending = {
        span.sdk_span_id: span
        for span in data.spans
        if span.sdk_span_id not in existing
    }
    known = set(existing) | set(pending)
    if any(
        span.parent_sdk_span_id and span.parent_sdk_span_id not in known
        for span in data.spans
    ):
        raise AppError(
            "invalid_span_parent", "Parent span does not exist in this trace", 422
        )
    rows = {}
    while pending:
        ready = [
            span
            for span in pending.values()
            if span.parent_sdk_span_id is None or span.parent_sdk_span_id in existing
        ]
        if not ready:
            raise AppError("invalid_span_parent", "Span parents contain a cycle", 422)
        for span in ready:
            row = Span(
                id=uuid4(),
                trace_id=data.id,
                parent_span_id=existing[span.parent_sdk_span_id].id
                if span.parent_sdk_span_id
                else None,
                **span.model_dump(exclude={"parent_sdk_span_id", "chunks", "payload"}),
                payload=redactor.scrub(span.payload),
            )
            db.add(row)
            rows[span.sdk_span_id] = (row, span)
            existing[span.sdk_span_id] = row
            del pending[span.sdk_span_id]
        # Flush each wave: parents must be in the transaction before their
        # children are inserted, and the unit of work cannot sort the
        # self-referential spans table against retrieved_chunks on its own.
        await db.flush()
    for row, span in rows.values():
        for chunk in span.chunks:
            db.add(RetrievedChunk(span_id=row.id, **redactor.scrub(chunk.model_dump())))
    await db.flush()
    return await details(db, data.id)


@router.post(
    "",
    response_model=TraceDetail,
    status_code=201,
    summary="Idempotently ingest a trace and span tree",
)
async def create(
    data: TraceCreate,
    request: Request,
    db: AsyncSession = Depends(session, scope="function"),
):
    return await ingest(db, data, request.app.state.redactor)


@router.get("", response_model=Page, summary="List traces by version and status")
async def listing(
    version_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    db: AsyncSession = Depends(session, scope="function"),
):
    filters = []
    if version_id:
        filters.append(Trace.app_version_id == version_id)
    if status:
        filters.append(Trace.status == status)
    return await page(db, Trace, filters, limit, cursor)


@router.get(
    "/{id}",
    response_model=TraceDetail,
    summary="Read trace, span tree and retrieved chunks",
)
async def get(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    return await details(db, id)


@router.get("/{id}/spans", response_model=Page, summary="Read trace spans and chunks")
async def spans(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    return {"items": (await details(db, id))["spans"], "next_cursor": None}


@router.get(
    "/{id}/timeline",
    response_model=Page,
    summary="Read a flat ordered trace timeline with latency",
)
async def timeline(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    return {"items": (await details(db, id))["spans"], "next_cursor": None}
