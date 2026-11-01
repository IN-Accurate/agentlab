from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.models import Assertion, AssertionResult, Run, RunResult, TestCase


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


async def test_case_assertion_crud_archives_without_rewriting_history(api):
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
        assert await db.scalar(select(func.count()).select_from(AssertionResult)) == 1
        assert await db.scalar(select(func.count()).select_from(RunResult)) == 1
        archived = await db.get(Assertion, UUID(assertion_id))
        assert archived.archived_at is not None
    assert (await client.get(f"/v1/test-cases/{case_id}/assertions")).json()[
        "items"
    ] == []
    assert (
        await client.patch(f"/v1/assertions/{assertion_id}", json={"position": 5})
    ).status_code == 404
    assert (await client.delete(f"/v1/test-cases/{case_id}")).status_code == 204
    assert (await client.get(f"/v1/test-cases/{case_id}")).status_code == 404
    assert (await client.get(f"/v1/projects/{project}/test-cases")).json()[
        "items"
    ] == []
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(RunResult)) == 1
        archived = await db.get(TestCase, UUID(case_id))
        assert archived.archived_at is not None


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


async def test_from_trace_input_cannot_be_updated(api):
    client, _ = api
    project, version = await project_version(client)
    trace_id = str(uuid4())
    trace = await client.post(
        "/v1/traces",
        json={
            "id": trace_id,
            "app_version_id": version,
            "input": "Exact original query",
            "output": "Recorded answer",
            "spans": [],
        },
    )
    assert trace.status_code == 201, trace.text
    case_id = (
        await client.post(
            f"/v1/projects/{project}/test-cases/from-trace/{trace_id}", json={}
        )
    ).json()["id"]
    forbidden = await client.patch(
        f"/v1/test-cases/{case_id}", json={"input": "Changed query"}
    )
    assert forbidden.status_code == 422, forbidden.text
    assert forbidden.json()["error"]["code"] == "trace_input_mismatch"
    assert (
        await client.patch(
            f"/v1/test-cases/{case_id}",
            json={"expected": {"reference": "policy"}},
        )
    ).status_code == 200
    assert (await client.get(f"/v1/test-cases/{case_id}")).json()[
        "input"
    ] == "Exact original query"


async def test_stable_split(api):
    client, _ = api
    project, _ = await project_version(client)
    for i in range(8):
        assert (
            await client.post(
                f"/v1/projects/{project}/test-cases", json={"input": f"Question {i}"}
            )
        ).status_code == 201
    first = (
        await client.post(f"/v1/projects/{project}/test-cases/split", json={})
    ).json()
    second = (
        await client.post(f"/v1/projects/{project}/test-cases/split", json={})
    ).json()
    assert first == second


async def test_tested_cases_keep_their_split_for_saved_summaries(api):
    client, app = api
    project, version = await project_version(client)
    ids = [
        (
            await client.post(
                f"/v1/projects/{project}/test-cases",
                json={"input": f"Question {i}", "split": "holdout"},
            )
        ).json()["id"]
        for i in range(12)
    ]
    tested, untested = ids[0], ids[1]
    async with app.state.sessions() as db, db.begin():
        run = Run(app_version_id=UUID(version), params={})
        db.add(run)
        await db.flush()
        db.add(RunResult(run_id=run.id, test_case_id=UUID(tested), passed=True))
    locked = await client.patch(f"/v1/test-cases/{tested}", json={"split": "dev"})
    assert (
        locked.status_code == 409 and locked.json()["error"]["code"] == "split_locked"
    )
    same = await client.patch(f"/v1/test-cases/{tested}", json={"split": "holdout"})
    assert same.status_code == 200
    moved = await client.patch(f"/v1/test-cases/{untested}", json={"split": "dev"})
    assert moved.status_code == 200
    items = (
        await client.post(f"/v1/projects/{project}/test-cases/split", json={})
    ).json()["items"]
    splits = {item["id"]: item["split"] for item in items}
    assert splits[tested] == "holdout" and "dev" in splits.values()
    summary = (await client.get(f"/v1/runs/{run.id}")).status_code
    assert summary == 200
