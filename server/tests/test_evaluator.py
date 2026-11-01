import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

import evaluator.llm
from app.errors import AppError
from app.models import Assertion, AssertionResult, Run, RunResult, TestCase, Trace
from evaluator import Evaluator
from evaluator.agent import (
    AgentResult,
    check,
    retrieve,
    run_agent,
    run_tool,
    save_trace,
    span,
    tool_definitions,
    validate,
)
from evaluator.diagnosis import Facts, classify, judge
from evaluator.evaluate import prior_messages
from evaluator.llm import ModelClient, ModelError
from evaluator.repair import score

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


async def test_client_reports_corrupt_compressed_reply_without_retrying():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"content-encoding": "gzip"}, content=b"not gzip"
        )

    with pytest.raises(ModelError) as error:
        await client(handler).complete([])
    assert error.value.kind == "provider" and len(calls) == 1


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
    for module in ("evaluator", "chat"):
        monkeypatch.setattr(
            f"app.services.{module}.safe_http_client",
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


async def test_diagnose_api_matches_rule_without_usable_judge_credential(
    api, monkeypatch
):
    http, app = api
    p, v, _ = await setup(http, app, monkeypatch, lambda r: completion())
    credential = await http.post(
        "/v1/credentials",
        json={"provider": "custom", "model": "m", "api_key": "secret-key"},
    )
    result_id, _ = await failed_result(
        app,
        p,
        v,
        "Refund window?",
        "Refunds within 60 days",
        {"reference": "30 days", "source": "refund-policy"},
        {"type": "contains", "value": "30 days"},
    )
    response = await http.post(
        f"/v1/run-results/{result_id}/diagnose",
        json={"credential_id": credential.json()["credential_id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rule"] == "retrieval_miss"


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
    await setup(http, app, monkeypatch, lambda r: completion())
    with pytest.raises(AppError) as caught:
        await app.state.evaluator.execute({"operation": "improve"}, {})
    assert caught.value.code == "evaluator_operation_unavailable"


def tool_call(arguments, parameters=None):
    tools = tool_definitions(
        {"tools": [{"name": "t", "parameters": parameters or {}, "result": {"ok": 1}}]}
    )
    return run_tool(tools, {"function": {"name": "t", "arguments": arguments}})


def test_tool_accepts_object_arguments_and_rejects_unparsable_ones():
    assert tool_call({"order": 1})[1] == "ok"
    assert tool_call('{"order": 1}')[1] == "ok"
    assert tool_call(7)[2]["error"]["kind"] == "tool_arg"
    assert tool_call("{bad")[2]["error"]["kind"] == "tool_arg"


@pytest.mark.parametrize(
    "schema",
    [{"type": "nope"}, {"$ref": "#/$defs/missing"}, {"$ref": "https://a.example/s"}],
)
def test_unusable_schemas_are_reported_not_raised(schema, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Schema triggered a remote fetch")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    assert tool_call("{}", schema)[2]["error"]["kind"] == "tool_schema"
    assert check({"type": "json_schema", "schema": schema}, "{}") is None


def test_local_schema_references_still_validate():
    schema = {"$defs": {"n": {"type": "integer"}}, "$ref": "#/$defs/n"}
    assert check({"type": "json_schema", "schema": schema}, "1") is True
    assert check({"type": "json_schema", "schema": schema}, '"x"') is False


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "max_length", "value": "many"},
        {"type": "max_length"},
        {"type": "unknown"},
        {"type": "json_schema", "schema": {"type": "nope"}},
    ],
)
def test_unevaluable_validators_do_not_become_failures(spec):
    assert check(spec, "{}") is None
    item = validate({"validators": [spec]}, "{}")[0]
    assert item["status"] == "ok" and item["payload"]["passed"] is None
    facts = Facts({"spans": [{"id": "v", **item}], "output": "{}"})
    assert classify(facts) is None


async def run_job(http, p, v, c, **extra):
    body = {"version_id": v, "credential_id": c, **extra}
    job = (await http.post(f"/v1/projects/{p}/runs", json=body)).json()
    return (await http.get(f"/v1/jobs/{job['job_id']}")).json()


async def test_run_grades_cases_and_saves_each_result(api, monkeypatch):
    http, app = api

    def handler(request):
        body = json.loads(request.content)
        if "grade" in body["messages"][0]["content"]:
            passed = "polite" in body["messages"][1]["content"]
            return completion(json.dumps({"passed": passed, "rationale": "why"}))
        return completion("Refunds within 30 days.")

    p, v, c = await setup(http, app, monkeypatch, handler)
    cases = [
        {"input": "good", "expected": {"reference": "30 days"}},
        {"input": "bad", "expected": {"reference": "60 days"}},
        {"input": "kind", "expected": {"behavior": "be polite"}},
        {"input": "rude", "expected": {"behavior": "be rude"}},
    ]
    ids = []
    for case in cases:
        response = await http.post(f"/v1/projects/{p}/test-cases", json=case)
        ids.append(response.json()["id"])
    await http.post(
        f"/v1/test-cases/{ids[0]}/assertions",
        json={"kind": "deterministic", "spec": {"type": "contains", "value": "nope"}},
    )
    job = await run_job(http, p, v, c, params={"temperature": 0, "max_tokens": 64})
    assert job["status"] == "completed", job
    assert (job["result"]["evaluated_cases"], job["result"]["passed"]) == (4, 1)
    run_id = job["result"]["run_id"]
    summary = (await http.get(f"/v1/runs/{run_id}/summary")).json()
    assert (summary["total"], summary["passed"]) == (4, 1)
    passed = {r["test_case_id"]: r["passed"] for r in summary["results"]}
    assert passed == dict(zip(ids, [False, False, True, False]))
    assert all(r["trace_id"] for r in summary["results"])
    run = (await http.get(f"/v1/runs/{run_id}")).json()
    assert run["params"]["max_tokens"] == 64
    async with app.state.sessions() as db:
        evidence = list(await db.scalars(select(AssertionResult)))
    assert [row.passed for row in evidence] == [False]


async def test_run_never_passes_on_failed_model_or_grader(api, monkeypatch):
    http, app = api

    def handler(request):
        body = json.loads(request.content)
        if "grade" in body["messages"][0]["content"]:
            return completion("not json")
        if body["messages"][-1]["content"] == "down":
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        if body["messages"][-1]["content"] == "empty":
            return completion("")
        return completion("answer")

    p, v, c = await setup(http, app, monkeypatch, handler)
    for query, expected in [
        ("down", {}),
        ("empty", {}),
        ("graded", {"behavior": "anything"}),
    ]:
        await http.post(
            f"/v1/projects/{p}/test-cases", json={"input": query, "expected": expected}
        )
    job = await run_job(http, p, v, c)
    assert job["status"] == "completed", job
    assert job["result"]["passed"] == 0


def events(text):
    parsed = []
    for frame in text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = frame.split("\n")
        data = next(x[5:] for x in lines if x.startswith("data:"))
        parsed.append((lines[0][6:].strip(), json.loads(data)))
    return parsed


async def test_chat_answers_with_history_and_saves_a_trace(api, monkeypatch):
    http, app = api
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return completion("Hello there")

    p, _, c = await setup(http, app, monkeypatch, handler)
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "Error: old failure"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "hi"},
    ]
    body = {"message": "hi", "history": history, "credential_id": c}
    response = await http.post(f"/v1/projects/{p}/chat", json=body)
    assert events(response.text) == [
        ("message.delta", {"text": "Hello there"}),
        ("done", {}),
    ]
    sent = [m["content"] for m in seen[0]["messages"] if m["role"] != "system"]
    assert sent == ["first", "reply", "hi"]
    async with app.state.sessions() as db:
        trace = await db.scalar(select(Trace))
    assert (trace.input, trace.output) == ("hi", "Hello there")


async def test_chat_reports_model_failure(api, monkeypatch):
    http, app = api
    p, _, c = await setup(
        http,
        app,
        monkeypatch,
        lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}),
    )
    body = {"message": "hi", "history": [], "credential_id": c}
    response = await http.post(f"/v1/projects/{p}/chat", json=body)
    parsed = events(response.text)
    assert parsed[0][0] == "error" and parsed[-1][0] == "done"
    assert parsed[0][1]["error"]["message"]


async def test_chat_run_test_returns_card_and_reuses_case(api, monkeypatch):
    http, app = api
    prompts = []

    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        if system.startswith("Write one"):
            prompts.append(body)
            return completion("Can I get a refund?")
        if "grade" in system:
            return completion('{"passed": false, "rationale": "Too vague"}')
        if system.startswith("You diagnose"):
            return completion('{"rule": "grounding", "confidence": 0.5}')
        assert "30 days" in system
        return completion("Maybe")

    p, _, c = await setup(http, app, monkeypatch, handler)
    message = "Run test: States the refund window\n\nAdditional context:\n30 days"
    body = {"message": message, "history": [], "credential_id": c}
    for _ in range(2):
        response = await http.post(f"/v1/projects/{p}/chat", json=body)
    name, tool = events(response.text)[-2]
    assert (name, tool["type"]) == ("tool.result", "run_summary")
    card = tool["data"]
    assert card["verdict"] == "fail" and card["actual"] == "Maybe"
    assert card["expected"] == "States the refund window"
    assert card["explanation"] == "Too vague"
    assert len(prompts) == 1
    async with app.state.sessions() as db:
        cases = list(await db.scalars(select(TestCase)))
        results = list(await db.scalars(select(RunResult)))
    assert len(cases) == 1 and cases[0].input == "Can I get a refund?"
    assert len(results) == 2
    diagnosis = await http.post(
        f"/v1/run-results/{card['run_result_id']}/diagnose",
        json={"credential_id": c},
    )
    assert diagnosis.status_code in (200, 201, 202)


def test_prior_messages_drop_current_turn_with_added_context():
    history = [{"role": "user", "content": "hi"}]
    assert prior_messages(history, "hi\n\nAdditional context:\nx") == []


async def fix_loop_fixture(http, app, monkeypatch, fixed=True):
    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        if system.startswith("You improve"):
            return completion(
                '{"system_prompt": "Always cite 30 days.", "rationale": "r"}'
            )
        if system.startswith("You diagnose"):
            return completion('{"rule": "grounding", "confidence": 0.5}')
        if "grade" in system:
            passed = "30 days" in body["messages"][1]["content"].split("criterion")[0]
            return completion(json.dumps({"passed": passed, "rationale": "why"}))
        return completion("30 days" if fixed and "cite" in system else "unsure")

    p, v, c = await setup(http, app, monkeypatch, handler)
    expected = {"behavior": "States 30 days"}
    await http.post(
        f"/v1/projects/{p}/test-cases", json={"input": "Dev?", "expected": expected}
    )
    await http.post(
        f"/v1/projects/{p}/test-cases",
        json={"input": "Holdout?", "expected": expected, "split": "holdout"},
    )
    job = await run_job(http, p, v, c)
    summary = (await http.get(f"/v1/runs/{job['result']['run_id']}/summary")).json()
    dev = next(r for r in summary["results"] if r["split"] == "dev")
    diagnosis = await http.post(
        f"/v1/run-results/{dev['id']}/diagnose", json={"credential_id": c}
    )
    return p, v, c, summary, diagnosis.json()["id"]


async def verify_job(http, repair_id, c):
    job = (
        await http.post(f"/v1/repairs/{repair_id}/verify", json={"credential_id": c})
    ).json()
    return (await http.get(f"/v1/jobs/{job['job_id']}")).json()


async def test_repair_and_verify_accept_a_real_improvement(api, monkeypatch):
    http, app = api
    p, v, c, _, diagnosis = await fix_loop_fixture(http, app, monkeypatch)
    created = await http.post(
        f"/v1/diagnoses/{diagnosis}/repairs", json={"credential_id": c}
    )
    assert created.status_code == 201, created.text
    repair = created.json()
    assert repair["patch_type"] == "prompt"
    candidate = (await http.get(f"/v1/versions/{repair['new_version_id']}")).json()
    assert candidate["parent_version_id"] == v
    assert candidate["config"]["system_prompt"] == "Always cite 30 days."
    assert candidate["config"]["knowledge_base"] == CONFIG["knowledge_base"]

    job = await verify_job(http, repair["id"], c)
    assert job["status"] == "completed", job
    scores = job["result"]["scorecard"]
    assert scores == {
        "target_passed": True,
        "regressions_count": 0,
        "holdout_passed": True,
        "accepted": True,
    }
    card = await http.get(f"/v1/verifications/{job['result']['verification_id']}")
    assert card.json()["candidate"]["passed"] == 2
    assert card.json()["baseline"]["passed"] == 0


async def test_verify_rejects_a_repair_that_does_not_help(api, monkeypatch):
    http, app = api
    p, v, c, _, diagnosis = await fix_loop_fixture(http, app, monkeypatch, fixed=False)
    repair = (
        await http.post(f"/v1/diagnoses/{diagnosis}/repairs", json={"credential_id": c})
    ).json()
    job = await verify_job(http, repair["id"], c)
    assert job["result"]["scorecard"]["target_passed"] is False
    assert job["result"]["scorecard"]["accepted"] is False


async def test_repair_refuses_holdout_results(api, monkeypatch):
    http, app = api
    p, v, c, summary, _ = await fix_loop_fixture(http, app, monkeypatch)
    held = next(r for r in summary["results"] if r["split"] == "holdout")
    diagnosis = await http.post(
        f"/v1/run-results/{held['id']}/diagnose", json={"credential_id": c}
    )
    response = await http.post(
        f"/v1/diagnoses/{diagnosis.json()['id']}/repairs", json={"credential_id": c}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "holdout_target"


def test_score_needs_holdout_tests_to_accept():
    cases = [{"id": "a", "split": "dev"}]
    scores = score(cases, {"a": False}, {"a": True}, "a")
    assert scores["target_passed"] is True
    assert (scores["holdout_passed"], scores["accepted"]) == (False, False)
    with_regression = score(
        [*cases, {"id": "b", "split": "holdout"}],
        {"a": False, "b": True},
        {"a": True, "b": False},
        "a",
    )
    assert with_regression["regressions_count"] == 1
    assert with_regression["accepted"] is False


def test_numbers_are_not_unsupported_without_retrieved_text():
    trace = {"output": "Ready in 30 minutes", "spans": [span("retrieval")]}
    for item in trace["spans"]:
        item["type"] = "retrieval"
    assert classify(Facts(trace, {"expected": {}}, [])) is None
