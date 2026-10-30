from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session, require, page, record
from app.errors import AppError
from app.models import Project, AppVersion
from app.schemas import ProjectCreate, VersionCreate, Page, entity
from app.security import validate_url

router = APIRouter(prefix="/v1", tags=["Projects and versions"])


@router.post(
    "/projects",
    response_model=entity(Project),
    status_code=201,
    summary="Create a project",
)
async def create(
    data: ProjectCreate, request: Request, db: AsyncSession = Depends(session)
):
    row = Project(name=request.app.state.redactor.text(data.name))
    db.add(row)
    await db.flush()
    return record(row)


@router.get("/projects", response_model=Page, summary="List projects")
async def listing(
    limit: int = Query(50, ge=1, le=200),
    cursor: UUID | None = None,
    db: AsyncSession = Depends(session),
):
    return await page(db, Project, limit=limit, cursor=cursor)


@router.get("/projects/{id}", response_model=entity(Project), summary="Read a project")
async def get(id: UUID, db: AsyncSession = Depends(session)):
    return record(await require(db, Project, id))


@router.patch(
    "/projects/{id}", response_model=entity(Project), summary="Rename a project"
)
async def rename(
    id: UUID, data: ProjectCreate, request: Request, db: AsyncSession = Depends(session)
):
    row = await require(db, Project, id)
    row.name = request.app.state.redactor.text(data.name)
    await db.flush()
    return record(row)


@router.delete(
    "/projects/{id}", status_code=204, summary="Delete a project and dependent records"
)
async def remove(id: UUID, db: AsyncSession = Depends(session)):
    from app.models import (
        Trace,
        Run,
        TestCase,
        RunResult,
        Verification,
        Diagnosis,
        Repair,
    )

    await require(db, Project, id)
    versions = select(AppVersion.id).where(AppVersion.project_id == id)
    traces = select(Trace.id).where(Trace.app_version_id.in_(versions))
    diagnoses = select(Diagnosis.id).where(Diagnosis.trace_id.in_(traces))
    repairs = select(Repair.id).where(Repair.diagnosis_id.in_(diagnoses))
    await db.execute(delete(Verification).where(Verification.repair_id.in_(repairs)))
    await db.execute(delete(Repair).where(Repair.id.in_(repairs)))
    await db.execute(
        delete(RunResult).where(
            RunResult.run_id.in_(select(Run.id).where(Run.app_version_id.in_(versions)))
        )
    )
    await db.execute(delete(Run).where(Run.app_version_id.in_(versions)))
    await db.execute(delete(TestCase).where(TestCase.project_id == id))
    await db.execute(delete(Trace).where(Trace.app_version_id.in_(versions)))
    # Parent references require children to be removed before their ancestors.
    while row := await db.scalar(
        select(AppVersion)
        .where(AppVersion.project_id == id)
        .order_by(AppVersion.created_at.desc())
        .limit(1)
    ):
        await db.execute(delete(AppVersion).where(AppVersion.id == row.id))
    await db.execute(delete(Project).where(Project.id == id))
    return Response(status_code=204)


@router.post(
    "/projects/{id}/versions",
    response_model=entity(AppVersion),
    status_code=201,
    summary="Create the initial immutable version",
)
async def version(
    id: UUID, data: VersionCreate, request: Request, db: AsyncSession = Depends(session)
):
    await require(db, Project, id)
    await db.execute(select(Project).where(Project.id == id).with_for_update())
    if await db.scalar(select(AppVersion.id).where(AppVersion.project_id == id)):
        raise AppError(
            "initial_version_exists", "Use a repair to create a candidate version", 409
        )
    config = (
        data.config.model_dump(exclude_unset=True)
        if hasattr(data.config, "model_dump")
        else data.config
    )
    agent = config.get("agent", {})
    if agent.get("base_url"):
        await validate_url(
            agent["base_url"], request.app.state.settings.testlab_allow_insecure_local
        )
    row = AppVersion(project_id=id, config=request.app.state.redactor.scrub(config))
    db.add(row)
    await db.flush()
    return record(row)


@router.get("/projects/{id}/versions", response_model=Page, summary="List versions")
async def versions(
    id: UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: UUID | None = None,
    db: AsyncSession = Depends(session),
):
    await require(db, Project, id)
    return await page(db, AppVersion, (AppVersion.project_id == id,), limit, cursor)


@router.get(
    "/versions/{id}",
    response_model=entity(AppVersion),
    summary="Read an immutable version",
)
async def read_version(id: UUID, db: AsyncSession = Depends(session)):
    return record(await require(db, AppVersion, id))


@router.get(
    "/versions/{id}/lineage", response_model=Page, summary="Read version ancestors"
)
async def lineage(id: UUID, db: AsyncSession = Depends(session)):
    row = await require(db, AppVersion, id)
    items = []
    while row:
        items.append(record(row))
        row = (
            await db.get(AppVersion, row.parent_version_id)
            if row.parent_version_id
            else None
        )
    return {"items": items, "next_cursor": None}


@router.get(
    "/versions/{a}/diff/{b}",
    response_model=dict,
    summary="Compare two version configurations",
)
async def diff(a: UUID, b: UUID, db: AsyncSession = Depends(session)):
    left, right = await require(db, AppVersion, a), await require(db, AppVersion, b)
    if left.project_id != right.project_id:
        raise AppError("project_mismatch", "Versions belong to different projects", 422)
    return {
        "baseline_version_id": str(a),
        "candidate_version_id": str(b),
        "before": left.config,
        "after": right.config,
    }
