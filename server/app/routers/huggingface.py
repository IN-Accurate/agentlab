import json
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import require, session
from app.errors import AppError
from app.models import Project, TestCase
from app.schemas import HuggingFaceImport
from app.security import safe_http_client

router = APIRouter(prefix="/v1", tags=["Hugging Face"])

HF_URL = "https://datasets-server.huggingface.co"
HF_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 20_000_000
PAGE_SIZE = 100
PREVIEW_ROWS = 3
MAX_INPUT = 32000
DATASET_PATTERN = r"^[\w.-]+(/[\w.-]+)?$"


async def fetch(client, path, params):
    try:
        async with client.stream(
            "GET", f"{HF_URL}/{path}", params=params, timeout=HF_TIMEOUT
        ) as response:
            if response.status_code in {401, 403, 404}:
                raise AppError(
                    "dataset_not_found",
                    "Dataset not found, private, or not available in the viewer",
                    404,
                )
            if response.status_code != 200:
                raise AppError(
                    "huggingface_unavailable", "Hugging Face could not read it", 502
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_RESPONSE_BYTES:
                    raise AppError(
                        "huggingface_too_large",
                        "Hugging Face response is too large",
                        502,
                    )
        return json.loads(body)
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            "huggingface_unavailable", "Could not reach Hugging Face", 502
        ) from exc


async def choose_split(client, dataset, config, split):
    found = await fetch(client, "splits", {"dataset": dataset})
    options = [
        {"config": item["config"], "split": item["split"]}
        for item in found.get("splits") or []
    ]
    if not options:
        raise AppError("dataset_not_found", "Dataset has no readable splits", 404)
    wanted = {"config": config, "split": split}
    if config and split and wanted not in options:
        raise AppError("unknown_split", "Dataset has no such subset and split", 422)
    return options, (wanted if config and split else options[0])


async def read_rows(client, dataset, chosen, offset, length):
    return await fetch(
        client,
        "rows",
        {"dataset": dataset, **chosen, "offset": offset, "length": length},
    )


def cell(value):
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.replace("\x00", "").strip()


def expected_for(data, reply):
    expected = {"dataset": data.dataset, "expected_reply": reply}
    if data.grading == "contains":
        expected["reference"] = reply
    else:
        expected["behavior"] = f"Gives a reply equivalent in meaning to: {reply}"
    return expected


def fingerprint(text, expected):
    return text, json.dumps(expected, sort_keys=True)


@router.get(
    "/huggingface/datasets",
    response_model=dict,
    summary="Preview a public Hugging Face dataset",
)
async def preview(
    request: Request,
    dataset: str = Query(min_length=1, max_length=200, pattern=DATASET_PATTERN),
    config: str | None = Query(None, max_length=200),
    split: str | None = Query(None, max_length=200),
):
    async with safe_http_client(request.app.state.settings) as client:
        options, chosen = await choose_split(client, dataset, config, split)
        page = await read_rows(client, dataset, chosen, 0, PREVIEW_ROWS)
    return {
        "dataset": dataset,
        "options": options,
        **chosen,
        "columns": [item["name"] for item in page.get("features") or []],
        "rows": [item["row"] for item in page.get("rows") or []],
        "total": page.get("num_rows_total"),
    }


@router.post(
    "/projects/{id}/test-cases/huggingface",
    response_model=dict,
    status_code=201,
    summary="Create test cases from a public Hugging Face dataset",
)
async def import_rows(
    id: UUID,
    data: HuggingFaceImport,
    request: Request,
    db: AsyncSession = Depends(session, scope="function"),
):
    await require(db, Project, id)
    rows, columns = [], []
    chosen = {"config": data.config, "split": data.split}
    async with safe_http_client(request.app.state.settings) as client:
        while len(rows) < data.limit:
            length = min(PAGE_SIZE, data.limit - len(rows))
            page = await read_rows(
                client, data.dataset, chosen, data.offset + len(rows), length
            )
            columns = [item["name"] for item in page.get("features") or []]
            items = [item["row"] for item in page.get("rows") or []]
            rows += items
            if len(items) < length:
                break
    if rows and {data.input_column, data.expected_column} - set(columns):
        raise AppError("unknown_column", "Dataset has no such column", 422)
    # Serializes imports per project so two runs cannot add the same rows.
    await db.execute(select(Project.id).where(Project.id == id).with_for_update())
    existing = await db.execute(
        select(TestCase.input, TestCase.expected).where(
            TestCase.project_id == id, TestCase.archived_at.is_(None)
        )
    )
    seen = {fingerprint(text, expected) for text, expected in existing}
    imported, skipped = 0, 0
    for row in rows:
        text = cell(row.get(data.input_column))[:MAX_INPUT]
        reply = cell(row.get(data.expected_column))
        expected = expected_for(data, reply)
        mark = fingerprint(text, expected)
        if not text or not reply or len(reply) > MAX_INPUT or mark in seen:
            skipped += 1
            continue
        seen.add(mark)
        db.add(
            TestCase(
                project_id=id,
                origin="user",
                split=data.test_split,
                input=text,
                expected=expected,
            )
        )
        imported += 1
    await db.flush()
    return {"imported": imported, "skipped": skipped}
