import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import RetrievedChunk, Span


async def setup_trace(client):
    project = (await client.post("/v1/projects", json={"name": "Acme Support"})).json()
    version = (
        await client.post(f"/v1/projects/{project['id']}/versions", json={"config": {}})
    ).json()
    return {
        "id": str(uuid4()),
        "app_version_id": version["id"],
        "input": "Refund policy?",
        "output": "30 days",
        "spans": [
            {
                "sdk_span_id": "answer",
                "parent_sdk_span_id": "retrieval",
                "type": "llm",
                "latency_ms": 20,
                "started_at": "2026-01-01T00:00:01Z",
            },
            {
                "sdk_span_id": "retrieval",
                "type": "retrieval",
                "latency_ms": 10,
                "started_at": "2026-01-01T00:00:00Z",
                "chunks": [
                    {
                        "rank": 1,
                        "text": "Refunds within 30 days",
                        "source": "policy",
                        "score": 0.9,
                    }
                ],
            },
        ],
    }


async def test_project_scoped_trace_pagination_and_filters(api):
    client, _ = api
    first = await setup_trace(client)
    second = await setup_trace(client)
    version = first["app_version_id"]
    project = (await client.get(f"/v1/versions/{version}")).json()["project_id"]
    expected = []
    for index in range(5):
        # Other projects must not consume places in this project's four-item pages.
        other = {**second, "id": str(uuid4()), "spans": []}
        assert (await client.post("/v1/traces", json=other)).status_code == 201
        row = {
            **first,
            "id": str(uuid4()),
            "spans": [],
            "status": "error" if index == 4 else "ok",
        }
        assert (await client.post("/v1/traces", json=row)).status_code == 201
        expected.append(row["id"])
    params = {"project_id": project, "limit": 4}
    page_one = (await client.get("/v1/traces", params=params)).json()
    assert [row["id"] for row in page_one["items"]] == expected[:4]
    assert page_one["next_cursor"]
    page_two = (
        await client.get(
            "/v1/traces", params={**params, "cursor": page_one["next_cursor"]}
        )
    ).json()
    assert [row["id"] for row in page_two["items"]] == expected[4:]
    assert page_two["next_cursor"] is None
    errors = (
        await client.get("/v1/traces", params={**params, "status": "error"})
    ).json()
    assert [row["id"] for row in errors["items"]] == expected[4:]
    mismatched = (
        await client.get(
            "/v1/traces", params={**params, "version_id": second["app_version_id"]}
        )
    ).json()
    assert mismatched["items"] == []


@pytest.mark.asyncio
async def test_concurrent_idempotent_ingest_and_read_views(api):
    client, app = api
    payload = await setup_trace(client)
    replies = await asyncio.gather(
        *(client.post("/v1/traces", json=payload) for _ in range(4))
    )
    assert [reply.status_code for reply in replies] == [201] * 4
    body = replies[0].json()
    assert len(body["spans"]) == 2
    assert body["span_tree"][0]["sdk_span_id"] == "retrieval"
    assert body["span_tree"][0]["children"][0]["sdk_span_id"] == "answer"
    assert body["span_tree"][0]["chunks"][0]["source"] == "policy"
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Span)) == 2
        assert await db.scalar(select(func.count()).select_from(RetrievedChunk)) == 1
    for suffix in ["", "/spans", "/timeline"]:
        response = await client.get(f"/v1/traces/{payload['id']}{suffix}")
        assert response.status_code == 200, response.text
    timeline = (await client.get(f"/v1/traces/{payload['id']}/timeline")).json()[
        "items"
    ]
    assert [span["latency_ms"] for span in timeline] == [10, 20]
    listing = (
        await client.get(
            "/v1/traces",
            params={"version_id": payload["app_version_id"], "status": "ok"},
        )
    ).json()
    assert [row["id"] for row in listing["items"]] == [payload["id"]]
    assert (await client.get("/v1/traces", params={"status": "error"})).json()[
        "items"
    ] == []


@pytest.mark.asyncio
async def test_missing_parent_rejected_and_chunk_text_redacted(api):
    client, _ = api
    payload = await setup_trace(client)
    secret = "placeholder-sensitive-trace-value"
    credential = await client.post(
        "/v1/credentials",
        json={
            "provider": "custom",
            "base_url": "https://example.com/v1",
            "model": "example-model",
            "api_key": secret,
        },
    )
    assert credential.status_code == 201, credential.text
    payload["spans"][1]["chunks"][0]["text"] = secret
    payload["output"] = secret
    response = await client.post("/v1/traces", json=payload)
    assert response.status_code == 201, response.text
    assert secret not in response.text
    payload["id"] = str(uuid4())
    payload["spans"][0]["parent_sdk_span_id"] = "missing"
    response = await client.post("/v1/traces", json=payload)
    assert response.status_code == 422, response.text


async def test_deepest_allowed_span_chain_round_trips(api):
    client, app = api
    body = await setup_trace(client)
    body["spans"] = [
        {
            "sdk_span_id": f"s{i}",
            "parent_sdk_span_id": f"s{i - 1}" if i else None,
            "type": "llm",
            "latency_ms": 1,
            "started_at": "2026-01-01T00:00:00Z",
            "chunks": [{"rank": 1, "text": "t"}] if i % 2 else [],
        }
        for i in reversed(range(200))
    ]
    created = await client.post("/v1/traces", json=body)
    assert created.status_code == 201, created.text
    read = (await client.get(f"/v1/traces/{body['id']}")).json()
    assert read == created.json() and read["created_at"].endswith("Z")
    depth, node = 1, read["span_tree"][0]
    while node["children"]:
        depth, node = depth + 1, node["children"][0]
    assert depth == 200
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(RetrievedChunk)) == 100
