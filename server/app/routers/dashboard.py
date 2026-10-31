from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session
from app.services import dashboard

router = APIRouter(prefix="/v1/projects", tags=["Dashboard"])


@router.get(
    "/{id}/dashboard",
    response_model=dict,
    summary="Format stored version scores and repair verdicts",
)
async def get(id: UUID, db: AsyncSession = Depends(session, scope="function")):
    return await dashboard.dashboard(db, id)


@router.get(
    "/{id}/dashboard/series",
    response_model=list[dict],
    summary="Read a stored metric over versions",
)
async def series(
    id: UUID,
    metric: str = Query(
        "pass_rate", pattern="^(pass_rate|regressions|latency_p50|failures_by_rule)$"
    ),
    db: AsyncSession = Depends(session, scope="function"),
):
    return await dashboard.series(db, id, metric)
