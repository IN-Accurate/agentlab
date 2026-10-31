from uuid import UUID

from app.models import (
    AppVersion,
    Run,
    RunResult,
    TestCase,
    Trace,
    Diagnosis,
    Repair,
    Span,
    Verification,
)
from test_runs import setup


async def test_chat_forwards_events_without_intent_or_model_calls(api):
    client, app = api
    p, v, c, t = await setup(client)
    body = {
        "message": "Explain this",
        "history": [{"role": "user", "content": "Earlier question"}],
        "credential_id": c,
    }
    assert (await client.post(f"/v1/projects/{p}/chat", json=body)).status_code == 503

    class TransportFixture:
        async def stream_chat(self, payload, context):
            assert payload["options"]["history"] == body["history"]
            assert payload["options"]["message"] == body["message"]
            yield {"event": "step.started", "data": {"step": "fixture"}}
            yield {"event": "message.delta", "data": {"text": "REPLA"}}
            yield {"event": "message.delta", "data": {"text": "CE_ME"}}
            yield {
                "event": "tool.result",
                "data": {"type": "scorecard", "data": {"accepted": False}},
            }
            yield {"event": "done", "data": {}}

    app.state.evaluator = TransportFixture()
    response = await client.post(f"/v1/projects/{p}/chat", json=body)
    assert response.status_code == 200
    assert "REPLACE_ME" not in response.text
    assert "[REDACTED]" in response.text
    assert response.text.count("event: done") == 1
    assert '"accepted": false' in response.text


async def test_dashboard_formats_existing_verdict_without_deciding(api):
    client, app = api
    p, v, c, t = await setup(client)
    async with app.state.sessions() as db, db.begin():
        candidate = AppVersion(project_id=UUID(p), parent_version_id=UUID(v), config={})
        db.add(candidate)
        await db.flush()
        trace = Trace(
            app_version_id=UUID(v),
            input="Stored fixture",
            output="Stored fixture",
            status="ok",
        )
        db.add(trace)
        await db.flush()
        diagnosis = Diagnosis(
            trace_id=trace.id, rule="judge", confidence=0.5, evidence={"fixture": True}
        )
        db.add(diagnosis)
        await db.flush()
        repair = Repair(
            diagnosis_id=diagnosis.id,
            new_version_id=candidate.id,
            patch_type="prompt",
            patch={},
        )
        left = Run(app_version_id=UUID(v), params={})
        right = Run(app_version_id=candidate.id, params={})
        db.add_all([repair, left, right])
        await db.flush()
        row = Verification(
            repair_id=repair.id,
            baseline_run_id=left.id,
            candidate_run_id=right.id,
            target_passed=True,
            regressions_count=0,
            holdout_passed=True,
            accepted=False,
        )
        db.add(row)
        await db.flush()
        vid = str(row.id)
    stored = (await client.get("/v1/verifications/" + vid)).json()
    assert stored["accepted"] is False
    dashboard = (await client.get(f"/v1/projects/{p}/dashboard")).json()
    assert dashboard["versions"][-1]["rejected_repairs"] == 1
    assert (
        await client.get(f"/v1/projects/{p}/dashboard/series?metric=pass_rate")
    ).status_code == 200


async def test_dashboard_counts_shared_trace_latency_once_per_run(api):
    client, app = api
    p, v, c, t = await setup(client)
    async with app.state.sessions() as db, db.begin():
        cases = [
            TestCase(
                project_id=UUID(p),
                origin="user",
                split="dev",
                input=f"Query {i}",
                expected={},
            )
            for i in range(2)
        ]
        db.add_all(cases)
        await db.flush()
        run = Run(app_version_id=UUID(v), params={})
        db.add(run)
        await db.flush()
        shared = Trace(app_version_id=UUID(v), input="s", output="s", status="ok")
        solo = Trace(app_version_id=UUID(v), input="u", output="u", status="ok")
        db.add_all([shared, solo])
        await db.flush()
        db.add_all(
            [
                RunResult(
                    run_id=run.id,
                    test_case_id=UUID(t),
                    passed=True,
                    trace_id=shared.id,
                ),
                RunResult(
                    run_id=run.id,
                    test_case_id=cases[0].id,
                    passed=True,
                    trace_id=shared.id,
                ),
                RunResult(
                    run_id=run.id,
                    test_case_id=cases[1].id,
                    passed=True,
                    trace_id=solo.id,
                ),
                Span(
                    trace_id=shared.id,
                    sdk_span_id="shared",
                    type="llm",
                    latency_ms=10,
                ),
                Span(trace_id=solo.id, sdk_span_id="solo", type="llm", latency_ms=1000),
            ]
        )
    dashboard = (await client.get(f"/v1/projects/{p}/dashboard")).json()
    assert dashboard["versions"][0]["latency_p50"] == 505.0
