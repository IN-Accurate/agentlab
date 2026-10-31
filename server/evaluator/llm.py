import asyncio
import json
from dataclasses import dataclass, field
from time import monotonic

import httpx

from app.errors import AppError

PROVIDER_URLS = {"openrouter": "https://openrouter.ai/api/v1"}
MAX_RESPONSE_BYTES = 5_000_000
REQUEST_TIMEOUT = 120.0
RETRIES = 2
BACKOFF_SECONDS = 1.0
MAX_RETRY_AFTER = 10.0
RETRYABLE = {408, 429, 500, 502, 503, 504}
KIND_MARKERS = (
    (
        "context_overflow",
        ("context_length", "maximum context", "context window", "too many tokens"),
    ),
    ("budget", ("insufficient", "quota", "credit", "billing")),
    ("safety", ("content_filter", "content policy", "moderation", "safety")),
)


class ModelError(AppError):
    def __init__(self, kind, message, status=None):
        super().__init__("model_error", message, 502)
        self.kind = kind


def classify(status, body):
    text = body.lower()
    for kind, markers in KIND_MARKERS:
        if any(marker in text for marker in markers):
            return kind
    if status == 402:
        return "budget"
    if status in {401, 403}:
        return "auth"
    return "provider"


@dataclass
class Completion:
    text: str
    finish_reason: str | None = None
    model: str | None = None
    latency_ms: int = 0
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)


class ModelClient:
    """One OpenAI-compatible interface for Muse Spark and Llama hosts."""

    def __init__(self, http, api_key, base_url, model):
        self.http = http
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model

    @classmethod
    def from_context(cls, context, credential_id, agent=None, judge=False):
        credentials = context["credentials"]
        meta = credentials["metadata"](credential_id)
        agent = agent or {}
        base_url = (
            agent.get("base_url")
            or meta.get("base_url")
            or PROVIDER_URLS.get(meta["provider"])
        )
        if not base_url:
            raise AppError(
                "model_not_configured",
                "Set base_url on the credential or agent for this provider",
                422,
            )
        model = agent.get("model") or (judge and meta.get("judge_model"))
        return cls(
            context["http_client"],
            credentials["decrypt_for_call"](credential_id),
            base_url,
            model or meta["model"],
        )

    def body(self, messages, tools=None, params=None):
        body = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
        for key, value in (params or {}).items():
            if key in {"temperature", "max_tokens", "seed"} and value is not None:
                body[key] = value
        return body

    async def complete(self, messages, tools=None, params=None):
        body = self.body(messages, tools, params)
        started = monotonic()
        for attempt in range(RETRIES + 1):
            retry_after = None
            try:
                async with self.http.stream(
                    "POST",
                    self.url,
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=REQUEST_TIMEOUT,
                ) as response:
                    raw = await read_capped(response)
                    retry_after = response.headers.get("retry-after")
                if response.status_code == 200:
                    return parse_completion(raw, started)
                error = failure(response.status_code, raw)
                if response.status_code not in RETRYABLE:
                    raise error
            except httpx.DecodingError as exc:
                raise ModelError("provider", "Malformed model response") from exc
            except httpx.TimeoutException:
                error = ModelError("timeout", "Model call timed out")
            except httpx.TransportError:
                error = ModelError("transport", "Model endpoint unreachable")
            if attempt == RETRIES:
                raise error
            await asyncio.sleep(delay(attempt, retry_after))


async def read_capped(response):
    raw = b""
    async for chunk in response.aiter_bytes():
        raw += chunk
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelError("provider", "Model response exceeded limit")
    return raw


def delay(attempt, retry_after):
    try:
        return min(float(retry_after), MAX_RETRY_AFTER)
    except (TypeError, ValueError):
        return BACKOFF_SECONDS * 2**attempt


def failure(status, raw):
    body = raw.decode("utf-8", "replace")
    try:
        message = json.loads(body)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = body
    return ModelError(classify(status, body), str(message)[:500], status)


def parse_completion(raw, started):
    try:
        data = json.loads(raw)
        choice = data["choices"][0]
        message = choice["message"]
        return Completion(
            text=message.get("content") or "",
            finish_reason=choice.get("finish_reason"),
            model=data.get("model"),
            latency_ms=int((monotonic() - started) * 1000),
            tool_calls=message.get("tool_calls") or [],
            usage=data.get("usage") or {},
        )
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ModelError("provider", "Malformed model response") from exc
