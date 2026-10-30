from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AppVersion, Assertion, AssertionResult, TestCase, Trace
from app.services.assertions import (
    deterministic,
    evaluate_trace,
    run_evaluation,
    summarize,
)
from app.services.llm import FakeLLMClient
from test_agent import setup_agent


@pytest.mark.parametrize(
    "spec,output,expected",
    [
        ({"type": "contains", "value": "30 days"}, "Refunds within 30 days", True),
        ({"type": "contains", "value": "30 days"}, "Refunds within 60 days", False),
        ({"type": "not_contains", "value": "60 days"}, "Refunds within 30 days", True),
        ({"type": "not_contains", "value": "60 days"}, "Refunds within 60 days", False),
        ({"type": "regex", "pattern": r"\d+ days"}, "30 days", True),
        ({"type": "regex", "pattern": r"\d+ days"}, "No deadline", False),
        (
            {"type": "json_schema", "schema": {"type": "object", "required": ["ok"]}},
            '{"ok":true}',
            True,
        ),
        (
            {"type": "json_schema", "schema": {"type": "object", "required": ["ok"]}},
            "{}",
            False,
        ),
        ({"type": "max_latency_ms", "value": 30}, "Answer", True),
        ({"type": "max_latency_ms", "value": 10}, "Answer", False),
        ({"type": "max_tokens", "value": 40}, "Answer", True),
        ({"type": "max_tokens", "value": 10}, "Answer", False),
        ({"type": "tool_called", "name": "lookup"}, "Answer", True),
        ({"type": "tool_called", "name": "missing"}, "Answer", False),
        (
            {
                "type": "tool_args_valid",
                "schema": {"type": "object", "required": ["id"]},
            },
            "Answer",
            True,
        ),
        (
            {
                "type": "tool_args_valid",
                "schema": {"type": "object", "required": ["missing"]},
            },
            "Answer",
            False,
        ),
        (
            {"type": "cited_source_in_retrieved", "source": "policy"},
            "30 days [policy]",
            True,
        ),
        (
            {"type": "cited_source_in_retrieved", "source": "unknown"},
            "30 days [unknown]",
            False,
        ),
    ],
)
def test_every_deterministic_assertion_pass_and_fail(spec, output, expected):
    spans = [
        SimpleNamespace(
            type="llm", latency_ms=20, payload={"usage": {"total_tokens": 32}}
        ),
        SimpleNamespace(
            type="tool",
            latency_ms=0,
            payload={"function": {"name": "lookup", "arguments": '{"id":"example"}'}},
        ),
    ]
    passed, evidence = deterministic(
        spec,
        SimpleNamespace(output=output),
        spans,
        [SimpleNamespace(source="policy")],
        SimpleNamespace(expected={}),
        {},
    )
    assert passed is expected
    assert evidence["status"] == ("passed" if expected else "failed")


def test_citation_without_retrieval_is_skipped():
    passed, evidence = deterministic(
        {"type": "cited_source_in_retrieved"},
        SimpleNamespace(output="Answer"),
        [],
        [],
        SimpleNamespace(expected={}),
        {},
    )
    assert passed is False
    assert evidence == {
        "status": "skipped",
        "reason": "no retrieval data",
        "type": "cited_source_in_retrieved",
    }


async def make_case(db, version, trace_output="30 days"):
    case = TestCase(
        project_id=version.project_id,
        origin="user",
        input="Refund policy?",
        expected={"reference": "30 days"},
    )
    trace = Trace(app_version_id=version.id, input=case.input, output=trace_output)
    db.add_all([case, trace])
    await db.flush()
    return case, trace


async def test_deterministic_failure_skips_judge_even_with_lower_position(api):
    client, app = api
    version_id, credential_id = await setup_agent(client, {})
    app.state.fake_llm = FakeLLMClient()
    async with app.state.sessions() as db, db.begin():
        version = await db.get(AppVersion, version_id)
        case, trace = await make_case(db, version, "60 days")
        db.add_all(
            [
                Assertion(test_case_id=case.id, kind="judge", position=0, spec={}),
                Assertion(
                    test_case_id=case.id,
                    kind="deterministic",
                    position=50,
                    spec={"type": "contains", "value": "30 days"},
                ),
            ]
        )
        await db.flush()
        passed, evidence = await evaluate_trace(
            db, app.state, trace, case, credential_id
        )
        assert passed is False
        assert [item["evidence"]["status"] for item in evidence] == [
            "failed",
            "skipped",
        ]
        assert app.state.fake_llm.calls == []


async def test_judge_retries_malformed_json_once(api):
    client, app = api
    version_id, credential_id = await setup_agent(client, {})
    app.state.fake_llm = FakeLLMClient(
        [
            "invalid",
            {
                "passed": True,
                "reasoning": "Matches reference",
                "unsupported_claims": [],
            },
        ]
    )
    async with app.state.sessions() as db, db.begin():
        case, trace = await make_case(db, await db.get(AppVersion, version_id))
        db.add(Assertion(test_case_id=case.id, kind="judge", spec={}))
        await db.flush()
        passed, evidence = await evaluate_trace(
            db, app.state, trace, case, credential_id
        )
        assert passed is True
        assert evidence[0]["evidence"]["reasoning"] == "Matches reference"
        assert len(app.state.fake_llm.calls) == 2


async def test_malformed_judge_twice_is_failed_not_passed(api):
    client, app = api
    version_id, credential_id = await setup_agent(client, {})
    app.state.fake_llm = FakeLLMClient(["invalid", '{"passed":"yes"}'])
    async with app.state.sessions() as db, db.begin():
        case, trace = await make_case(db, await db.get(AppVersion, version_id))
        db.add(Assertion(test_case_id=case.id, kind="judge", spec={}))
        await db.flush()
        passed, evidence = await evaluate_trace(
            db, app.state, trace, case, credential_id
        )
        assert passed is False
        assert "twice" in evidence[0]["evidence"]["reason"]
        assert len(app.state.fake_llm.calls) == 2


async def test_run_persists_results_and_preserves_skipped_citation(api):
    client, app = api
    version_id, credential_id = await setup_agent(
        client, {"agent": {"type": "openai_compatible"}}
    )
    async with app.state.sessions() as db, db.begin():
        version = await db.get(AppVersion, version_id)
        case = TestCase(
            project_id=version.project_id, origin="user", split="holdout", input="Hello"
        )
        db.add(case)
        await db.flush()
        db.add(
            Assertion(
                test_case_id=case.id,
                kind="deterministic",
                spec={"type": "cited_source_in_retrieved"},
            )
        )
        await db.flush()
        run = await run_evaluation(
            db, app.state, version, [case], credential_id, {"seed": 7}
        )
        summary = await summarize(db, run.id)
        assert summary["total"] == summary["passed"] == 1
        assert summary["holdout"]["pass_rate"] == 1
        assert summary["dev"]["pass_rate"] is None
        result = await db.scalar(select(AssertionResult))
        assert result.passed is False
        assert result.evidence["status"] == "skipped"
