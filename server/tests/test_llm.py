import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.errors import AppError
from app.security import Redactor, create_credential
from app.services import llm
from app.services.retrieval import retrieve
from app.store import CredentialStore


def state_and_credential(fake=True):
    state = SimpleNamespace(
        settings=Settings(
            _env_file=None,
            testlab_fake_llm=fake,
            app_encryption_key=Fernet.generate_key().decode(),
        ),
        credential_store=CredentialStore(),
        redactor=Redactor(),
    )
    credential = create_credential(
        {
            "provider": "custom",
            "base_url": "https://example.com/v1",
            "model": "example-model",
            "judge_model": "example-judge",
            "api_key": "REPLACE_ME",
        },
        state.settings,
        state.credential_store,
        state.redactor,
    )
    return state, credential["credential_id"]


async def test_fake_refund_context_and_scripted_judge():
    state, credential = state_and_credential()
    bad = await llm.complete(
        state, credential, [{"role": "user", "content": "What is the refund policy?"}]
    )
    good = await llm.complete(
        state,
        credential,
        [
            {"role": "system", "content": "Context: Refunds within 30 days."},
            {"role": "user", "content": "What is the refund policy?"},
        ],
    )
    assert "60 days" in bad["content"]
    assert "30 days" in good["content"]
    result = await llm.complete(
        state,
        credential,
        [
            {"role": "system", "content": "TASK:judge"},
            {
                "role": "user",
                "content": json.dumps(
                    {"output": bad["content"], "expected": {"reference": "30 days"}}
                ),
            },
        ],
        judge=True,
    )
    assert json.loads(result["content"])["passed"] is False
    state.fake_llm = llm.FakeLLMClient(["malformed", {"passed": True}])
    assert (await llm.complete(state, credential, []))["content"] == "malformed"
    assert json.loads((await llm.complete(state, credential, []))["content"])["passed"]


def test_keyword_retrieval_filters_ranking_and_zero_top_k():
    config = {
        "knowledge_base": [
            {"id": "policy", "text": "Refund receipt policy", "source": "policy"},
            {"id": "other", "text": "Shipping times", "source": "shipping"},
        ],
        "retrieval": {"top_k": 1, "min_score": 0.1},
    }
    found = retrieve(config, "refund policy")
    assert found[0]["id"] == "policy"
    assert found[0]["rank"] == 1
    assert "score" not in config["knowledge_base"][0]
    config["retrieval"]["filters"] = {"source": "shipping"}
    assert retrieve(config, "refund policy") == []
    config["retrieval"]["top_k"] = 0
    assert retrieve(config, "shipping") == []


async def test_http_pins_dns_retries_and_redacts(monkeypatch):
    state, credential = state_and_credential(False)
    calls = []

    async def validate(*args):
        return ["8.8.8.8"]

    def handle(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429, json={"error": "retry"})
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        assert json.loads(request.content)["model"] == "example-judge"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "Accidental REPLACE_ME echo"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 5},
            },
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(llm, "validate_url", validate)
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    result = await llm.complete(state, credential, [], judge=True)
    assert len(calls) == 3
    assert "REPLACE_ME" not in result["content"]
    assert result["usage"]["total_tokens"] == 5


async def test_provider_response_size_cap(monkeypatch):
    state, credential = state_and_credential(False)

    async def validate(*args):
        return ["8.8.8.8"]

    client_type = httpx.AsyncClient
    monkeypatch.setattr(llm, "validate_url", validate)
    monkeypatch.setattr(llm, "MAX_RESPONSE_BYTES", 20)
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=b"x" * 21)
            ),
            **kwargs,
        ),
    )
    with pytest.raises(AppError) as error:
        await llm.complete(state, credential, [])
    assert error.value.code == "provider_response_too_large"


async def test_provider_stream_redacts_split_key(monkeypatch):
    state, credential = state_and_credential(False)

    async def validate(*args):
        return ["8.8.8.8"]

    body = "".join(
        "data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n"
        for part in ["REPLACE_", "ME"]
    )
    client_type = httpx.AsyncClient
    monkeypatch.setattr(llm, "validate_url", validate)
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, text=body + "data: [DONE]\n\n")
            ),
            **kwargs,
        ),
    )
    value = "".join([part async for part in llm.stream(state, credential, [])])
    assert value == "[REDACTED]"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": None},
        {"choices": [{"message": {"content": ["bad-shape"]}}]},
    ],
)
async def test_malformed_provider_response_becomes_safe_error(monkeypatch, payload):
    state, credential = state_and_credential(False)

    async def validate(*args):
        return ["8.8.8.8"]

    client_type = httpx.AsyncClient
    monkeypatch.setattr(llm, "validate_url", validate)
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
            **kwargs,
        ),
    )
    with pytest.raises(AppError) as error:
        await llm.complete(state, credential, [])
    assert error.value.code == "provider_unavailable"


async def test_redirect_rejected_without_forwarding_key(monkeypatch):
    state, credential = state_and_credential(False)
    calls = []

    async def validate(*args):
        return ["8.8.8.8"]

    def handle(request):
        calls.append(request)
        return httpx.Response(
            307, headers={"Location": "https://other.example.com/steal"}
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(llm, "validate_url", validate)
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    with pytest.raises(AppError) as error:
        await llm.complete(state, credential, [])
    assert error.value.code == "provider_error"
    assert len(calls) == 1
