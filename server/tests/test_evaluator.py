import json
from uuid import UUID

import httpx
import pytest

import evaluator.llm
from app.models import Assertion, AssertionResult, Run, RunResult, TestCase
from evaluator import Evaluator
from evaluator.agent import AgentResult, retrieve, run_agent, save_trace, span
from evaluator.diagnosis import Facts, classify, judge
from evaluator.llm import ModelClient, ModelError

CONFIG = {
    "system_prompt": "Answer from policy.",
    "knowledge_base": [
        {"id": "a", "text": "Refunds within 30 days.", "source": "refund-policy"},
        {"id": "b", "text": "Shipping takes a week.", "source": "shipping"},
    ],
    "retrieval": {"top_k": 1},
}


def completion(text="ok", finish="stop", tool_calls=None):
    message = {"content": text, "tool_calls": tool_calls}
    return httpx.Response(
        200,
        json={"model": "m", "choices": [{"message": message, "finish_reason": finish}]},
    )


def client(handler, base_url="https://model.example/v1"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ModelClient(http, "secret-key", base_url, "m")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(evaluator.llm, "BACKOFF_SECONDS", 0)


async def test_client_sends_key_and_retries_transient_failures():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(503) if len(seen) == 1 else completion("hello")

    reply = await client(handler).complete(
        [{"role": "user", "content": "hi"}], params={"temperature": 0, "other": 1}
    )
    assert reply.text == "hello" and len(seen) == 2
    assert str(seen[0].url) == "https://model.example/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer secret-key"
    body = json.loads(seen[0].content)
    assert body["temperature"] == 0 and "other" not in body


@pytest.mark.parametrize(
    ("status", "message", "kind"),
    [
        (400, "maximum context length exceeded", "context_overflow"),
        (402, "payment needed", "budget"),
        (401, "bad key", "auth"),
    ],
)
async def test_client_classifies_failures_without_retrying(status, message, kind):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": message}})

    with pytest.raises(ModelError) as error:
        await client(handler).complete([])
    assert error.value.kind == kind and len(calls) == 1


async def test_client_rejects_malformed_and_oversized_replies(monkeypatch):
    with pytest.raises(ModelError):
        await client(lambda r: httpx.Response(200, json={})).complete([])
    monkeypatch.setattr(evaluator.llm, "MAX_RESPONSE_BYTES", 2)
    with pytest.raises(ModelError):
        await client(lambda r: completion()).complete([])


def test_retrieve_applies_filters_and_top_k():
    assert retrieve(CONFIG, "refund policy?")[0]["source"] == "refund-policy"
    assert len(retrieve(CONFIG, "refund policy?")) == 1
    filtered = {**CONFIG, "retrieval": {"filters": {"source": "shipping"}}}
    assert retrieve(filtered, "refund policy?") == []


async def test_hosted_agent_records_retrieval_llm_and_validation():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return completion("Refunds within 30 days.")

    config = {**CONFIG, "validators": [{"type": "json"}]}
    result = await run_agent(client(handler), config, "refund policy?")
    assert result.output == "Refunds within 30 days." and result.status == "ok"
    assert [s["type"] for s in result.spans] == ["retrieval", "llm", "validation"]
    assert result.spans[2]["status"] == "error"
    assert "Refunds within 30 days" in sent[0]["messages"][0]["content"]


async def test_external_agent_skips_retrieval():
    config = {**CONFIG, "agent": {"type": "openai_compatible"}}
    result = await run_agent(client(lambda r: completion()), config, "refund policy?")
    assert [s["type"] for s in result.spans] == ["llm"]


async def test_agent_rejects_bad_tool_arguments_then_recovers():
    replies = [
        completion(
            "",
            "tool_calls",
            [
                {
                    "id": "1",
                    "function": {"name": "lookup", "arguments": '{"order": "x"}'},
                }
            ],
        ),
        completion("done"),
    ]
    config = {
        "tools": [
            {
                "name": "lookup",
                "parameters": {
                    "type": "object",
                    "properties": {"order": {"type": "integer"}},
                },
                "result": {"status": "shipped"},
            }
        ]
    }
    result = await run_agent(client(lambda r: replies.pop(0)), config, "where?")
    tool = next(s for s in result.spans if s["type"] == "tool")
    assert tool["status"] == "error" and tool["payload"]["error"]["kind"] == "tool_arg"
    assert result.output == "done"


async def test_agent_records_model_failure_as_error_trace():
    handler = lambda r: httpx.Response(402, json={"error": {"message": "no credit"}})  # noqa: E731
    result = await run_agent(client(handler), {}, "hi")
    assert result.status == "error"
    assert result.spans[-1]["payload"]["error"]["kind"] == "budget"


def llm(**payload):
    return {"id": "s", "type": "llm", "status": "ok", "payload": payload}


def retrieval(*chunks):
    return {"id": "r", "type": "retrieval", "status": "ok", "chunks": list(chunks)}


POLICY = {"text": "Refunds within 30 days", "source": "refund-policy"}
OTHER = {"text": "Shipping takes 7 days", "source": "shipping"}


@pytest.mark.parametrize(
    ("spans", "output", "expected", "failures", "rule"),
    [
        (
            [{"id": "v", "type": "validation", "status": "error",
              "payload": {"category": "format"}}],
            "x", {}, [], "format",
        ),
        (
            [{"id": "t", "type": "tool", "status": "error",
              "payload": {"error": {"kind": "tool_arg", "message": "bad"}}}],
            "x", {}, [], "tool_arg",
        ),
        (
            [llm(error={"kind": "context_overflow"})],
            "", {}, [], "context_overflow",
        ),
        ([llm(finish_reason="length")], "x", {}, [], "budget"),
        ([llm(finish_reason="content_filter")], "x", {}, [], "safety"),
        (
            [retrieval(OTHER)], "60 days", {"source": "refund-policy"}, [],
            "retrieval_miss",
        ),
        (
            [retrieval(POLICY)], "Ask support", {"reference": "30 days"}, [],
            "grounding",
        ),
        ([retrieval(POLICY)], "Refunds within 60 days", {}, [], "unsupported_claim"),
        (
            [], "x", {}, [{"spec": {"type": "contains", "value": "y"}}],
            "instruction_violation",
        ),
    ],
)  # fmt: skip
def test_rules_classify_each_failure(spans, output, expected, failures, rule):
    facts = Facts({"spans": spans, "output": output}, {"expected": expected}, failures)
    assert classify(facts)[0] == rule


def test_rules_run_in_order_and_can_decline():
    facts = Facts(
        {"spans": [llm(finish_reason="length"), retrieval(OTHER)], "output": "60"}
    )
    assert classify(facts)[0] == "budget"
    assert classify(Facts({"spans": [], "output": "fine"})) is None


async def test_judge_fallback_stores_suggestion_and_survives_bad_replies():
    verdict = '{"rule": "grounding", "confidence": 7, "rationale": "why"}'
    facts = Facts({"spans": [], "output": "x"})
    rule, confidence, evidence = await judge(
        client(lambda r: completion(verdict)), facts
    )
    assert (rule, confidence, evidence["suggested_rule"]) == ("judge", 1.0, "grounding")
    assert (await judge(client(lambda r: completion("nope")), facts))[1] == 0.0
    assert (await judge(None, facts))[1] == 0.0


async def setup(client_, app, monkeypatch, handler):
    monkeypatch.setattr(
        "app.services.evaluator.safe_http_client",
        lambda settings: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app.state.evaluator = Evaluator()
    p = (await client_.post("/v1/projects", json={"name": "Acme"})).json()["id"]
    v = (
        await client_.post(f"/v1/projects/{p}/versions", json={"config": CONFIG})
    ).json()["id"]
    c = (
        await client_.post(
            "/v1/credentials",
            json={"provider": "openrouter", "model": "m", "api_key": "secret-key"},
        )
    ).json()["credential_id"]
    return p, v, c


async def failed_result(app, project, version, query, output, expected, spec):
    result = AgentResult(
        output=output,
        spans=[
            span(
                "retrieval",
                chunks=[{"rank": 1, "text": "Ships fast", "source": "shipping"}],
            )
        ],
    )
    async with app.state.sessions() as db, db.begin():
        db.info["redactor"] = app.state.redactor
        trace_id = await save_trace(db, UUID(version), query, result)
        case = TestCase(
            project_id=UUID(project), origin="user", input=query, expected=expected
        )
        run = Run(app_version_id=UUID(version))
        db.add_all([case, run])
        await db.flush()
        row = RunResult(
            run_id=run.id, test_case_id=case.id, trace_id=trace_id, passed=False
        )
        assertion = Assertion(test_case_id=case.id, kind="deterministic", spec=spec)
        db.add_all([row, assertion])
        await db.flush()
        db.add(
            AssertionResult(
                run_result_id=row.id, assertion_id=assertion.id, passed=False
            )
        )
        await db.flush()
        return str(row.id), str(trace_id)


async def test_diagnose_api_confirms_retrieval_miss_without_model_call(
    api, monkeypatch
):
    http, app = api
    calls = []

    def handler(request):
        calls.append(request)
        return completion()

    p, v, _ = await setup(http, app, monkeypatch, handler)
    result_id, trace_id = await failed_result(
        app,
        p,
        v,
        "Refund window?",
        "Refunds within 60 days",
        {"reference": "30 days", "source": "refund-policy"},
        {"type": "contains", "value": "30 days"},
    )
    response = await http.post(f"/v1/run-results/{result_id}/diagnose", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rule"] == "retrieval_miss" and body["trace_id"] == trace_id
    assert body["evidence"]["retrieved_sources"] == ["shipping"]
    assert calls == []


async def test_diagnose_api_uses_judge_for_unmatched_trace(api, monkeypatch):
    http, app = api
    verdict = '{"rule": "instruction_violation", "confidence": 0.4, "rationale": "r"}'
    p, v, c = await setup(http, app, monkeypatch, lambda r: completion(verdict))
    async with app.state.sessions() as db, db.begin():
        db.info["redactor"] = app.state.redactor
        trace_id = await save_trace(db, UUID(v), "hi", AgentResult(output="hello"))
    response = await http.post(
        f"/v1/traces/{trace_id}/diagnose", json={"credential_id": c}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["rule"], body["confidence"]) == ("judge", 0.4)
    assert body["evidence"]["suggested_rule"] == "instruction_violation"


async def test_test_connection_reports_success_and_failure(api, monkeypatch):
    http, app = api
    replies = [
        completion("pong"),
        httpx.Response(401, json={"error": {"message": "bad key"}}),
    ]
    p, _, c = await setup(http, app, monkeypatch, lambda r: replies.pop(0))
    body = {"credential_id": c, "agent": {"type": "openai_compatible"}}
    path = f"/v1/projects/{p}/agent/test-connection"
    assert (await http.post(path, json=body)).json()["ok"] is True
    failed = (await http.post(path, json=body)).json()
    assert (failed["ok"], failed["kind"]) == (False, "auth")


async def test_unimplemented_operations_stay_explicit(api, monkeypatch):
    http, app = api
    p, v, c = await setup(http, app, monkeypatch, lambda r: completion())
    await http.post(f"/v1/projects/{p}/test-cases", json={"input": "q"})
    job = (
        await http.post(
            f"/v1/projects/{p}/runs", json={"version_id": v, "credential_id": c}
        )
    ).json()
    result = (await http.get(f"/v1/jobs/{job['job_id']}")).json()
    assert result["status"] == "failed"
    assert result["error"]["error"]["code"] == "evaluator_operation_unavailable"
