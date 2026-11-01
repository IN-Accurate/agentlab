import pytest
from pydantic import ValidationError

from app.models import Run
from app.routers.mvp import scorecard_counts, to_result
from app.schemas import RunResult, RunResultCreate


def demo_payload(**overrides):
    payload = {
        "run_id": "demo-run-1",
        "query": "What is the refund policy?",
        "retrieved_chunks": [
            {"id": "c1", "text": "Refunds within 30 days.", "score": 0.9}
        ],
        "answer": "Refunds are available within 60 days.",
        "grounding_score": 0.2,
        "verdict": "fail",
        "failure_type": "retrieval_miss",
        "diagnosis": "Retrieved chunk was stale; re-index policy docs.",
        "model": "muse-spark",
    }
    payload.update(overrides)
    return payload


def test_mvp_result_validation_mirrors_table_checks():
    assert RunResult(**demo_payload()).run_id == "demo-run-1"
    minimal = RunResult(
        run_id="r2",
        query="q",
        retrieved_chunks=[],
        answer="a",
        grounding_score=1.0,
        verdict="pass",
        model="m",
    )
    assert minimal.parent_run_id is None
    assert minimal.failure_type is None
    assert minimal.diagnosis is None
    for bad in [
        {"verdict": "maybe"},
        {"grounding_score": 1.5},
        {"grounding_score": -0.1},
        {"failure_type": "unknown_bug"},
        {"run_id": ""},
    ]:
        with pytest.raises(ValidationError):
            RunResult(**demo_payload(**bad))


def test_to_result_mapping():
    row = Run(**demo_payload(run_id="r9"))
    result = to_result(row)
    assert result.run_id == "r9"
    assert result.retrieved_chunks[0]["score"] == 0.9
    assert result.grounding_score == 0.2


def test_scorecard_counts_merge():
    merged = scorecard_counts(3, [("pass", 1), ("fail", 2)], [("retrieval_miss", 2)])
    assert merged == {
        "total": 3,
        "by_verdict": {"pass": 1, "fail": 2},
        "by_failure_type": {
            "retrieval_miss": 2,
            "hallucination": 0,
            "prompt_issue": 0,
        },
    }


async def test_mvp_run_crud(api):
    client, _ = api
    created = await client.post("/v1/mvp/runs", json=demo_payload())
    assert created.status_code == 201, created.text
    assert created.json()["run_id"] == "demo-run-1"
    assert (await client.get("/v1/mvp/runs/demo-run-1")).json()["verdict"] == "fail"
    assert (await client.get("/v1/mvp/runs/missing")).status_code == 404
    assert (await client.post("/v1/mvp/runs", json=demo_payload())).status_code == 409
    assert (
        await client.post(
            "/v1/mvp/runs", json=demo_payload(run_id="r2", parent_run_id="missing")
        )
    ).status_code == 404
    assert (
        await client.post(
            "/v1/mvp/runs", json=demo_payload(run_id="r3", verdict="maybe")
        )
    ).status_code == 422
    assert (
        await client.post("/v1/mvp/runs", json=demo_payload(run_id=""))
    ).status_code == 422


async def test_mvp_scorecard(api):
    client, _ = api
    await client.post("/v1/mvp/runs", json=demo_payload())
    await client.post(
        "/v1/mvp/runs",
        json=demo_payload(
            run_id="r-pass",
            verdict="pass",
            failure_type=None,
            diagnosis=None,
            grounding_score=0.95,
        ),
    )
    await client.post(
        "/v1/mvp/runs",
        json=demo_payload(run_id="r-hallu", failure_type="hallucination"),
    )
    scorecard = (await client.get("/v1/mvp/scorecard")).json()
    assert scorecard["total"] == 3
    assert scorecard["by_verdict"] == {"pass": 1, "fail": 2}
    assert scorecard["by_failure_type"]["retrieval_miss"] == 1
    assert scorecard["by_failure_type"]["hallucination"] == 1
    assert scorecard["by_failure_type"]["prompt_issue"] == 0


async def test_mvp_diff(api):
    client, _ = api
    await client.post("/v1/mvp/runs", json=demo_payload())
    await client.post(
        "/v1/mvp/runs",
        json=demo_payload(
            run_id="r-fixed",
            parent_run_id="demo-run-1",
            verdict="pass",
            failure_type=None,
            diagnosis=None,
            grounding_score=0.95,
        ),
    )
    diff = (await client.get("/v1/mvp/runs/r-fixed/diff")).json()
    assert diff["run"]["run_id"] == "r-fixed"
    assert diff["parent"]["run_id"] == "demo-run-1"
    assert diff["verdict_changed"] is True
    assert (await client.get("/v1/mvp/runs/demo-run-1/diff")).status_code == 404
    assert (await client.get("/v1/mvp/runs/missing/diff")).status_code == 404


async def test_mvp_rejects_contradictory_or_unstorable_runs(api):
    client, _ = api
    passing = demo_payload(run_id="p", verdict="pass", failure_type="retrieval_miss")
    assert (await client.post("/v1/mvp/runs", json=passing)).status_code == 422
    for field, value in [
        ("answer", "a\x00b"),
        ("retrieved_chunks", [{"text": "a\x00b"}]),
    ]:
        payload = demo_payload(run_id="n", **{field: value})
        assert (await client.post("/v1/mvp/runs", json=payload)).status_code == 422
    assert RunResultCreate(**demo_payload()).verdict == "fail"
    assert RunResult(**passing).failure_type == "retrieval_miss"
