from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session, require, record, page
from app.errors import AppError
from app.models import Project, AppVersion, TestCase, Run, RunResult, AssertionResult
from app.schemas import RunCreate, Page, JobResponse, entity
from app.security import credential_record
from app.routers.jobs import submit
from app.services.assertions import run_evaluation, summarize

router = APIRouter(prefix="/v1", tags=["Runs"])


async def selected(db, project_id, data):
    await require(db, Project, project_id)
    version = await require(db, AppVersion, data.version_id)
    if version.project_id != project_id:
        raise AppError("project_mismatch", "Version belongs to another project", 422)
    query = (
        select(TestCase).where(TestCase.project_id == project_id).order_by(TestCase.id)
    )
    if data.split:
        query = query.where(TestCase.split == data.split)
    if data.test_case_ids is not None:
        query = query.where(TestCase.id.in_(data.test_case_ids))
    tests = list(await db.scalars(query))
    if data.test_case_ids is not None and {x.id for x in tests} != set(
        data.test_case_ids
    ):
        raise AppError(
            "test_mismatch", "Selected tests do not match project and split", 422
        )
    if not tests:
        raise AppError("no_tests", "Add test cases before evaluating", 422)
    return version, tests


@router.post(
    "/projects/{id}/runs",
    response_model=JobResponse,
    status_code=202,
    summary="Evaluate an agent version in a background job",
)
async def run(
    id: UUID,
    data: RunCreate,
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(session),
):
    await selected(db, id, data)
    credential_record(str(data.credential_id), request.app.state.credential_store)
    if data.judge_credential_id:
        credential_record(
            str(data.judge_credential_id), request.app.state.credential_store
        )

    async def operation(job_db, progress):
        version, tests = await selected(job_db, id, data)
        result = await run_evaluation(
            job_db,
            request.app.state,
            version,
            tests,
            str(data.credential_id),
            data.params.model_dump(exclude_none=True),
            str(data.judge_credential_id) if data.judge_credential_id else None,
            progress,
        )
        return {"run_id": str(result.id), "summary": await summarize(job_db, result.id)}

    return await submit(request.app.state, background, operation)


@router.get("/runs", response_model=Page, summary="List evaluation runs")
async def runs(
    version_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: UUID | None = None,
    db: AsyncSession = Depends(session),
):
    return await page(
        db,
        Run,
        (Run.app_version_id == version_id,) if version_id else (),
        limit,
        cursor,
    )


@router.get("/runs/{id}", response_model=entity(Run), summary="Read a run")
async def get(id: UUID, db: AsyncSession = Depends(session)):
    return record(await require(db, Run, id))


@router.get("/runs/{id}/results", response_model=Page, summary="List per-test results")
async def results(
    id: UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: UUID | None = None,
    db: AsyncSession = Depends(session),
):
    await require(db, Run, id)
    return await page(db, RunResult, (RunResult.run_id == id,), limit, cursor)


@router.get(
    "/run-results/{id}",
    response_model=dict,
    summary="Read a result and assertion evidence",
)
async def result(id: UUID, db: AsyncSession = Depends(session)):
    row = await require(db, RunResult, id)
    assertions = list(
        await db.scalars(
            select(AssertionResult).where(AssertionResult.run_result_id == id)
        )
    )
    evidence = []
    for item in assertions:
        value = record(item)
        if value["evidence"].get("status") == "skipped":
            value["passed"] = None
        evidence.append(value)
    return {**record(row), "assertion_results": evidence}


@router.get(
    "/runs/{id}/summary",
    response_model=dict,
    summary="Summarize overall dev and holdout scores",
)
async def summary(id: UUID, db: AsyncSession = Depends(session)):
    await require(db, Run, id)
    return await summarize(db, id)


@router.get(
    "/runs/{a}/compare/{b}",
    response_model=dict,
    summary="Compare two runs from one project",
)
async def compare(a: UUID, b: UUID, db: AsyncSession = Depends(session)):
    left, right = await require(db, Run, a), await require(db, Run, b)
    lv, rv = (
        await require(db, AppVersion, left.app_version_id),
        await require(db, AppVersion, right.app_version_id),
    )
    if lv.project_id != rv.project_id:
        raise AppError("project_mismatch", "Runs belong to different projects", 422)
    return {"baseline": await summarize(db, a), "candidate": await summarize(db, b)}
