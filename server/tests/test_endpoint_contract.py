from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from fastapi import BackgroundTasks
from pydantic import SecretStr
from sqlalchemy import func, select

from app.errors import AppError
from app.models import AppVersion, Project, TestCase
from app.routers.jobs import get_job, submit


async def test_job_failure_rolls_back_and_redacts_stored_error(api):
    _, app = api
    private = "REPLACE_ME_PRIVATE_VALUE"
    app.state.redactor.register(private)
    tasks = BackgroundTasks()

    async def operation(db, progress):
        db.add(Project(name="Rolled back project"))
        await db.flush()
        await progress(0.5)
        raise AppError(
            "provider_failure", "Provider echoed " + private, 502, {"provider": private}
        )

    accepted = await submit(app.state, tasks, operation)
    assert accepted["status"] == "queued"
    await tasks()
    failed = get_job(app.state, accepted["job_id"])
    assert failed["status"] == "failed"
    assert failed["result"] is None
    assert private not in repr(failed)
    assert failed["error"]["error"]["code"] == "provider_failure"
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Project)) == 0


async def test_version_config_rejects_secret_fields_without_persistence(api):
    client, app = api
    created = await client.post("/v1/projects", json={"name": "Acme Support"})
    project = created.json()["id"]
    for config in [  # realistic-length secrets only
        {"system_prompt": "Bearer sk-f09e8d7c6b5a4321abcd"},
        {"system_prompt": "leaked sk-abcdefghij1234567890 here"},
        {"tools": [{"api_key": "REPLACE_ME"}]},
        {"agent": {"type": "openai_compatible", "api_key": "REPLACE_ME"}},
    ]:
        response = await client.post(
            f"/v1/projects/{project}/versions", json={"config": config}
        )
        assert response.status_code == 422, response.text
        assert "REPLACE_ME" not in response.text
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AppVersion)) == 0


async def test_error_envelope_rate_limit_and_query_key_rejection(api):
    client, app = api
    invalid = await client.get("/v1/projects/invalid-uuid")
    missing = await client.get(f"/v1/projects/{uuid4()}")
    query_key = await client.get("/health?api_key=REPLACE_ME")
    for response, status in [(invalid, 422), (missing, 404), (query_key, 400)]:
        assert response.status_code == status
        assert set(response.json()) == {"error"}
        assert set(response.json()["error"]) == {"code", "message", "details"}
        assert "REPLACE_ME" not in response.text
    app.state.settings.credential_rate_limit = 2
    body = {"provider": "custom", "model": "example-model", "api_key": "REPLACE_ME"}
    assert (await client.post("/v1/credentials", json=body)).status_code == 201
    assert (await client.post("/v1/credentials", json=body)).status_code == 201
    limited = await client.post("/v1/credentials", json=body)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"


async def test_null_updates_are_rejected(api):
    client, _ = api
    project = (await client.post("/v1/projects", json={"name": "Acme Support"})).json()[
        "id"
    ]
    case = (
        await client.post(
            f"/v1/projects/{project}/test-cases", json={"input": "Original query"}
        )
    ).json()["id"]
    assertion = (
        await client.post(
            f"/v1/test-cases/{case}/assertions", json={"kind": "judge", "spec": {}}
        )
    ).json()["id"]
    for field in ("input", "expected", "split"):
        response = await client.patch(f"/v1/test-cases/{case}", json={field: None})
        assert response.status_code == 422, response.text
    for field in ("kind", "spec", "position"):
        response = await client.patch(f"/v1/assertions/{assertion}", json={field: None})
        assert response.status_code == 422, response.text
    assert (await client.get(f"/v1/test-cases/{case}")).json()[
        "input"
    ] == "Original query"


async def test_project_delete_uses_lineage_when_version_timestamps_tie(api):
    client, app = api
    project = (
        await client.post("/v1/projects", json={"name": "Disposable project"})
    ).json()["id"]
    untouched = (
        await client.post("/v1/projects", json={"name": "Unrelated project"})
    ).json()["id"]
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    async with app.state.sessions() as db, db.begin():
        parent = AppVersion(project_id=UUID(project), config={}, created_at=created_at)
        db.add(parent)
        await db.flush()
        child = AppVersion(
            project_id=UUID(project),
            parent_version_id=parent.id,
            config={},
            created_at=created_at,
        )
        db.add(child)
        await db.flush()
        db.add(
            AppVersion(
                project_id=UUID(project),
                parent_version_id=child.id,
                config={},
                created_at=created_at,
            )
        )
    response = await client.delete(f"/v1/projects/{project}")
    assert response.status_code == 204, response.text
    assert (await client.get(f"/v1/projects/{untouched}")).status_code == 200
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AppVersion)) == 0


async def test_version_config_accepts_secret_lookalike_prose(api):
    client, _ = api
    project = (await client.post("/v1/projects", json={"name": "Acme Support"})).json()[
        "id"
    ]
    response = await client.post(
        f"/v1/projects/{project}/versions",
        json={
            "config": {
                "system_prompt": "We use sk-learn. Bearer tokens must be rotated quarterly."
            }
        },
    )
    assert response.status_code == 201, response.text


async def test_api_key_guard(api):
    client, app = api
    app.state.settings.agentlab_api_key = "test-key-123"
    denied = await client.get("/v1/projects")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "unauthorized"
    wrong = await client.get("/v1/projects", headers={"X-Api-Key": "wrong"})
    assert wrong.status_code == 401
    assert (await client.get("/health")).status_code == 200
    allowed = await client.get("/v1/projects", headers={"X-Api-Key": "test-key-123"})
    assert allowed.status_code == 200, allowed.text


async def test_default_credential_keeps_key_on_server(api, monkeypatch):
    async def allow_url(*args):
        return None

    monkeypatch.setattr("app.routers.credentials.validate_url", allow_url)
    client, app = api
    settings = app.state.settings
    status = await client.get("/v1/credentials/default")
    assert status.json() == {"available": False, "base_url": None, "model": None}
    missing = await client.post("/v1/credentials/default")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "default_credential_unavailable"

    key = "server-side-meta-key"
    settings.meta_base_url = "https://example.com/v1"
    settings.meta_model = "example-model"
    settings.meta_api_key = SecretStr("REPLACE_ME")
    assert (await client.get("/v1/credentials/default")).json()["available"] is False

    settings.meta_api_key = SecretStr(key)
    status = await client.get("/v1/credentials/default")
    assert status.json() == {
        "available": True,
        "base_url": "https://example.com/v1",
        "model": "example-model",
    }
    created = await client.post("/v1/credentials/default")
    assert created.status_code == 201
    assert key not in status.text + created.text
    stored = app.state.credential_store.get(created.json()["credential_id"])
    assert stored["base_url"] == "https://example.com/v1"
    assert key not in str(stored)

    settings.credential_rate_limit = 1
    limited = await client.post("/v1/credentials/default")
    assert limited.status_code == 429


def huggingface_client(rows, seen=None):
    features = [{"name": "question"}, {"name": "answer"}]

    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.path == "/splits":
            if request.url.params["dataset"] == "missing/set":
                return httpx.Response(404)
            return httpx.Response(
                200, json={"splits": [{"config": "default", "split": "train"}]}
            )
        offset = int(request.url.params["offset"])
        length = int(request.url.params["length"])
        page = rows[offset : offset + length]
        return httpx.Response(
            200,
            json={
                "features": features,
                "rows": [{"row": row} for row in page],
                "num_rows_total": len(rows),
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_huggingface_preview_and_import(api, monkeypatch):
    client, app = api
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(150)]
    rows[1] = {"question": "", "answer": "empty input"}
    rows[2] = {"question": "q2", "answer": None}
    rows[3] = {"question": "q0", "answer": "a0"}
    seen = []
    monkeypatch.setattr(
        "app.routers.huggingface.safe_http_client",
        lambda settings: huggingface_client(rows, seen),
    )
    project = (await client.post("/v1/projects", json={"name": "Hub"})).json()["id"]
    preview = await client.get("/v1/huggingface/datasets?dataset=owner/name")
    assert preview.status_code == 200
    assert preview.json()["columns"] == ["question", "answer"]
    assert (preview.json()["config"], preview.json()["total"]) == ("default", 150)
    assert all(r.url.host == "datasets-server.huggingface.co" for r in seen)

    body = {
        "dataset": "owner/name",
        "config": "default",
        "split": "train",
        "input_column": "question",
        "expected_column": "answer",
        "limit": 120,
    }
    path = f"/v1/projects/{project}/test-cases/huggingface"
    first = await client.post(path, json=body)
    assert first.status_code == 201
    assert first.json() == {"imported": 117, "skipped": 3}
    again = await client.post(path, json=body)
    assert again.json() == {"imported": 0, "skipped": 120}

    contains = await client.post(
        path, json={**body, "grading": "contains", "limit": 1, "test_split": "holdout"}
    )
    assert contains.json()["imported"] == 1
    async with app.state.sessions() as db:
        cases = list(await db.scalars(select(TestCase)))
    meaning = next(x for x in cases if "behavior" in x.expected)
    assert "a" in meaning.expected["expected_reply"]
    exact = next(x for x in cases if "reference" in x.expected)
    assert (exact.expected["reference"], exact.split) == ("a0", "holdout")

    wrong = await client.post(path, json={**body, "input_column": "nope"})
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "unknown_column"


async def test_huggingface_rejects_missing_and_invalid_datasets(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(
        "app.routers.huggingface.safe_http_client",
        lambda settings: huggingface_client([]),
    )
    missing = await client.get("/v1/huggingface/datasets?dataset=missing/set")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "dataset_not_found"
    bad = await client.get("/v1/huggingface/datasets?dataset=../secret/x/y")
    assert bad.status_code == 422


async def test_default_credential_shares_the_credentials_rate_limit(api):
    client, app = api
    app.state.settings.credential_rate_limit = 1
    app.state.settings.chat_rate_limit = 1000
    body = {"provider": "custom", "model": "example-model", "api_key": "REPLACE_ME"}
    assert (await client.post("/v1/credentials", json=body)).status_code == 201
    limited = await client.post("/v1/credentials/default")
    assert limited.status_code == 429


async def test_huggingface_rejects_oversized_responses(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr("app.routers.huggingface.MAX_RESPONSE_BYTES", 10)
    monkeypatch.setattr(
        "app.routers.huggingface.safe_http_client",
        lambda settings: huggingface_client([]),
    )
    response = await client.get("/v1/huggingface/datasets?dataset=owner/name")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "huggingface_too_large"


async def test_huggingface_skips_oversized_replies(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr("app.routers.huggingface.MAX_INPUT", 5)
    rows = [{"question": "q", "answer": "too long"}, {"question": "r", "answer": "ok"}]
    monkeypatch.setattr(
        "app.routers.huggingface.safe_http_client",
        lambda settings: huggingface_client(rows),
    )
    project = (await client.post("/v1/projects", json={"name": "Hub"})).json()["id"]
    body = {
        "dataset": "owner/name",
        "config": "default",
        "split": "train",
        "input_column": "question",
        "expected_column": "answer",
    }
    response = await client.post(
        f"/v1/projects/{project}/test-cases/huggingface", json=body
    )
    assert response.json() == {"imported": 1, "skipped": 1}
