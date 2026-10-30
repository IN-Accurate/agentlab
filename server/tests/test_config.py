import httpx

from app.config import Settings
from app.main import create_app


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
