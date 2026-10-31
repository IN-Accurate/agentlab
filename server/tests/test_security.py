import asyncio
import logging
import socket
from types import SimpleNamespace

import pytest
import httpx
from cryptography.fernet import Fernet
from uvicorn.logging import AccessFormatter, DefaultFormatter

from app.errors import AppError
from app.config import Settings
from app.security import (
    RedactionFilter,
    Redactor,
    SafeTransport,
    create_credential,
    credential_metadata,
    decrypt_credential,
    revoke_credential,
    safe_http_client,
    rate_limit,
    RATE_LIMIT_MAX_KEYS,
    validate_url,
)
from app.schemas import reject_secrets
from app.store import CredentialStore, JobStore


def test_store_expiry_and_copy(monkeypatch):
    monkeypatch.setattr("app.store.monotonic", lambda: 10)
    store = CredentialStore()
    store.put("a", {"nested": {"value": 1}}, 5)
    result = store.get("a")
    result["nested"]["value"] = 9
    assert store.get("a")["nested"]["value"] == 1
    monkeypatch.setattr("app.store.monotonic", lambda: 15)
    assert store.get("a") is None


def test_job_update_preserves_expiry(monkeypatch):
    monkeypatch.setattr("app.store.monotonic", lambda: 10)
    store = JobStore()
    store.put("a", {"status": "queued"}, 5)
    assert store.update("a", status="completed")["status"] == "completed"
    monkeypatch.setattr("app.store.monotonic", lambda: 16)
    assert store.get("a") is None


def test_encrypted_credential_and_redaction(caplog):
    key = "placeholder-sensitive-value"
    settings = SimpleNamespace(app_encryption_key=Fernet.generate_key())
    store = CredentialStore()
    redactor = Redactor()
    metadata = create_credential(
        {"provider": "custom", "model": "example-model", "api_key": key},
        settings,
        store,
        redactor,
    )
    identifier = metadata["credential_id"]
    assert key not in repr(store.get(identifier))
    assert key not in repr(metadata)
    assert decrypt_credential(identifier, store, settings) == key
    logger = logging.getLogger("security-test")
    log_filter = RedactionFilter(redactor)
    logger.addFilter(log_filter)
    try:
        with caplog.at_level(logging.WARNING):
            logger.warning("upstream failed: %s", key)
            try:
                raise ValueError(key)
            except ValueError:
                logger.exception("request failed")
    finally:
        logger.removeFilter(log_filter)
    assert key not in caplog.text
    assert redactor.text("Bearer placeholder-token-7") == "Bearer [REDACTED]"
    store.delete(identifier)
    with pytest.raises(AppError):
        credential_metadata(identifier, store)


def test_uvicorn_formatters_keep_structured_fields():
    redactor = Redactor()
    log_filter = RedactionFilter(redactor)
    access = logging.LogRecord(
        "uvicorn.access",
        20,
        "",
        0,
        "%s - %s %s %s %s",
        ("127.0.0.1:8000", "GET", "/health", "1.1", 200),
        None,
    )
    assert log_filter.filter(access)
    assert "200 OK" in AccessFormatter(
        "%(message)s %(status_code)s", use_colors=False
    ).format(access)
    normal = logging.LogRecord(
        "uvicorn.error", 20, "", 0, "Started process [%d]", (1,), None
    )
    normal.color_message = "Started process [%d]"
    log_filter.filter(normal)
    assert "Started process [1]" in DefaultFormatter(
        "%(message)s", use_colors=True
    ).format(normal)


@pytest.mark.asyncio
async def test_url_rejects_private_mixed_dns(monkeypatch):
    async def resolve(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(AppError, match="restricted"):
        await validate_url("https://example.com/v1")


@pytest.mark.asyncio
async def test_url_public_and_local_opt_in(monkeypatch):
    async def resolve(host, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1" if host == "localhost" else "8.8.8.8", 443),
            )
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    assert await validate_url("https://example.com/v1") == ["8.8.8.8"]
    with pytest.raises(AppError):
        await validate_url("http://localhost/v1")
    assert await validate_url("http://localhost/v1", allow_local=True) == ["127.0.0.1"]
    with pytest.raises(AppError):
        await validate_url("https://example.com/v1?api_key=REPLACE_ME")


@pytest.mark.parametrize("origins", ["*", " * ", "http://localhost:3000, * "])
def test_cors_rejects_whitespace_wildcard(origins):
    with pytest.raises(ValueError, match="explicit CORS"):
        Settings(_env_file=None, cors_origins=origins)


def test_production_rejects_insecure_local_exception():
    with pytest.raises(ValueError, match="development-only"):
        Settings(
            _env_file=None,
            app_env="production",
            app_encryption_key=Fernet.generate_key().decode(),
            testlab_allow_insecure_local=True,
        )


@pytest.mark.parametrize("field", ["api_key", "apikey", "authorization", "%61pi_key"])
def test_access_log_scrubs_unregistered_query_credentials(field):
    key = "REPLACE_ME_UNREGISTERED"
    log_filter = RedactionFilter(Redactor())
    record = logging.LogRecord(
        "uvicorn.access",
        20,
        "",
        0,
        "%s - %s %s %s %s",
        (
            "127.0.0.1:8000",
            "GET",
            f"/v1/credentials?{field}={key}&limit=10",
            "1.1",
            400,
        ),
        None,
    )
    assert log_filter.filter(record)
    text = AccessFormatter("%(message)s %(status_code)s", use_colors=False).format(
        record
    )
    assert key not in text
    assert "[REDACTED]" in text
    assert "limit=10" in text
    assert "400 Bad Request" in text


def test_redactor_preserves_secret_lookalikes():
    redactor = Redactor()
    assert redactor.text("We use sk-learn for models.") == "We use sk-learn for models."
    assert (
        redactor.text("Bearer authentication is common.")
        == "Bearer authentication is common."
    )
    assert (
        redactor.text("Bearer tokens must be rotated.")
        == "Bearer tokens must be rotated."
    )
    assert redactor.text("key sk-abcdefghij1234567890 end") == "key [REDACTED] end"
    assert (
        redactor.text("auth Bearer abcdefghij1234567890!") == "auth Bearer [REDACTED]!"
    )


@pytest.mark.parametrize(
    "value",
    [
        "sk-learn",
        "Bearer tokens must be rotated",
        "the Bearer of news",
        "Bearer authentication",
    ],
)
def test_reject_secrets_ignores_lookalikes(value):
    reject_secrets(value)


@pytest.mark.parametrize(
    "value",
    [
        "sk-abcdefghij1234567890",
        "Bearer abcdefghij1234567890",
        {"nested": ["sk-abcdefghij1234567890"]},
    ],
)
def test_reject_secrets_blocks_realistic_lengths(value):
    with pytest.raises(ValueError, match="credential endpoint"):
        reject_secrets(value)


def test_settings_rejects_malformed_encryption_key():
    with pytest.raises(ValueError, match="Fernet key"):
        Settings(_env_file=None, app_encryption_key="not-a-valid-key")


def test_revoked_and_expired_credentials_release_fingerprints(monkeypatch):
    settings = SimpleNamespace(app_encryption_key=Fernet.generate_key())
    redactor = Redactor()
    store = CredentialStore(on_evict=redactor.discard)
    monkeypatch.setattr("app.store.monotonic", lambda: 10)
    first = create_credential(
        {
            "provider": "custom",
            "model": "m",
            "api_key": "first-secret-value",
            "ttl_seconds": 60,
        },
        settings,
        store,
        redactor,
    )["credential_id"]
    assert redactor.text("leaked first-secret-value") == "leaked [REDACTED]"
    revoke_credential(first, store)
    assert redactor.text("leaked first-secret-value") == "leaked first-secret-value"
    live = create_credential(
        {
            "provider": "custom",
            "model": "m",
            "api_key": "shared-secret-value",
            "ttl_seconds": 60,
        },
        settings,
        store,
        redactor,
    )["credential_id"]
    duplicate = create_credential(
        {
            "provider": "custom",
            "model": "m",
            "api_key": "shared-secret-value",
            "ttl_seconds": 60,
        },
        settings,
        store,
        redactor,
    )["credential_id"]
    revoke_credential(live, store)
    assert redactor.text("shared-secret-value here") == "[REDACTED] here"
    revoke_credential(duplicate, store)
    assert redactor.text("shared-secret-value here") == "shared-secret-value here"
    short = create_credential(
        {
            "provider": "custom",
            "model": "m",
            "api_key": "expiring-secret-1",
            "ttl_seconds": 60,
        },
        settings,
        store,
        redactor,
    )["credential_id"]
    monkeypatch.setattr("app.store.monotonic", lambda: 1000)
    assert store.get(short) is None
    assert redactor.text("expiring-secret-1") == "expiring-secret-1"


@pytest.mark.asyncio
async def test_safe_transport_validates_destination_at_call_time(monkeypatch):
    class RecordingTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.requests = []

        async def handle_async_request(self, request):
            self.requests.append(request)
            return httpx.Response(200, json={"ok": True})

    async def private(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]

    async def public(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    settings = SimpleNamespace(testlab_allow_insecure_local=False)
    inner = RecordingTransport()
    client = httpx.AsyncClient(transport=SafeTransport(settings, inner=inner))
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", private)
    with pytest.raises(AppError, match="restricted"):
        await client.get("https://example.com/v1")
    assert inner.requests == []
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", public)
    assert (await client.get("https://example.com/v1?api-version=1")).status_code == 200
    assert len(inner.requests) == 1
    with pytest.raises(AppError, match="HTTPS"):
        await client.get("http://example.com/v1")
    with pytest.raises(AppError, match="credentials"):
        await client.get("https://user@example.com/v1")
    await client.aclose()
    built = safe_http_client(settings)
    assert isinstance(built, httpx.AsyncClient)
    assert (built.timeout.connect, built.timeout.read) == (10.0, 300.0)
    await built.aclose()


def test_rate_limit_table_is_bounded():
    buckets = {(f"10.0.0.{i}", "credentials"): [0.0] for i in range(10005)}
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(rate_limits=buckets)),
        client=SimpleNamespace(host="10.9.9.9"),
        url=SimpleNamespace(path="/v1/credentials"),
    )
    rate_limit(request, 20)
    assert len(buckets) <= RATE_LIMIT_MAX_KEYS


def test_production_requires_api_key():
    with pytest.raises(ValueError, match="TESTLAB_API_KEY"):
        Settings(
            _env_file=None,
            app_env="production",
            app_encryption_key=Fernet.generate_key().decode(),
        )
    Settings(
        _env_file=None,
        app_env="production",
        app_encryption_key=Fernet.generate_key().decode(),
        testlab_api_key="k",
    )
