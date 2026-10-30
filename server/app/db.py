from fastapi import Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def connect(settings):
    url, args = settings.connection()
    engine = create_async_engine(
        url, connect_args=args, pool_pre_ping=True, hide_parameters=True
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def session(request: Request):
    async with request.app.state.sessions() as value, value.begin():
        yield value
