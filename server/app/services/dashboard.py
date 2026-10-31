from collections import defaultdict

from sqlalchemy import select

from app.db import record, require
from app.errors import AppError
from app.models import (
    Project,
    AppVersion,
    Run,
    RunResult,
    TestCase,
    Trace,
    Diagnosis,
    Repair,
    Verification,
    Span,
)
from app.services.results import format_summary


async def dashboard(db, project_id):
    await require(db, Project, project_id)
    versions = list(
        await db.scalars(
            select(AppVersion)
            .where(AppVersion.project_id == project_id)
            .order_by(AppVersion.created_at, AppVersion.id)
        )
    )
    if not versions:
        return {"project_id": str(project_id), "versions": []}
    version_ids = [version.id for version in versions]
    latest = list(
        await db.scalars(
            select(Run)
            .where(Run.app_version_id.in_(version_ids))
            .distinct(Run.app_version_id)
            .order_by(Run.app_version_id, Run.created_at.desc(), Run.id.desc())
        )
    )
    run_by_version = {run.app_version_id: run for run in latest}
    rows = (
        list(
            await db.execute(
                select(RunResult, TestCase)
                .join(TestCase, TestCase.id == RunResult.test_case_id)
                .where(RunResult.run_id.in_([run.id for run in latest]))
            )
        )
        if latest
        else []
    )
    results_by_run = defaultdict(list)
    run_by_trace = defaultdict(list)
    for result, case in rows:
        results_by_run[result.run_id].append((result, case))
        if result.trace_id is not None:
            run_by_trace[result.trace_id].append(result.run_id)
    latency_by_run = defaultdict(list)
    if run_by_trace:
        for trace_id, latency_ms in await db.execute(
            select(Span.trace_id, Span.latency_ms).where(
                Span.trace_id.in_(list(run_by_trace)),
                Span.type == "llm",
                Span.latency_ms.is_not(None),
            )
        ):
            for run_id in run_by_trace[trace_id]:
                latency_by_run[run_id].append(latency_ms)
    failures_by_version = defaultdict(dict)
    for rule, version_id in await db.execute(
        select(Diagnosis.rule, Trace.app_version_id)
        .join(Trace, Trace.id == Diagnosis.trace_id)
        .where(Trace.app_version_id.in_(version_ids))
    ):
        counts = failures_by_version[version_id]
        counts[rule] = counts.get(rule, 0) + 1
    repairs = list(
        await db.scalars(select(Repair).where(Repair.new_version_id.in_(version_ids)))
    )
    repairs_by_version = defaultdict(list)
    for repair in repairs:
        repairs_by_version[repair.new_version_id].append(repair.id)
    verifications_by_repair = defaultdict(list)
    if repairs:
        for verification in await db.scalars(
            select(Verification).where(
                Verification.repair_id.in_([repair.id for repair in repairs])
            )
        ):
            verifications_by_repair[verification.repair_id].append(verification)
    items = []
    for version in versions:
        run = run_by_version.get(version.id)
        summary = (
            format_summary(run, results_by_run[run.id], latency_by_run[run.id])
            if run
            else None
        )
        verifications = [
            verification
            for repair_id in repairs_by_version.get(version.id, [])
            for verification in verifications_by_repair.get(repair_id, [])
        ]
        items.append(
            {
                "version_id": str(version.id),
                "label": f"v{len(items) + 1}",
                "created_at": record(version)["created_at"],
                "summary": summary,
                "pass_rate": summary["pass_rate"] if summary else None,
                "dev_pass_rate": summary["dev"]["pass_rate"] if summary else None,
                "holdout_pass_rate": summary["holdout"]["pass_rate"]
                if summary
                else None,
                "failures_by_rule": failures_by_version.get(version.id, {}),
                "accepted_repairs": sum(v.accepted for v in verifications),
                "rejected_repairs": sum(not v.accepted for v in verifications),
                "regressions": sum(v.regressions_count for v in verifications),
                "latency_p50": summary["latency_p50"] if summary else None,
            }
        )
    return {"project_id": str(project_id), "versions": items}


async def series(db, project_id, metric):
    if metric not in {"pass_rate", "regressions", "latency_p50", "failures_by_rule"}:
        raise AppError("invalid_metric", "Unknown dashboard metric", 422)
    data = await dashboard(db, project_id)
    return [
        {
            "version_id": v["version_id"],
            "label": v["label"],
            "created_at": v["created_at"],
            "value": v[metric],
        }
        for v in data["versions"]
    ]
