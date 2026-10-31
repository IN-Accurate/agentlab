from uuid import UUID

from sqlalchemy import select, func

from app.errors import AppError
from app.models import AppVersion, Run


async def setup(client):
    p = (await client.post("/v1/projects", json={"name": "Acme Support"})).json()["id"]
    v = (
        await client.post(
            f"/v1/projects/{p}/versions",
            json={"config": {"system_prompt": "Keep these instructions"}},
        )
    ).json()["id"]
    c = (
        await client.post(
            "/v1/credentials",
            json={
                "provider": "custom",
                "model": "example-model",
                "api_key": "REPLACE_ME",
            },
        )
    ).json()["credential_id"]
    t = (
        await client.post(
            f"/v1/projects/{p}/test-cases",
            json={
                "input": "Original query",
                "expected": {"reference": "Original expectation"},
            },
        )
    ).json()["id"]
    await client.post(
        f"/v1/test-cases/{t}/assertions",
        json={"kind": "judge", "spec": {"opaque": "Unmodified rubric"}},
    )
    return p, v, c, t


async def test_missing_evaluator_returns_503(api):
    client, _ = api
    p, v, c, t = await setup(client)
    r = await client.post(
        f"/v1/projects/{p}/runs", json={"version_id": v, "credential_id": c}
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "evaluator_not_configured"
    for path in [f"/v1/projects/{p}/improve", f"/v1/projects/{p}/test-cases/seed"]:
        assert (
            await client.post(path, json={"version_id": v, "credential_id": c})
        ).status_code == 503


async def test_evaluator_receives_preprocessed_inputs_and_scoped_credentials(api):
    client, app = api
    p, v, c, t = await setup(client)

    class TransportFixture:
        async def execute(self, payload, context):
            assert payload["operation"] == "run"
            assert (
                payload["version"]["config"]["system_prompt"]
                == "Keep these instructions"
            )
            assert payload["test_cases"][0]["input"] == "Original query"
            assert payload["test_cases"][0]["assertions"][0]["spec"] == {
                "opaque": "Unmodified rubric"
            }
            assert context["credentials"]["decrypt_for_call"](c) == "REPLACE_ME"
            try:
                context["credentials"]["decrypt_for_call"]("unrelated")
            except AppError as e:
                assert e.status == 403
            else:
                raise AssertionError("Unrelated credential was allowed")
            await context["report_progress"](0.5)
            row = Run(app_version_id=UUID(v), params={"transport_fixture": True})
            context["session"].add(row)
            await context["session"].flush()
            return {"run_id": str(row.id), "evaluated_cases": 0}

    app.state.evaluator = TransportFixture()
    r = await client.post(
        f"/v1/projects/{p}/runs", json={"version_id": v, "credential_id": c}
    )
    assert r.status_code == 202, r.text
    job = (await client.get("/v1/jobs/" + r.json()["job_id"])).json()
    assert job["status"] == "completed", job
    assert job["result"]["evaluated_cases"] == 0
    assert "event: done" in (await client.get(f"/v1/jobs/{job['job_id']}/events")).text


async def test_invalid_backend_reference_rolls_back(api):
    client, app = api
    p, v, c, t = await setup(client)

    class InvalidFixture:
        async def execute(self, payload, context):
            context["session"].add(Run(app_version_id=UUID(v), params={}))
            return {"run_id": "invalid"}

    app.state.evaluator = InvalidFixture()
    response = await client.post(
        f"/v1/projects/{p}/runs", json={"version_id": v, "credential_id": c}
    )
    job = (await client.get("/v1/jobs/" + response.json()["job_id"])).json()
    assert job["status"] == "failed"
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Run)) == 0


async def test_run_referencing_another_version_is_rejected(api):
    client, app = api
    p, v, c, t = await setup(client)
    async with app.state.sessions() as db, db.begin():
        other = AppVersion(project_id=UUID(p), config={})
        db.add(other)
        await db.flush()
        other_id = str(other.id)

    class WrongVersionFixture:
        async def execute(self, payload, context):
            row = Run(app_version_id=UUID(other_id), params={})
            context["session"].add(row)
            await context["session"].flush()
            return {"run_id": str(row.id)}

    app.state.evaluator = WrongVersionFixture()
    response = await client.post(
        f"/v1/projects/{p}/runs", json={"version_id": v, "credential_id": c}
    )
    job = (await client.get("/v1/jobs/" + response.json()["job_id"])).json()
    assert job["status"] == "failed"
    assert job["error"]["error"]["code"] == "invalid_evaluator_response"


async def test_evaluator_can_commit_per_batch_with_session_factory(api):
    client, app = api
    p, v, c, t = await setup(client)
    seen = {}

    class BatchFixture:
        async def execute(self, payload, context):
            assert "session_factory" in context
            assert context["http_client"] is not None
            async with context["session_factory"]() as batch_db:
                row = Run(app_version_id=UUID(v), params={"batched": True})
                batch_db.add(row)
                await batch_db.commit()
                seen["run_id"] = str(row.id)
            return {"run_id": seen["run_id"]}

    app.state.evaluator = BatchFixture()
    response = await client.post(
        f"/v1/projects/{p}/runs", json={"version_id": v, "credential_id": c}
    )
    job = (await client.get("/v1/jobs/" + response.json()["job_id"])).json()
    assert job["status"] == "completed", job
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Run)) == 1


async def test_legacy_run_read_accepts_stored_list_chunks(api):
    client, app = api
    _, v, _, _ = await setup(client)
    async with app.state.sessions() as db, db.begin():
        row = Run(
            app_version_id=UUID(v),
            params={},
            retrieved_chunks=[{"id": "c1", "text": "t", "score": 0.5}],
        )
        db.add(row)
        await db.flush()
        run_id = str(row.id)
    response = await client.get(f"/v1/runs/{run_id}")
    assert response.status_code == 200, response.text
    assert response.json()["retrieved_chunks"][0]["id"] == "c1"
