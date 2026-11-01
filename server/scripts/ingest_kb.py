"""Load .md / .txt files into a project's Neon knowledge base.

    DATABASE_URL=<direct url> EMBEDDING_API_KEY=... EMBEDDING_MODEL=text-embedding-3-small \
    EMBEDDING_BASE_URL=https://openrouter.ai/api/v1 \
    python server/scripts/ingest_kb.py --project <project-uuid> --dir ./kb

Each file becomes one document; its stem is the `source` that test cases refer to
(expected.source). Re-running is safe: unchanged files are skipped, edited ones replaced.
Like seed_demo.py this never reads .env, so you always know which database you hit.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.config import Settings
from app.db import connect
from app.models import Project
from evaluator.rag.embeddings import EmbeddingClient
from evaluator.rag.store import ingest

REQUIRED = ("DATABASE_URL", "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL")


def load(directory):
    files = sorted(p for p in Path(directory).rglob("*") if p.suffix in {".md", ".txt"})
    return [
        {"source": p.stem, "title": p.stem.replace("-", " "), "text": p.read_text(encoding="utf-8")}
        for p in files
    ]


async def main(project_id, directory, max_chars, overlap):
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)}")
    documents = load(directory)
    if not documents:
        raise SystemExit(f"No .md or .txt files under {directory}")
    engine, sessions = connect(Settings(_env_file=None))
    try:
        async with httpx.AsyncClient(timeout=120) as http:
            embedder = EmbeddingClient(
                http,
                os.environ["EMBEDDING_API_KEY"],
                os.environ["EMBEDDING_BASE_URL"],
                os.environ["EMBEDDING_MODEL"],
            )
            async with sessions() as db, db.begin():
                if await db.get(Project, project_id) is None:
                    raise SystemExit(f"Project {project_id} not found")
                print(json.dumps(await ingest(db, embedder, project_id, documents, max_chars, overlap), indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=UUID, required=True)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--max-chars", type=int, default=800)
    parser.add_argument("--overlap", type=int, default=120)
    args = parser.parse_args()
    asyncio.run(main(args.project, args.dir, args.max_chars, args.overlap))
