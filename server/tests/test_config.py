import httpx
from fastapi import BackgroundTasks

from app.config import Settings
from app.db import session as session_dependency
from app.main import create_app
from app.routers.jobs import submit


def test_neon_url():
    settings = Settings(
        _env_file=None,
        database_url="postgresql://example:REPLACE_ME@ep-example-pooler.example.com/testlab_test?sslmode=require&channel_binding=require",
    )
    url, args = settings.connection()
    assert url.drivername == "postgresql+asyncpg"
    assert "sslmode" not in url.query
    assert args["statement_cache_size"] == 0
    assert args["ssl"].check_hostname


async def test_health():
    app = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}


async def test_database_connection(api):
    from sqlalchemy import text

    _, app = api
    async with app.state.sessions() as session:
        assert await session.scalar(text("SELECT 1")) == 1


async def test_non_streaming_routes_use_function_scope():
    app = create_app(Settings(_env_file=None))
    checked = 0
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        stack = list(dependant.dependencies)
        while stack:
            current = stack.pop()
            stack.extend(current.dependencies)
            if current.call is session_dependency:
                checked += 1
                expected = "request" if route.path.endswith("/chat") else "function"
                assert current.scope == expected, route.path
    assert checked > 40


async def test_oversized_body_rejected():
    app = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as client:
        response = await client.post("/v1/projects", content=b"x" * (10_000_001))
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "body_too_large"


async def test_job_events_end_cleanly_when_job_expires():
    app = create_app(Settings(_env_file=None))
    accepted = await submit(app.state, BackgroundTasks(), lambda db, progress: None)
    real_get = app.state.job_store.get
    calls = 0

    def flaky(key):
        nonlocal calls
        calls += 1
        return real_get(key) if calls == 1 else None

    app.state.job_store.get = flaky
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/v1/jobs/{accepted['job_id']}/events")
        assert response.status_code == 200
        assert "event: error" in response.text
        assert "job_not_found" in response.text
