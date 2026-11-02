import logging
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import BackgroundTasks
from sqlalchemy import select, text, update

from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.models import SavedCredential
from app.routers.jobs import submit
from app.security import RedactionFilter, decrypt_credential
from app.services.chat import events
from app.services.credentials import release, scope
from app.services.evaluator import context, execute


async def test_reusing_keyless_settings_keeps_credentials_ephemeral():
    settings = Settings(_env_file=None, app_encryption_key=None)
    first = create_app(settings)
    second = create_app(settings)
    try:
        assert settings.app_encryption_key is None
        assert first.state.saved_credentials is None
        assert second.state.saved_credentials is None
        assert (
            first.state.settings.app_encryption_key
            != second.state.settings.app_encryption_key
        )
    finally:
        await first.state.engine.dispose()
        await second.state.engine.dispose()


@pytest.fixture
async def durable_apps(api):
    _, existing = api
    key = Fernet.generate_key().decode()
    async with AsyncExitStack() as stack:

        async def start(encryption_key=key):
            app = create_app(
                Settings(
                    _env_file=None,
                    database_url=existing.state.settings.database_url,
                    app_encryption_key=encryption_key,
                )
            )
            await app.state.engine.dispose()
            app.state.engine = existing.state.engine
            app.state.sessions = existing.state.sessions
            await stack.enter_async_context(app.router.lifespan_context(app))
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://testserver",
                )
            )
            return client, app

        yield start


async def create_key(client, key="provider-key-with-no-standard-prefix", **kwargs):
    response = await client.post(
        "/v1/credentials",
        json={
            "provider": "custom",
            "model": "test-model",
            "api_key": key,
            **kwargs,
        },
    )
    assert response.status_code == 201, response.text
    assert key not in response.text
    return response.json()["credential_id"]


async def test_saved_key_survives_restart_and_redacts_rows_and_logs(
    durable_apps, caplog
):
    first, old_app = await durable_apps()
    raw = "provider-key-with-no-standard-prefix"
    identifier = await create_key(first, raw)
    async with old_app.state.sessions() as db:
        rows = (await db.execute(select(SavedCredential.__table__))).mappings().all()
    assert len(rows) == 1
    assert raw not in repr(rows)
    assert rows[0]["ciphertext"] != raw
    assert (
        Fernet(old_app.state.settings.app_encryption_key)
        .decrypt(rows[0]["ciphertext"].encode())
        .decode()
        == raw
    )

    # End the old process's in-memory lifetime before constructing a new app.
    old_app.state.credential_store.delete(identifier)
    second, new_app = await durable_apps()
    assert new_app.state.credential_store is not old_app.state.credential_store
    with caplog.at_level(logging.WARNING):
        logging.getLogger("saved-key-test").warning("upstream echoed %s", raw)
    assert raw not in caplog.text
    assert "[REDACTED]" in caplog.text
    response = await second.get(f"/v1/credentials/{identifier}")
    assert response.status_code == 200
    assert response.json()["credential_id"] == identifier
    hydrated = await scope(new_app.state, {identifier})
    try:
        credentials = context(None, new_app.state, hydrated)["credentials"]
        assert credentials["metadata"](identifier)["model"] == "test-model"
        assert credentials["decrypt_for_call"](identifier) == raw
        with pytest.raises(AppError, match="Credential not supplied"):
            credentials["decrypt_for_call"]("another-credential")
    finally:
        release(hydrated)


async def test_saved_revocation_is_checked_across_workers(durable_apps):
    first, app_a = await durable_apps()
    identifier = await create_key(first)
    second, app_b = await durable_apps()
    assert (await second.get(f"/v1/credentials/{identifier}")).status_code == 200
    assert app_b.state.credential_store.get(identifier) is not None
    assert (await first.delete(f"/v1/credentials/{identifier}")).status_code == 204
    assert (await second.get(f"/v1/credentials/{identifier}")).status_code == 404
    with pytest.raises(AppError, match="expired, revoked or unknown"):
        await scope(app_b.state, {identifier})
    async with app_a.state.sessions() as db:
        assert await db.scalar(select(SavedCredential.id)) is None


async def test_saved_expiry_uses_persisted_clock_after_restart(durable_apps):
    first, app = await durable_apps()
    identifier = await create_key(first)
    async with app.state.sessions() as db, db.begin():
        await db.execute(
            update(SavedCredential.__table__)
            .where(SavedCredential.id == UUID(identifier))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert (await first.get(f"/v1/credentials/{identifier}")).status_code == 404
    second, restarted = await durable_apps()
    assert (await second.get(f"/v1/credentials/{identifier}")).status_code == 404
    assert restarted.state.credential_store.get(identifier) is None


async def test_wrong_saved_key_returns_sanitized_error(durable_apps):
    first, _ = await durable_apps()
    raw = "nonstandard-sensitive-provider-key"
    identifier = await create_key(first, raw)
    second, wrong = await durable_apps(Fernet.generate_key().decode())
    hydrated = await scope(wrong.state, {identifier})
    try:
        with pytest.raises(AppError) as caught:
            decrypt_credential(identifier, hydrated.store, wrong.state.settings)
        assert caught.value.code == "credential_unavailable"
        assert caught.value.status == 422
        assert raw not in str(caught.value)
        assert raw not in repr(wrong.state.credential_store.get(identifier))
    finally:
        release(hydrated)

    class DecryptingEvaluator:
        def execute(self, payload, evaluator_context):
            evaluator_context["credentials"]["decrypt_for_call"](identifier)
            return {"ok": True}

    wrong.state.evaluator = DecryptingEvaluator()
    project = (await second.post("/v1/projects", json={"name": "saved key"})).json()
    response = await second.post(
        f"/v1/projects/{project['id']}/agent/test-connection",
        json={"credential_id": identifier, "agent": {"type": "openai_compatible"}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "credential_unavailable"
    assert raw not in response.text


async def test_concurrent_scopes_keep_keys_and_redaction_separate(durable_apps):
    client, app = await durable_apps()
    raw_a, raw_b = "first-unusual-provider-key", "second-unusual-provider-key"
    a = await create_key(client, raw_a)
    b = await create_key(client, raw_b)
    first = await scope(app.state, {a})
    second = await scope(app.state, {b})
    try:
        assert decrypt_credential(a, first.store, app.state.settings) == raw_a
        assert decrypt_credential(b, second.store, app.state.settings) == raw_b
        with pytest.raises(AppError):
            decrypt_credential(b, first.store, app.state.settings)
        await app.state.saved_credentials.revoke(a)
        credentials = context(None, app.state, first)["credentials"]
        for callback in credentials.values():
            with pytest.raises(AppError) as revoked:
                callback(a)
            assert revoked.value.code == "credential_expired"
            assert revoked.value.status == 404
        # In-flight work retains redaction until its operation is cleaned up.
        assert app.state.redactor.text(raw_a) == "[REDACTED]"
    finally:
        release(first)
        release(second)
    assert app.state.redactor.text(raw_a) == raw_a


async def test_no_stable_key_keeps_existing_ephemeral_flow(api):
    client, app = api
    assert app.state.saved_credentials is None
    identifier = await create_key(client)
    assert app.state.credential_store.get(identifier) is not None
    async with app.state.sessions() as db:
        assert await db.scalar(select(SavedCredential.id)) is None


async def test_disconnected_chat_closes_upstream_before_releasing_redaction(
    durable_apps,
):
    client, app = await durable_apps()
    raw = "chat-disconnect-sensitive-provider-key"
    identifier = await create_key(client, raw)
    closed = []

    class StreamingEvaluator:
        async def stream_chat(self, payload, evaluator_context):
            try:
                yield {"event": "step.started", "data": {}}
            finally:
                closed.append(app.state.redactor.text(raw))

    app.state.evaluator = StreamingEvaluator()
    hydrated = await scope(app.state, {identifier})
    async with app.state.sessions() as db:
        stream = events(app.state, db, ({}, hydrated))
        assert (await anext(stream))["event"] == "step.started"
        await app.state.saved_credentials.revoke(identifier)
        await stream.aclose()
    assert closed == ["[REDACTED]"]
    assert app.state.redactor.text(raw) == raw


async def test_saved_metadata_cannot_persist_the_raw_key(durable_apps, monkeypatch):
    async def allow_url(*args):
        return None

    monkeypatch.setattr("app.routers.credentials.validate_url", allow_url)
    client, app = await durable_apps()
    raw = "secret-echoed-in-metadata"
    identifier = await create_key(
        client, raw, model=raw, judge_model=raw, base_url=f"https://example.com/{raw}"
    )
    response = await client.get(f"/v1/credentials/{identifier}")
    assert raw not in response.text
    assert response.json()["model"] == "[REDACTED]"
    async with app.state.sessions() as db:
        row = (await db.execute(select(SavedCredential.__table__))).mappings().one()
    assert raw not in repr(row)
    assert row["base_url"] == "https://example.com/[REDACTED]"


async def test_failed_save_leaves_no_cached_credential(durable_apps):
    client, app = await durable_apps()
    async with app.state.sessions() as db, db.begin():
        await db.execute(
            text(
                "ALTER TABLE saved_credentials ADD CONSTRAINT reject_test_model "
                "CHECK (model <> 'fail-to-save')"
            )
        )
    raw = "failed-save-sensitive-provider-key"
    response = await client.post(
        "/v1/credentials",
        json={
            "provider": "custom",
            "model": "fail-to-save",
            "api_key": raw,
        },
    )
    assert response.status_code == 409
    assert raw not in response.text
    assert app.state.credential_store._items == {}
    assert app.state.redactor.text(raw) == raw
    async with app.state.sessions() as db:
        assert await db.scalar(select(SavedCredential.id)) is None


async def test_revoked_inflight_failure_stays_redacted_in_job_logs(
    durable_apps, caplog
):
    client, app = await durable_apps()
    raw = "inflight-key-without-recognizable-prefix"
    identifier = await create_key(client, raw)

    class FailingEvaluator:
        async def execute(self, payload, evaluator_context):
            assert (
                evaluator_context["credentials"]["decrypt_for_call"](identifier) == raw
            )
            await app.state.saved_credentials.revoke(identifier)
            raise RuntimeError(raw)

    app.state.evaluator = FailingEvaluator()
    prepared = (
        {"operation": "test_connection", "project_id": str(uuid4())},
        {identifier},
    )
    tasks = BackgroundTasks()

    async def operation(db, progress):
        return await execute(db, app.state, prepared, progress)

    with caplog.at_level(logging.ERROR):
        job = await submit(app.state, tasks, operation)
        await tasks()
    assert app.state.job_store.get(job["job_id"])["status"] == "failed"
    assert "[REDACTED]" in caplog.text
    assert raw not in caplog.text


async def test_revoked_inflight_failure_stays_redacted_in_server_logs(durable_apps):
    client, app = await durable_apps()
    raw = "server-failure-sensitive-provider-key"
    identifier = await create_key(client, raw)

    class FailingEvaluator:
        async def execute(self, payload, evaluator_context):
            await app.state.saved_credentials.revoke(identifier)
            raise RuntimeError(raw)

    app.state.evaluator = FailingEvaluator()
    prepared = (
        {"operation": "test_connection", "project_id": str(uuid4())},
        {identifier},
    )
    async with app.state.sessions() as db:
        with pytest.raises(RuntimeError) as caught:
            await execute(db, app.state, prepared)
    assert app.state.redactor.text(raw) == raw
    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        "",
        0,
        "Exception in ASGI application",
        (),
        (type(caught.value), caught.value, caught.value.__traceback__),
    )
    RedactionFilter(app.state.redactor).filter(record)
    formatted = logging.Formatter().format(record)
    assert "[REDACTED]" in formatted
    assert raw not in formatted
