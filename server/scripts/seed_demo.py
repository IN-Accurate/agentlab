import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import Settings
from app.db import connect
from app.models import Project, AppVersion, TestCase, Assertion, Trace

NAME = "Demo - Refund Bot"


async def seed(db, reset=False):
    projects = list(await db.scalars(select(Project).where(Project.name == NAME)))
    if reset:
        from app.routers.projects import remove

        for project in projects:
            await remove(project.id, db=db)
    elif projects:
        raise ValueError("Demo already exists; use --reset to replace only its data")
    project = Project(name=NAME)
    db.add(project)
    await db.flush()
    version = AppVersion(
        project_id=project.id,
        config={
            "system_prompt": "Answer from the refund policy.",
            "knowledge_base": [
                {
                    "id": "policy",
                    "text": "Refunds are available within 30 days with proof of purchase.",
                    "source": "refund-policy",
                }
            ],
            "retrieval": {"top_k": 1},
        },
    )
    db.add(version)
    await db.flush()
    trace = Trace(
        app_version_id=version.id,
        input="What is the refund policy?",
        output="Refunds are available within 60 days.",
        status="ok",
    )
    db.add(trace)
    await db.flush()
    case = TestCase(
        project_id=project.id,
        source_trace_id=trace.id,
        origin="from_trace",
        split="dev",
        input=trace.input,
        expected={"reference": "30 days", "source": "refund-policy"},
    )
    db.add(case)
    await db.flush()
    db.add(
        Assertion(
            test_case_id=case.id,
            kind="deterministic",
            position=0,
            spec={"type": "contains", "value": "30 days"},
        )
    )
    holdout = TestCase(
        project_id=project.id,
        origin="user",
        split="holdout",
        input="What proof is required?",
        expected={"reference": "proof of purchase"},
    )
    db.add(holdout)
    await db.flush()
    return {
        "project_id": str(project.id),
        "version_id": str(version.id),
        "trace_id": str(trace.id),
        "test_case_id": str(case.id),
        "note": "Canned API setup only. No model call, verdict, diagnosis or repair was generated.",
    }


async def main(reset=False):
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("Set DATABASE_URL explicitly; this script does not read .env")
    engine, sessions = connect(Settings(_env_file=None))
    try:
        async with sessions() as db, db.begin():
            result = await seed(db, reset)
        print(json.dumps(result, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create canned API bridge records without running an evaluator"
    )
    parser.add_argument("--reset", action="store_true")
    asyncio.run(main(parser.parse_args().reset))
