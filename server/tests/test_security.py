import logging
import socket
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from uvicorn.logging import AccessFormatter, DefaultFormatter

from app.errors import AppError
from app.security import (
    RedactionFilter,
    Redactor,
    create_credential,
    credential_metadata,
    decrypt_credential,
    validate_url,
)
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
    assert redactor.text("Bearer placeholder-token") == "Bearer [REDACTED]"
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

    import asyncio

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

    import asyncio

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    assert await validate_url("https://example.com/v1") == ["8.8.8.8"]
    with pytest.raises(AppError):
        await validate_url("http://localhost/v1")
    assert await validate_url("http://localhost/v1", allow_local=True) == ["127.0.0.1"]
    with pytest.raises(AppError):
        await validate_url("https://example.com/v1?api_key=REPLACE_ME")
