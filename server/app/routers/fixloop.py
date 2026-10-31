from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session, require, page, record
from app.models import RunResult, Trace, Diagnosis, Replay, Repair, Verification
from app.schemas import (
    DiagnoseRequest,
    ReplayCreate,
    RepairCreate,
    Credentials,
    RunCreate,
    Page,
    JobResponse,
    entity,
)
from app.services.evaluator import prepare, execute
from app.services.results import summarize
from app.routers.jobs import submit

router = APIRouter(prefix="/v1", tags=["Fix loop"])


@router.post(
    "/run-results/{id}/diagnose",
    response_model=entity(Diagnosis),
    summary="Diagnose a failed evaluation result",
)
async def diagnose_result(
    id: UUID,
    data: DiagnoseRequest,
    request: Request,
    db: AsyncSession = Depends(session, scope="function"),
):
    return await execute(
        db,
        request.app.state,
        await prepare(
            db,
            request.app.state,
            "diagnose",
            data,
            target_model=RunResult,
            target_id=id,
        ),
    )


@router.post(
    "/traces/{id}/diagnose",
    response_model=entity(Diagnosis),
    summary="Diagnose a captured trace",
)
async def diagnose_trace(
    id: UUID,
    data: DiagnoseRequest,
    request: Request,
    db: AsyncSession = Depends(session, scope="function"),
):
    return await execute(
        db,
        request.app.state,
        await prepare(
            db, request.app.state, "diagnose", data, target_model=Trace, target_id=id
        ),
    )


@router.get("/diagnoses", response_model=Page, summary="List diagnoses")
async def diagnoses(
    trace_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    db: AsyncSession = Depends(session, scope="function"),
):
    return await page(
        db,
        Diagnosis,
        (Diagnosis.trace_id == trace_id,) if trace_id else (),
        limit,
        cursor,
    )


@router.get(
    "/diagnoses/{id}",
    response_model=entity(Diagnosis),
    summary="Read diagnosis evidence",
)
async def diagnosis(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    return record(await require(db, Diagnosis, id))


@router.post(
    "/diagnoses/{id}/replay",
    response_model=entity(Replay),
    status_code=201,
    summary="Confirm a diagnosis by changing one variable",
)
async def replay(
    id: UUID,
    data: ReplayCreate,
    request: Request,
    db: AsyncSession = Depends(session, scope="function"),
):
    return await execute(
        db,
        request.app.state,
        await prepare(
            db, request.app.state, "replay", data, target_model=Diagnosis, target_id=id
        ),
    )


@router.get(
    "/replays/{id}", response_model=entity(Replay), summary="Read replay outcome"
)
async def get_replay(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    return record(await require(db, Replay, id))


@router.post(
    "/diagnoses/{id}/repairs",
    response_model=entity(Repair),
    status_code=201,
    summary="Create a candidate version from a confirmed diagnosis",
)
async def repair(
    id: UUID,
    data: RepairCreate,
    request: Request,
    db: AsyncSession = Depends(session, scope="function"),
):
    return await execute(
        db,
        request.app.state,
        await prepare(
            db, request.app.state, "repair", data, target_model=Diagnosis, target_id=id
        ),
    )


@router.get(
    "/repairs/{id}",
    response_model=entity(Repair),
    summary="Read a repair and candidate version reference",
)
async def get_repair(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    return record(await require(db, Repair, id))


@router.post(
    "/repairs/{id}/verify",
    response_model=JobResponse,
    status_code=202,
    summary="Verify baseline and candidate without regressions",
)
async def verify(
    id: UUID,
    data: Credentials,
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(session, scope="function"),
):
    prepared = await prepare(
        db, request.app.state, "verify", data, target_model=Repair, target_id=id
    )

    async def operation(job_db, progress):
        return await execute(job_db, request.app.state, prepared, progress)

    return await submit(request.app.state, background, operation)


@router.get(
    "/verifications/{id}",
    response_model=dict,
    summary="Read the verification scorecard",
)
async def verification(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    row = await require(db, Verification, id)
    return {
        **record(row),
        "baseline": await summarize(db, row.baseline_run_id),
        "candidate": await summarize(db, row.candidate_run_id),
    }


@router.post(
    "/projects/{id}/improve",
    response_model=JobResponse,
    status_code=202,
    summary="Run one diagnose replay repair verify cycle",
)
async def improve(
    id: UUID,
    data: RunCreate,
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(session, scope="function"),
):
    prepared = await prepare(db, request.app.state, "improve", data, project_id=id)

    async def operation(job_db, progress):
        return await execute(job_db, request.app.state, prepared, progress)

    return await submit(request.app.state, background, operation)
