import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import Unresolvable

from app.models import RetrievedChunk, Span, Trace
from evaluator.llm import ModelError

MAX_STEPS = 4
MAX_SPANS = 200
MAX_TEXT = 8000
MAX_OUTPUT = 32000
STOPWORDS = set(
    "a an and are do for how i in is it my of on or the to what when where which "
    "who why with you".split()
)


LIMIT_MESSAGES = {
    "step_limit": "Stopped at the team's step limit",
    "spend_limit": "Stopped at the team's token limit",
}


@dataclass
class AgentResult:
    output: str = ""
    status: str = "ok"
    spans: list = field(default_factory=list)


@dataclass
class Budget:
    """Model calls and tokens shared by every agent in one team request."""

    max_steps: int
    max_tokens: int | None = None
    steps: int = 0
    tokens: int = 0

    def charge(self):
        """Counts one model call, or returns the limit that blocks it."""
        if self.steps >= self.max_steps:
            return "step_limit"
        if self.max_tokens is not None and self.tokens >= self.max_tokens:
            return "spend_limit"
        self.steps += 1
        return None

    def record(self, messages, reply):
        # Providers may omit usage; about four characters per token is the
        # usual estimate, so the limit still holds.
        used = (reply.usage or {}).get("total_tokens")
        if not isinstance(used, int):
            text = json.dumps(messages, default=str) + reply.text
            used = len(text) // 4
        self.tokens += used


def limit_span(kind):
    error = {"kind": kind, "message": LIMIT_MESSAGES[kind]}
    return span("llm", status="error", payload={"error": error})


def clip(value, limit=MAX_TEXT):
    return value if len(value) <= limit else value[:limit] + "...[truncated]"


def span(kind, started=None, status="ok", payload=None, chunks=None):
    return {
        "sdk_span_id": uuid4().hex,
        "type": kind,
        "started_at": datetime.now(UTC),
        "latency_ms": int((monotonic() - started) * 1000) if started else 0,
        "status": status,
        "payload": payload or {},
        "chunks": chunks or [],
    }


def terms(text):
    words = re.findall(r"\w+", text.lower())
    return {word.rstrip("s") for word in words if word not in STOPWORDS}


def retrieve(config, query):
    settings = config.get("retrieval") or {}
    filters = settings.get("filters") or {}
    wanted = terms(query)
    scored = []
    for chunk in config.get("knowledge_base") or []:
        if any(
            chunk.get(key) not in (value if isinstance(value, list) else [value])
            for key, value in filters.items()
        ):
            continue
        overlap = wanted & terms(f"{chunk['text']} {chunk.get('source', '')}")
        score = len(overlap) / len(wanted) if wanted else 0
        if score > 0 and score >= settings.get("min_score", 0):
            scored.append((score, chunk))
    scored.sort(key=lambda item: -item[0])
    top = scored[: settings.get("top_k", 3)]
    return [
        {
            "rank": rank,
            "text": chunk["text"],
            "source": chunk.get("source"),
            "score": score,
        }
        for rank, (score, chunk) in enumerate(top, start=1)
    ]


def system_message(config, chunks):
    parts = [config.get("system_prompt"), config.get("grounding_rule")]
    if chunks:
        context = "\n".join(
            f"[{chunk['rank']}] ({chunk['source']}) {chunk['text']}" for chunk in chunks
        )
        parts.append(f"Context:\n{context}")
    return "\n\n".join(part for part in parts if part)


def tool_definitions(config):
    """Tools may hold a canned `result`; AgentLab cannot run the app's own tools."""
    tools = {}
    for tool in config.get("tools") or []:
        schema = tool.get("function", tool)
        if schema.get("name"):
            fields = ("name", "description", "parameters")
            tools[schema["name"]] = {
                "schema": {
                    "type": "function",
                    "function": {k: schema[k] for k in fields if k in schema},
                },
                "result": tool.get("result"),
            }
    return tools


def schema_problems(schema, data):
    """Returns None for an unusable schema; the empty registry blocks remote $refs."""
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, registry=Registry())
        return [error.message for error in validator.iter_errors(data)]
    except (SchemaError, Unresolvable):
        return None


def run_tool(tools, call):
    function = call.get("function") or {}
    name = function.get("name")
    raw = function.get("arguments")
    payload = {"name": name, "arguments": clip(str(raw))}

    def fail(kind, message):
        error = {"kind": kind, "message": message}
        return {"error": message}, "error", {**payload, "error": error}

    if name not in tools:
        return fail("tool_arg", f"Unknown tool {name}")
    try:
        arguments = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (ValueError, TypeError):
        return fail("tool_arg", "Arguments are not valid JSON")
    schema = tools[name]["schema"]["function"].get("parameters") or {}
    problems = schema_problems(schema, arguments)
    if problems is None:
        return fail("tool_schema", "Tool schema is invalid")
    if problems:
        return fail("tool_arg", "; ".join(problems)[:500])
    result = tools[name]["result"]
    if result is None:
        return fail("no_executor", f"No canned result for tool {name}")
    return result, "ok", {**payload, "result": result}


def check(spec, output):
    """Returns None for a spec this agent cannot evaluate."""
    kind, value = spec.get("type"), spec.get("value")
    if kind == "contains":
        return str(value).lower() in output.lower()
    if kind == "not_contains":
        return str(value).lower() not in output.lower()
    if kind == "max_length":
        try:
            return len(output) <= int(value)
        except (TypeError, ValueError):
            return None
    if kind in {"json", "json_schema"}:
        try:
            data = json.loads(output)
        except ValueError:
            return False
        if kind == "json":
            return True
        problems = schema_problems(spec.get("schema") or {}, data)
        return None if problems is None else not problems
    return None


def category(spec):
    default = "format" if spec.get("type") in {"json", "json_schema"} else "instruction"
    return spec.get("category", default)


def validate(config, output):
    spans = []
    for spec in config.get("validators") or []:
        passed = check(spec, output)
        payload = {"spec": spec, "passed": passed, "category": category(spec)}
        status = "error" if passed is False else "ok"
        spans.append(span("validation", status=status, payload=payload))
    return spans


async def run_agent(model, config, query, params=None, history=None, budget=None):
    agent = config.get("agent") or {}
    result = AgentResult()
    chunks = []
    if agent.get("type", "hosted") == "hosted":
        started = monotonic()
        chunks = retrieve(config, query)
        result.spans.append(
            span("retrieval", started, payload={"query": query}, chunks=chunks)
        )
    system = system_message(config, chunks)
    messages = [{"role": "system", "content": system}] if system else []
    messages += history or []
    messages.append({"role": "user", "content": query})
    tools = tool_definitions(config)
    schemas = [tool["schema"] for tool in tools.values()]
    params = {**(config.get("params") or {}), **(params or {})}
    for _ in range(MAX_STEPS):
        limit = budget.charge() if budget else None
        if limit:
            result.status = "error"
            result.spans.append(limit_span(limit))
            return result
        started = monotonic()
        try:
            reply = await model.complete(messages, schemas, params)
        except ModelError as exc:
            error = {"kind": exc.kind, "message": exc.message}
            payload = {"model": model.model, "error": error}
            result.status = "error"
            result.spans.append(span("llm", started, "error", payload))
            return result
        if budget:
            budget.record(messages, reply)
        payload = {
            "model": reply.model or model.model,
            "input": clip(json.dumps(messages, default=str)),
            "output": clip(reply.text),
            "finish_reason": reply.finish_reason,
            "usage": reply.usage,
        }
        result.spans.append(span("llm", started, payload=payload))
        if not reply.tool_calls:
            result.output = clip(reply.text, MAX_OUTPUT)
            result.spans += validate(config, result.output)
            return result
        messages.append(
            {"role": "assistant", "content": reply.text, "tool_calls": reply.tool_calls}
        )
        for call in reply.tool_calls:
            started = monotonic()
            content, status, payload = run_tool(tools, call)
            result.spans.append(span("tool", started, status, payload))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(content, default=str),
                }
            )
    result.status = "error"
    result.spans.append(
        span("llm", status="error", payload={"error": {"kind": "step_limit"}})
    )
    return result


async def save_trace(db, version_id, query, result):
    """Flush without committing; the session's redactor scrubs every value."""
    trace = Trace(
        app_version_id=version_id,
        input=clip(query, MAX_OUTPUT),
        output=result.output,
        status=result.status,
    )
    db.add(trace)
    await db.flush()
    chunks = []
    kept = result.spans[:MAX_SPANS]
    # Parents come before their children in the list, so they insert first.
    ids = {item["sdk_span_id"]: uuid4() for item in kept}
    for item in kept:
        values = {k: v for k, v in item.items() if k not in {"chunks", "parent"}}
        row = Span(
            id=ids[item["sdk_span_id"]],
            trace_id=trace.id,
            parent_span_id=ids.get(item.get("parent")),
            **values,
        )
        db.add(row)
        chunks += [RetrievedChunk(span_id=row.id, **chunk) for chunk in item["chunks"]]
    # No relationships are declared, so spans must reach the database first.
    await db.flush()
    db.add_all(chunks)
    await db.flush()
    return trace.id
