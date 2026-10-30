import asyncio
import json
import re
from collections import deque
from time import perf_counter

import httpx

from app.errors import AppError
from app.security import credential_record, decrypt_credential, validate_url

MAX_RESPONSE_BYTES = 2_000_000


class FakeLLMClient:
    def __init__(self, responses=None):
        self.responses = deque(responses or [])
        self.calls = []

    async def complete(self, messages, params=None, tools=None):
        self.calls.append(
            {"messages": messages, "params": params or {}, "tools": tools or []}
        )
        if self.responses:
            value = self.responses.popleft()
            if isinstance(value, Exception):
                raise value
            if isinstance(value, dict) and "content" in value:
                return value
            content = value if isinstance(value, str) else json.dumps(value)
        else:
            content = self.answer(messages)
        return {
            "content": content,
            "tool_calls": [],
            "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
            "finish_reason": "stop",
            "latency_ms": 1,
        }

    def answer(self, messages):
        combined = "\n".join(str(m.get("content", "")) for m in messages)
        lower = combined.lower()
        payload = {}
        for message in reversed(messages):
            try:
                value = json.loads(message.get("content", ""))
                if isinstance(value, dict):
                    payload = value
                    break
            except (ValueError, TypeError):
                continue
        if "task:intent" in lower:
            user = str(payload.get("message", messages[-1].get("content", ""))).lower()
            intent = next(
                (
                    v
                    for k, v in [
                        ("verify", "verify_fix"),
                        ("fix", "propose_fix"),
                        ("why", "explain_failure"),
                        ("progress", "show_progress"),
                        ("run", "run_evaluation"),
                    ]
                    if k in user
                ),
                "general_question",
            )
            return json.dumps({"intent": intent})
        if "task:seed" in lower:
            count = min(int(payload.get("count", 3)), 20)
            return json.dumps(
                {
                    "items": [
                        {
                            "input": "What is the refund policy?",
                            "expected": {
                                "reference": "30 days",
                                "source": "refund-policy",
                            },
                        }
                        for _ in range(count)
                    ]
                }
            )
        if "task:diagnose" in lower:
            return json.dumps(
                {
                    "rule": "judge",
                    "confidence": 0.5,
                    "evidence": {"reason": "Scripted fallback diagnosis"},
                }
            )
        if "task:judge" in lower:
            output = str(
                payload.get(
                    "output",
                    payload.get("answer", payload.get("trace", {}).get("output", "")),
                )
            )
            if not output:
                output = next(
                    (
                        str(m.get("content", ""))
                        for m in reversed(messages)
                        if m.get("role") == "assistant"
                    ),
                    "",
                )
            expected = payload.get("expected", {})
            reference = (
                str(expected.get("reference", ""))
                if isinstance(expected, dict)
                else str(expected)
            )
            passed = bool(output) and "60 days" not in output.lower()
            if reference:
                passed = reference.lower() in output.lower()
            return json.dumps(
                {
                    "passed": passed,
                    "reasoning": "Scripted comparison to supplied reference",
                    "unsupported_claims": [] if passed else [output],
                }
            )
        system = "\n".join(
            str(m.get("content", "")) for m in messages if m.get("role") == "system"
        )
        user = str(messages[-1].get("content", "")) if messages else ""
        if "refund" in lower:
            if "30" in system or "30" in user and "context" in user.lower():
                return "Refunds are available within 30 days with proof of purchase."
            return "Refunds are available within 60 days."
        match = re.search(
            r"(?:reply|respond|say)(?: only| exactly)?(?: with)?[ :]+[\"']?([^\n\"']+)",
            system,
            re.I,
        )
        if match:
            return match.group(1).rstrip(".")
        return "Hello. This is a deterministic test response."


def _fake(state):
    client = getattr(state, "fake_llm", None)
    if client is None:
        client = FakeLLMClient()
        state.fake_llm = client
    return client


async def _request_data(
    state, credential_id, messages, params, tools, judge, agent, streaming
):
    record = credential_record(str(credential_id), state.credential_store)
    agent = agent or {}
    base = agent.get("base_url") or record.get("base_url")
    if not base and record["provider"] == "openrouter":
        base = "https://openrouter.ai/api/v1"
    if not base:
        raise AppError("missing_base_url", "This provider requires a base URL", 422)
    addresses = await validate_url(base, state.settings.testlab_allow_insecure_local)
    url = httpx.URL(base.rstrip("/") + "/chat/completions")
    # Pin the validated IP while preserving certificate hostname verification.
    pinned = url.copy_with(host=addresses[0])
    model = record.get("judge_model") if judge else agent.get("model")
    model = model or record["model"]
    headers = {"Host": url.netloc.decode(), "Content-Type": "application/json"}
    if record["provider"] == "openrouter":
        if state.settings.openrouter_referer:
            headers["HTTP-Referer"] = state.settings.openrouter_referer
        if state.settings.openrouter_title:
            headers["X-Title"] = state.settings.openrouter_title
    headers["Authorization"] = "Bearer " + decrypt_credential(
        str(credential_id), state.credential_store, state.settings
    )
    values = (
        params.model_dump(exclude_none=True)
        if hasattr(params, "model_dump")
        else dict(params or {})
    )
    body = {"model": model, "messages": messages, "stream": streaming, **values}
    if tools:
        body["tools"] = tools
    return pinned, headers, body, {"sni_hostname": url.host}


async def _read(response):
    chunks = bytearray()
    async for chunk in response.aiter_bytes():
        chunks.extend(chunk)
        if len(chunks) > MAX_RESPONSE_BYTES:
            raise AppError(
                "provider_response_too_large",
                "Provider response exceeds the limit",
                502,
            )
    return bytes(chunks)


def _provider_error(state, response, body):
    details = state.redactor.text(body.decode(errors="replace")[:2000])
    return AppError(
        "provider_error",
        "Model provider rejected the request",
        502,
        {"provider_status": response.status_code, "message": details},
    )


async def complete(
    state, credential_id, messages, params=None, tools=None, judge=False, agent=None
):
    if state.settings.testlab_fake_llm:
        credential_record(str(credential_id), state.credential_store)
        return state.redactor.scrub(
            await _fake(state).complete(messages, params, tools)
        )
    started = perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=20),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for attempt in range(3):
                url, headers, body, extensions = await _request_data(
                    state, credential_id, messages, params, tools, judge, agent, False
                )
                async with client.stream(
                    "POST", url, headers=headers, json=body, extensions=extensions
                ) as response:
                    raw = await _read(response)
                    retry = response.status_code == 429 or response.status_code >= 500
                    if retry and attempt < 2:
                        await asyncio.sleep(0.25 * 2**attempt)
                        continue
                    if response.is_error or response.is_redirect:
                        raise _provider_error(state, response, raw)
                data = json.loads(raw)
                choice = data["choices"][0]
                return state.redactor.scrub(
                    {
                        "content": choice["message"].get("content") or "",
                        "tool_calls": choice["message"].get("tool_calls", []),
                        "usage": data.get("usage", {}),
                        "finish_reason": choice.get("finish_reason"),
                        "latency_ms": round((perf_counter() - started) * 1000),
                    }
                )
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise AppError(
            "provider_unavailable", "Could not obtain a valid model response", 502
        ) from exc


async def stream(
    state, credential_id, messages, params=None, tools=None, judge=False, agent=None
):
    if state.settings.testlab_fake_llm:
        result = await complete(
            state, credential_id, messages, params, tools, judge, agent
        )
        for word in result["content"].split(" "):
            yield word + " "
        return
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=20),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for attempt in range(3):
                url, headers, body, extensions = await _request_data(
                    state, credential_id, messages, params, tools, judge, agent, True
                )
                async with client.stream(
                    "POST", url, headers=headers, json=body, extensions=extensions
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        raw = await _read(response)
                        if attempt < 2:
                            await asyncio.sleep(0.25 * 2**attempt)
                            continue
                        raise _provider_error(state, response, raw)
                    if response.is_error or response.is_redirect:
                        raise _provider_error(state, response, await _read(response))
                    pending = b""
                    size = 0
                    text = ""
                    # Buffer the bounded response to catch keys split across deltas.
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise AppError(
                                "provider_response_too_large",
                                "Provider stream exceeds the limit",
                                502,
                            )
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            if not line.startswith(b"data:"):
                                continue
                            value = line[5:].strip()
                            if value == b"[DONE]":
                                continue
                            event = json.loads(value)
                            if event.get("choices"):
                                text += (
                                    event["choices"][0].get("delta", {}).get("content")
                                    or ""
                                )
                    cleaned = state.redactor.text(text)
                    for start in range(0, len(cleaned), 128):
                        yield cleaned[start : start + 128]
                    return
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise AppError(
            "provider_unavailable", "Could not read the model stream", 502
        ) from exc
