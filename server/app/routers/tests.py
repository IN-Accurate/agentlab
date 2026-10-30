import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import page, record, require, session
from app.errors import AppError
from app.models import (
    AppVersion,
    Assertion,
    AssertionResult,
    Project,
    RunResult,
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
from app.services import llm

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
async def create(id: UUID, data: TestCreate, db: AsyncSession = Depends(session)):
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
    cursor: UUID | None = None,
    db: AsyncSession = Depends(session),
):
    await require(db, Project, id)
    return await page(db, TestCase, (TestCase.project_id == id,), limit, cursor)


@router.get(
    "/test-cases/{id}", response_model=entity(TestCase), summary="Read a test case"
)
async def get(id: UUID, db: AsyncSession = Depends(session)):
    return record(await require(db, TestCase, id))


@router.patch(
    "/test-cases/{id}", response_model=entity(TestCase), summary="Update a test case"
)
async def update(id: UUID, data: TestUpdate, db: AsyncSession = Depends(session)):
    row = await require(db, TestCase, id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise AppError("invalid_test", "Test fields cannot be null", 422)
        setattr(row, key, value)
    await db.flush()
    return record(row)


@router.delete(
    "/test-cases/{id}",
    status_code=204,
    summary="Delete a test case and its assertion results",
)
async def remove(id: UUID, db: AsyncSession = Depends(session)):
    await require(db, TestCase, id)
    await db.execute(delete(RunResult).where(RunResult.test_case_id == id))
    await db.execute(delete(TestCase).where(TestCase.id == id))
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
    db: AsyncSession = Depends(session),
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
    summary="Generate seed tests using the judge",
)
async def seed(
    id: UUID, data: SeedCreate, request: Request, db: AsyncSession = Depends(session)
):
    await require(db, Project, id)
    version = await require(db, AppVersion, data.version_id)
    if version.project_id != id:
        raise AppError("project_mismatch", "Version belongs to another project", 422)
    payload = {
        "knowledge_base": version.config.get("knowledge_base", []),
        "system_prompt": version.config.get("system_prompt", ""),
        "sample_inputs": data.sample_inputs,
        "count": data.count,
    }
    answer = await llm.complete(
        request.app.state,
        data.judge_credential_id or data.credential_id,
        [
            {
                "role": "system",
                "content": "TASK:seed Generate test inputs and expected facts from the supplied context. Return JSON {items: [{input: string, expected: object}]}. Do not include credentials or unsupported facts.",
            },
            {"role": "user", "content": json.dumps(payload)},
        ],
        judge=True,
    )
    try:
        generated = json.loads(answer["content"])["items"]
        if not isinstance(generated, list) or not 1 <= len(generated) <= data.count:
            raise ValueError("Invalid number of generated tests")
        tests = [TestCreate.model_validate(item) for item in generated]
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        raise AppError(
            "invalid_seed_response", "Judge returned invalid seed tests", 502
        ) from exc
    rows = []
    for test in tests:
        row = TestCase(
            project_id=id,
            input=test.input,
            expected=test.expected,
            origin="seed",
            split="dev",
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return {"items": [record(row) for row in rows], "next_cursor": None}


@router.post(
    "/projects/{id}/test-cases/split",
    response_model=Page,
    summary="Assign stable dev and holdout splits by test ID",
)
async def split(id: UUID, data: SplitCreate, db: AsyncSession = Depends(session)):
    await require(db, Project, id)
    rows = list(
        (
            await db.scalars(
                select(TestCase).where(TestCase.project_id == id).order_by(TestCase.id)
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
    id: UUID, data: AssertionCreate, db: AsyncSession = Depends(session)
):
    await require(db, TestCase, id)
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
    cursor: UUID | None = None,
    db: AsyncSession = Depends(session),
):
    await require(db, TestCase, id)
    return await page(db, Assertion, (Assertion.test_case_id == id,), limit, cursor)


@router.patch(
    "/assertions/{id}", response_model=entity(Assertion), summary="Update an assertion"
)
async def update_assertion(
    id: UUID, data: AssertionUpdate, db: AsyncSession = Depends(session)
):
    row = await require(db, Assertion, id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise AppError("invalid_assertion", "Assertion fields cannot be null", 422)
        setattr(row, key, value)
    await db.flush()
    return record(row)


@router.delete(
    "/assertions/{id}",
    status_code=204,
    summary="Delete an assertion and its saved evidence",
)
async def remove_assertion(id: UUID, db: AsyncSession = Depends(session)):
    await require(db, Assertion, id)
    await db.execute(delete(AssertionResult).where(AssertionResult.assertion_id == id))
    await db.execute(delete(Assertion).where(Assertion.id == id))
    return Response(status_code=204)
