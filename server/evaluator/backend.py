from uuid import UUID

from sqlalchemy import select

from app.db import record
from app.errors import AppError
from app.models import Assertion, AssertionResult, Diagnosis, TestCase
from evaluator.diagnosis import Facts, classify, judge
from evaluator.llm import ModelClient, ModelError


async def test_connection(payload, context):
    options = payload["options"]
    agent = options.get("agent") or {}
    try:
        model = ModelClient.from_context(context, options["credential_id"], agent)
        reply = await model.complete(
            [{"role": "user", "content": "ping"}], params={"max_tokens": 1}
        )
    except ModelError as exc:
        return {"ok": False, "error": exc.message, "kind": exc.kind}
    return {
        "ok": True,
        "latency_ms": reply.latency_ms,
        "model": reply.model or model.model,
    }


async def source_case(db, trace_id):
    row = await db.scalar(
        select(TestCase).where(TestCase.source_trace_id == trace_id).limit(1)
    )
    return record(row) if row else None


async def assertion_failures(db, run_result_id):
    rows = await db.execute(
        select(AssertionResult, Assertion)
        .join(Assertion, Assertion.id == AssertionResult.assertion_id)
        .where(
            AssertionResult.run_result_id == run_result_id,
            AssertionResult.passed.is_(False),
        )
    )
    return [
        {"spec": assertion.spec, "evidence": result.evidence}
        for result, assertion in rows
    ]


async def run_diagnosis(payload, context):
    target, options = payload["target"], payload["options"]
    db = context["session"]
    trace_id = UUID(str(target["trace"]["id"]))
    if target.get("passed") is True:
        raise AppError("result_passed", "Only failed results can be diagnosed", 422)
    if "run_id" in target:
        case = target["test_case"]
        failures = await assertion_failures(db, UUID(str(target["id"])))
    else:
        case, failures = await source_case(db, trace_id), []
    facts = Facts(target["trace"], case, failures)
    found = classify(facts)
    if found is None:
        credential = options.get("judge_credential_id") or options.get("credential_id")
        model = (
            ModelClient.from_context(context, credential, judge=True)
            if credential
            else None
        )
        found = await judge(model, facts)
    rule, confidence, evidence = found
    row = Diagnosis(
        trace_id=trace_id, rule=rule, confidence=confidence, evidence=evidence
    )
    db.add(row)
    await db.flush()
    return {"id": str(row.id)}


HANDLERS = {"test_connection": test_connection, "diagnose": run_diagnosis}


class Evaluator:
    async def execute(self, payload, context):
        handler = HANDLERS.get(payload["operation"])
        if handler is None:
            raise AppError(
                "evaluator_operation_unavailable",
                f"Operation {payload['operation']} is not implemented yet",
                503,
            )
        return await handler(payload, context)
