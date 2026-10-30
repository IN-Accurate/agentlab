from sqlalchemy import select

from app.db import record, require
from app.errors import AppError
from app.models import Project, AppVersion, Run, Trace, Diagnosis, Repair, Verification
from app.services.results import summarize


async def dashboard(db, project_id):
    await require(db, Project, project_id)
    versions = list(
        await db.scalars(
            select(AppVersion)
            .where(AppVersion.project_id == project_id)
            .order_by(AppVersion.created_at, AppVersion.id)
        )
    )
    items = []
    for version in versions:
        run = await db.scalar(
            select(Run)
            .where(Run.app_version_id == version.id)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(1)
        )
        summary = await summarize(db, run.id) if run else None
        diagnoses = list(
            await db.scalars(
                select(Diagnosis)
                .join(Trace, Trace.id == Diagnosis.trace_id)
                .where(Trace.app_version_id == version.id)
            )
        )
        counts = {}
        for diagnosis in diagnoses:
            counts[diagnosis.rule] = counts.get(diagnosis.rule, 0) + 1
        repairs = list(
            await db.scalars(select(Repair).where(Repair.new_version_id == version.id))
        )
        verifications = (
            list(
                await db.scalars(
                    select(Verification).where(
                        Verification.repair_id.in_([r.id for r in repairs])
                    )
                )
            )
            if repairs
            else []
        )
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
                "failures_by_rule": counts,
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
