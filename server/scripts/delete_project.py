import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add server directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import Settings
from app.db import connect
from app.models import Project
from app.routers.projects import remove


async def delete_project(db, name):
    projects = list(await db.scalars(select(Project).where(Project.name == name)))
    if not projects:
        print(f"No project found with name '{name}'")
        return False
    for project in projects:
        print(f"Deleting project '{project.name}' ({project.id})...")
        await remove(project.id, db=db)
        print(f"Deleted project '{project.name}' successfully.")
    return True


async def main():
    parser = argparse.ArgumentParser(description="Delete a project by name")
    parser.add_argument("name", nargs="?", default="muse-spark-1.3", help="Project name to delete")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        # Try reading from .env if present
        env_file = Path(__file__).resolve().parents[2] / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not os.environ.get("DATABASE_URL"):
        print("Set DATABASE_URL explicitly or in .env to delete from database directly.")
        return

    settings = Settings(_env_file=None)
    engine, sessions = connect(settings)
    try:
        async with sessions() as db, db.begin():
            await delete_project(db, args.name)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

