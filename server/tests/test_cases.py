from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.models import AssertionResult, Run, RunResult, TestCase


async def project_version(client):
    project = await client.post("/v1/projects", json={"name": "Acme Support"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    version = await client.post(
        f"/v1/projects/{project_id}/versions",
        json={"config": {"system_prompt": "Answer policy questions."}},
    )
    assert version.status_code == 201, version.text
    return project_id, version.json()["id"]


async def test_case_assertion_crud_removes_dependent_evidence(api):
    client, app = api
    project, version = await project_version(client)
    created = await client.post(
        f"/v1/projects/{project}/test-cases",
        json={"input": "What is the policy?", "expected": {"reference": "30 days"}},
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    updated = await client.patch(
        f"/v1/test-cases/{case_id}",
        json={"input": "Original input preserved in storage"},
    )
    assert updated.status_code == 200
    assert (await client.get(f"/v1/test-cases/{case_id}")).json()[
        "input"
    ] == "Original input preserved in storage"
    added = await client.post(
        f"/v1/test-cases/{case_id}/assertions",
        json={
            "kind": "deterministic",
            "spec": {"type": "contains", "value": "30 days"},
        },
    )
    assert added.status_code == 201, added.text
    assertion_id = added.json()["id"]
    assert (
        await client.patch(f"/v1/assertions/{assertion_id}", json={"position": 3})
    ).json()["position"] == 3
    listing = await client.get(f"/v1/test-cases/{case_id}/assertions")
    assert listing.json()["items"][0]["id"] == assertion_id
    async with app.state.sessions() as db, db.begin():
        run = Run(app_version_id=UUID(version), params={})
        db.add(run)
        await db.flush()
        result = RunResult(run_id=run.id, test_case_id=UUID(case_id), passed=False)
        db.add(result)
        await db.flush()
        db.add(
            AssertionResult(
                run_result_id=result.id,
                assertion_id=UUID(assertion_id),
                passed=False,
                evidence={"reason": "Failed reference check"},
            )
        )
    assert (await client.delete(f"/v1/assertions/{assertion_id}")).status_code == 204
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AssertionResult)) == 0
        assert await db.scalar(select(func.count()).select_from(RunResult)) == 1
    assert (await client.delete(f"/v1/test-cases/{case_id}")).status_code == 204
    assert (await client.get(f"/v1/test-cases/{case_id}")).status_code == 404
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(RunResult)) == 0


async def test_from_trace_preserves_input_and_rejects_cross_project_source(api):
    client, _ = api
    project, version = await project_version(client)
    other_project, _ = await project_version(client)
    trace_id = str(uuid4())
    trace = await client.post(
        "/v1/traces",
        json={
            "id": trace_id,
            "app_version_id": version,
            "input": "Exact original query with punctuation?",
            "output": "Recorded answer",
            "spans": [],
        },
    )
    assert trace.status_code == 201, trace.text
    created = await client.post(
        f"/v1/projects/{project}/test-cases/from-trace/{trace_id}",
        json={"expected": {"reference": "policy"}},
    )
    assert created.status_code == 201, created.text
    assert created.json()["input"] == "Exact original query with punctuation?"
    assert created.json()["source_trace_id"] == trace_id
    assert created.json()["origin"] == "from_trace"
    assert (
        await client.post(
            f"/v1/projects/{other_project}/test-cases/from-trace/{trace_id}"
        )
    ).status_code == 422
    assert (
        await client.post(
            f"/v1/projects/{project}/test-cases/from-trace/{trace_id}",
            json={"input": "Changed query"},
        )
    ).status_code == 422
    assert (
        await client.post(
            f"/v1/projects/{other_project}/test-cases",
            json={"input": "A query", "source_trace_id": trace_id},
        )
    ).status_code == 422


async def test_seed_and_stable_split(api):
    client, _ = api
    project, version = await project_version(client)
    credential = await client.post(
        "/v1/credentials",
        json={"provider": "custom", "model": "example-model", "api_key": "REPLACE_ME"},
    )
    assert credential.status_code == 201, credential.text
    result = await client.post(
        f"/v1/projects/{project}/test-cases/seed",
        json={
            "version_id": version,
            "credential_id": credential.json()["credential_id"],
            "count": 3,
            "sample_inputs": ["What is the refund policy?"],
        },
    )
    assert result.status_code == 201, result.text
    assert len(result.json()["items"]) == 3
    assert all(row["origin"] == "seed" for row in result.json()["items"])
    first = await client.post(f"/v1/projects/{project}/test-cases/split", json={})
    assert first.status_code == 200, first.text
    first_splits = {row["id"]: row["split"] for row in first.json()["items"]}
    second = await client.post(f"/v1/projects/{project}/test-cases/split", json={})
    assert first_splits == {row["id"]: row["split"] for row in second.json()["items"]}
    listing = await client.get(f"/v1/projects/{project}/test-cases?limit=2")
    assert len(listing.json()["items"]) == 2
    cursor = listing.json()["next_cursor"]
    assert cursor
    last = await client.get(
        f"/v1/projects/{project}/test-cases?limit=2&cursor={cursor}"
    )
    assert len(last.json()["items"]) == 1


async def test_invalid_judge_seed_response_is_not_persisted(api, monkeypatch):
    from app.services import llm

    client, app = api
    project, version = await project_version(client)
    credential = await client.post(
        "/v1/credentials",
        json={"provider": "custom", "model": "example-model", "api_key": "REPLACE_ME"},
    )

    async def malformed(*args, **kwargs):
        return {"content": '{"items":[{"input":""}]}'}

    monkeypatch.setattr(llm, "complete", malformed)
    response = await client.post(
        f"/v1/projects/{project}/test-cases/seed",
        json={
            "version_id": version,
            "credential_id": credential.json()["credential_id"],
        },
    )
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "invalid_seed_response"
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(TestCase)) == 0
