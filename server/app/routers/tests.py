import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import page, record, require, require_live, session
from app.errors import AppError
from app.models import (
    AppVersion,
    Assertion,
    Project,
    TestCase,
    Trace,
)
from app.schemas import (
    AssertionCreate,
    AssertionUpdate,
    Page,
    SeedCreate,
    SplitCreate,
    TestCreate,
    TestUpdate,
    entity,
)
from app.services.evaluator import prepare, execute

router = APIRouter(prefix="/v1", tags=["Tests and assertions"])


async def source_trace(db, project_id, trace_id):
    trace = await require(db, Trace, trace_id)
    version = await require(db, AppVersion, trace.app_version_id)
    if version.project_id != project_id:
        raise AppError(
            "project_mismatch", "Source trace belongs to another project", 422
        )
    return trace


@router.post(
    "/projects/{id}/test-cases",
    response_model=entity(TestCase),
    status_code=201,
    summary="Create a test case",
)
async def create(
    id: UUID, data: TestCreate, db: AsyncSession = Depends(session, scope="function")
):
    await require(db, Project, id)
    if data.source_trace_id:
        await source_trace(db, id, data.source_trace_id)
    if data.origin == "from_trace" and data.source_trace_id is None:
        raise AppError(
            "source_trace_required",
            "A trace-derived test requires source_trace_id",
            422,
        )
    row = TestCase(project_id=id, **data.model_dump())
    db.add(row)
    await db.flush()
    return record(row)


@router.get(
    "/projects/{id}/test-cases", response_model=Page, summary="List project test cases"
)
async def listing(
    id: UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    db: AsyncSession = Depends(session, scope="function"),
):
    await require(db, Project, id)
    return await page(
        db,
        TestCase,
        (TestCase.project_id == id, TestCase.archived_at.is_(None)),
        limit,
        cursor,
    )


@router.get(
    "/test-cases/{id}", response_model=entity(TestCase), summary="Read a test case"
)
async def get(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    return record(await require_live(db, TestCase, id))


@router.patch(
    "/test-cases/{id}", response_model=entity(TestCase), summary="Update a test case"
)
async def update(
    id: UUID, data: TestUpdate, db: AsyncSession = Depends(session, scope="function")
):
    row = await require_live(db, TestCase, id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise AppError("invalid_test", "Test fields cannot be null", 422)
        if key == "input" and row.origin == "from_trace" and value != row.input:
            raise AppError(
                "trace_input_mismatch",
                "Trace-derived tests preserve the original input",
                422,
            )
        setattr(row, key, value)
    await db.flush()
    return record(row)


@router.delete(
    "/test-cases/{id}",
    status_code=204,
    summary="Archive a test case and preserve its run history",
)
async def remove(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    row = await require_live(db, TestCase, id)
    row.archived_at = datetime.now(UTC)
    await db.flush()
    return Response(status_code=204)


@router.post(
    "/projects/{id}/test-cases/from-trace/{trace_id}",
    response_model=entity(TestCase),
    status_code=201,
    summary="Create a regression test from the original trace input",
)
async def from_trace(
    id: UUID,
    trace_id: UUID,
    data: TestUpdate | None = None,
    db: AsyncSession = Depends(session, scope="function"),
):
    await require(db, Project, id)
    trace = await source_trace(db, id, trace_id)
    if not trace.input:
        raise AppError("missing_trace_input", "Source trace has no input", 422)
    if data and data.input is not None and data.input != trace.input:
        raise AppError(
            "trace_input_mismatch",
            "Trace-derived tests preserve the original input",
            422,
        )
    row = TestCase(
        project_id=id,
        source_trace_id=trace.id,
        origin="from_trace",
        input=trace.input,
        expected=data.expected if data and data.expected is not None else {},
        split=data.split if data and data.split else "dev",
    )
    db.add(row)
    await db.flush()
    return record(row)


@router.post(
    "/projects/{id}/test-cases/seed",
    response_model=Page,
    status_code=201,
    summary="Forward seed generation to the evaluator",
)
async def seed(
    id: UUID,
    data: SeedCreate,
    request: Request,
    db: AsyncSession = Depends(session, scope="function"),
):
    await require(db, Project, id)
    result = await execute(
        db,
        request.app.state,
        await prepare(db, request.app.state, "seed", data, project_id=id),
    )
    return {"items": result["items"], "next_cursor": None}


@router.post(
    "/projects/{id}/test-cases/split",
    response_model=Page,
    summary="Assign stable dev and holdout splits by test ID",
)
async def split(
    id: UUID, data: SplitCreate, db: AsyncSession = Depends(session, scope="function")
):
    await require(db, Project, id)
    rows = list(
        (
            await db.scalars(
                select(TestCase)
                .where(TestCase.project_id == id, TestCase.archived_at.is_(None))
                .order_by(TestCase.id)
            )
        ).all()
    )
    for row in rows:
        bucket = (
            int.from_bytes(hashlib.sha256(str(row.id).encode()).digest()[:8], "big")
            % 10
        )
        row.split = "dev" if bucket < 7 else "holdout"
    await db.flush()
    return {"items": [record(row) for row in rows], "next_cursor": None}


@router.post(
    "/test-cases/{id}/assertions",
    response_model=entity(Assertion),
    status_code=201,
    summary="Add a deterministic or judge assertion",
)
async def create_assertion(
    id: UUID,
    data: AssertionCreate,
    db: AsyncSession = Depends(session, scope="function"),
):
    await require_live(db, TestCase, id)
    row = Assertion(test_case_id=id, **data.model_dump())
    db.add(row)
    await db.flush()
    return record(row)


@router.get(
    "/test-cases/{id}/assertions",
    response_model=Page,
    summary="List assertions for a test case",
)
async def assertions(
    id: UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    db: AsyncSession = Depends(session, scope="function"),
):
    await require_live(db, TestCase, id)
    return await page(
        db,
        Assertion,
        (Assertion.test_case_id == id, Assertion.archived_at.is_(None)),
        limit,
        cursor,
    )


@router.patch(
    "/assertions/{id}", response_model=entity(Assertion), summary="Update an assertion"
)
async def update_assertion(
    id: UUID,
    data: AssertionUpdate,
    db: AsyncSession = Depends(session, scope="function"),
):
    row = await require_live(db, Assertion, id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise AppError("invalid_assertion", "Assertion fields cannot be null", 422)
        setattr(row, key, value)
    await db.flush()
    return record(row)


@router.delete(
    "/assertions/{id}",
    status_code=204,
    summary="Archive an assertion and preserve its saved evidence",
)
async def remove_assertion(
    id: UUID, db: AsyncSession = Depends(session, scope="function")
):
    row = await require_live(db, Assertion, id)
    row.archived_at = datetime.now(UTC)
    await db.flush()
    return Response(status_code=204)
