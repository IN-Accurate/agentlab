from copy import deepcopy
from datetime import UTC, datetime
from time import perf_counter

import jsonschema

from app.models import Trace, Span, RetrievedChunk
from app.services import llm, retrieval


async def run_agent(
    db, state, version, text, credential_id, params=None, config_override=None
):
    config = deepcopy(
        config_override if config_override is not None else version.config
    )
    config.setdefault("params", {}).update(params or {})
    external = config.get("agent", {}).get("type", "hosted") == "openai_compatible"
    trace = Trace(
        app_version_id=version.id, input=state.redactor.text(text), status="ok"
    )
    db.add(trace)
    await db.flush()
    context = []
    parent = None
    if not external:
        start = perf_counter()
        context = retrieval.retrieve(config, text)
        span = Span(
            trace_id=trace.id,
            sdk_span_id="retrieval",
            type="retrieval",
            started_at=datetime.now(UTC),
            latency_ms=int((perf_counter() - start) * 1000),
            status="ok",
            payload={"top_k": config.get("retrieval", {}).get("top_k", 3)},
        )
        db.add(span)
        await db.flush()
        parent = span.id
        for chunk in context:
            db.add(
                RetrievedChunk(
                    span_id=span.id,
                    rank=chunk["rank"],
                    text=state.redactor.text(chunk["text"]),
                    source=state.redactor.text(chunk.get("source", "")),
                    score=chunk.get("score"),
                )
            )
    system = config.get("system_prompt", "")
    if config.get("grounding_rule"):
        system += "\n" + config["grounding_rule"]
    if context:
        system += "\nContext:\n" + "\n".join(
            f"[{c.get('source', '')}] {c['text']}" for c in context
        )
    started = datetime.now(UTC)
    begin = perf_counter()
    try:
        result = await llm.complete(
            state,
            credential_id,
            [{"role": "system", "content": system}, {"role": "user", "content": text}],
            params=config.get("params"),
            tools=config.get("tools"),
            agent=config.get("agent"),
        )
        trace.output = state.redactor.text(result["content"] or "")
        payload = state.redactor.scrub(
            {k: result.get(k) for k in ("usage", "finish_reason", "tool_calls")}
        )
        payload["agent_type"] = "openai_compatible" if external else "hosted"
        span = Span(
            trace_id=trace.id,
            parent_span_id=parent,
            sdk_span_id="llm",
            type="llm",
            started_at=started,
            latency_ms=result.get("latency_ms", int((perf_counter() - begin) * 1000)),
            status="ok",
            payload=payload,
        )
        db.add(span)
        await db.flush()
        for i, call in enumerate(result.get("tool_calls") or []):
            # Tool calls are observations; arbitrary user tools are never executed here.
            db.add(
                Span(
                    trace_id=trace.id,
                    parent_span_id=span.id,
                    sdk_span_id=f"tool-{i}",
                    type="tool",
                    started_at=datetime.now(UTC),
                    status="ok",
                    payload=state.redactor.scrub(call),
                )
            )
        failures = []
        for validator in config.get("validators", []):
            if validator.get("type") == "json_schema":
                import json

                try:
                    jsonschema.validate(json.loads(trace.output), validator["schema"])
                except (ValueError, jsonschema.ValidationError):
                    failures.append("json_schema")
        db.add(
            Span(
                trace_id=trace.id,
                parent_span_id=span.id,
                sdk_span_id="validation",
                type="validation",
                started_at=datetime.now(UTC),
                status="error" if failures else "ok",
                payload={"failed": failures},
            )
        )
    except Exception:
        trace.status = "error"
        trace.output = "Agent call failed"
        db.add(
            Span(
                trace_id=trace.id,
                parent_span_id=parent,
                sdk_span_id="llm",
                type="llm",
                started_at=started,
                latency_ms=int((perf_counter() - begin) * 1000),
                status="error",
                payload={"error": "provider_error"},
            )
        )
    await db.flush()
    return trace
