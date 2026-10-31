import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.main import create_app


@pytest.fixture
async def api():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable test database")
    parsed = make_url(url)
    if "test" not in (parsed.database or "").lower():
        pytest.fail("Use a dedicated database containing test in its name")
    connection = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"))
    schema = "test_" + uuid4().hex
    await connection.execute(f'CREATE SCHEMA "{schema}"')
    await connection.execute(f'SET search_path TO "{schema}"')
    await connection.execute(
        (Path(__file__).parents[2] / "db/testlab_schema.sql").read_text()
    )
    settings = Settings(_env_file=None, database_url=url)
    app = create_app(settings)
    await app.state.engine.dispose()
    engine_url, connect_args = settings.connection()
    engine = create_async_engine(
        engine_url, connect_args=connect_args, hide_parameters=True
    )

    # Neon drops extra startup parameters, so server_settings search_path
    # would silently land sessions in public. Set it in-band instead.
    @event.listens_for(engine.sync_engine, "connect")
    def _set_search_path(dbapi_connection, connection_record):
        dbapi_connection.await_(
            dbapi_connection._connection.execute(f'SET search_path TO "{schema}"')
        )

    app.state.engine = engine
    app.state.sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://testserver",
            ) as client,
        ):
            yield client, app
    finally:
        await engine.dispose()
        await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await connection.close()
