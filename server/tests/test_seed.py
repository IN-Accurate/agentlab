import importlib.util
from pathlib import Path

from sqlalchemy import select, func
from app.models import Project, Trace


async def test_seed_reset_preserves_other_projects(api):
    _, app = api
    spec = importlib.util.spec_from_file_location(
        "seed_demo", Path(__file__).parents[1] / "scripts/seed_demo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async with app.state.sessions() as db, db.begin():
        db.add(Project(name="Unrelated project"))
        first = await module.seed(db)
        assert first["note"].startswith("Canned API setup")
        second = await module.seed(db, reset=True)
        assert first["project_id"] != second["project_id"]
        assert await db.scalar(select(func.count()).select_from(Project)) == 2
        assert await db.scalar(select(func.count()).select_from(Trace)) == 1
