from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session
from app.errors import AppError
from app.models import Run
from app.schemas import RunResult, RunResultCreate

router = APIRouter(prefix="/v1/mvp", tags=["MVP runs"])

VERDICTS = ("pass", "fail")
FAILURE_TYPES = ("retrieval_miss", "hallucination", "prompt_issue")


def to_result(row):
    return RunResult(
        run_id=row.run_id,
        parent_run_id=row.parent_run_id,
        query=row.query,
        retrieved_chunks=row.retrieved_chunks,
        answer=row.answer,
        grounding_score=row.grounding_score,
        verdict=row.verdict,
        failure_type=row.failure_type,
        diagnosis=row.diagnosis,
        model=row.model,
    )


async def require_run(db, run_id, code="run_not_found", message="Run not found"):
    row = await db.scalar(select(Run).where(Run.run_id == run_id))
    if row is None:
        raise AppError(code, message, 404)
    return row


def scorecard_counts(total, verdict_rows, failure_rows):
    by_verdict = dict.fromkeys(VERDICTS, 0)
    for verdict, count in verdict_rows:
        by_verdict[verdict] = count
    by_failure_type = dict.fromkeys(FAILURE_TYPES, 0)
    for failure, count in failure_rows:
        by_failure_type[failure] = count
    return {
        "total": total,
        "by_verdict": by_verdict,
        "by_failure_type": by_failure_type,
    }


@router.post(
    "/runs", response_model=RunResult, status_code=201, summary="Record an MVP run"
)
async def create_run(
    data: RunResultCreate, db: AsyncSession = Depends(session, scope="function")
):
    if data.parent_run_id is not None:
        await require_run(
            db, data.parent_run_id, "parent_not_found", "Parent run not found"
        )
    row = Run(**data.model_dump())
    db.add(row)
    await db.flush()
    return to_result(row)


@router.get("/runs/{run_id}", response_model=RunResult, summary="Read an MVP run")
async def read_run(run_id: str, db: AsyncSession = Depends(session, scope="function")):
    return to_result(await require_run(db, run_id))


@router.get(
    "/scorecard",
    response_model=dict,
    summary="Group MVP runs by verdict and failure type",
)
async def scorecard(db: AsyncSession = Depends(session, scope="function")):
    total = await db.scalar(
        select(func.count()).select_from(Run).where(Run.run_id.is_not(None))
    )
    verdict_rows = list(
        await db.execute(
            select(Run.verdict, func.count())
            .where(Run.run_id.is_not(None), Run.verdict.is_not(None))
            .group_by(Run.verdict)
        )
    )
    failure_rows = list(
        await db.execute(
            select(Run.failure_type, func.count())
            .where(Run.run_id.is_not(None), Run.failure_type.is_not(None))
            .group_by(Run.failure_type)
        )
    )
    return scorecard_counts(total, verdict_rows, failure_rows)


@router.get(
    "/runs/{run_id}/diff",
    response_model=dict,
    summary="Compare a run against its parent",
)
async def diff_run(run_id: str, db: AsyncSession = Depends(session, scope="function")):
    row = await require_run(db, run_id)
    if row.parent_run_id is None:
        raise AppError("parent_not_found", "Run has no parent to diff against", 404)
    parent = await require_run(
        db, row.parent_run_id, "parent_not_found", "Parent run not found"
    )
    result, baseline = to_result(row), to_result(parent)
    return {
        "run": result.model_dump(mode="json"),
        "parent": baseline.model_dump(mode="json"),
        "verdict_changed": baseline.verdict != result.verdict,
    }
