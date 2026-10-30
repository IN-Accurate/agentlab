from statistics import median

from sqlalchemy import select

from app.db import record, require
from app.models import Run, RunResult, TestCase, Span


async def summarize(db, run_id):
    run = await require(db, Run, run_id)
    rows = list(
        await db.execute(
            select(RunResult, TestCase)
            .join(TestCase, TestCase.id == RunResult.test_case_id)
            .where(RunResult.run_id == run.id)
        )
    )

    def counts(selected):
        total = len(selected)
        passed = sum(result.passed for result, _ in selected)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total if total else None,
        }

    latency = list(
        await db.scalars(
            select(Span.latency_ms).where(
                Span.trace_id.in_([result.trace_id for result, _ in rows]),
                Span.type == "llm",
                Span.latency_ms.is_not(None),
            )
        )
    )
    return {
        "run_id": str(run.id),
        "version_id": str(run.app_version_id),
        **counts(rows),
        "dev": counts([(result, case) for result, case in rows if case.split == "dev"]),
        "holdout": counts(
            [(result, case) for result, case in rows if case.split == "holdout"]
        ),
        "latency_p50": median(latency) if latency else None,
        "failures": [record(result) for result, _ in rows if not result.passed],
        "results": [{**record(result), "split": case.split} for result, case in rows],
    }
