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
        value.info["redactor"] = request.app.state.redactor
        yield value


async def require(session, model, identifier):
    from uuid import UUID
    from app.errors import AppError

    row = await session.get(model, UUID(str(identifier)))
    if row is None:
        raise AppError("not_found", "Record not found", 404)
    return row


def record(row):
    import json
    from fastapi.encoders import jsonable_encoder

    return json.loads(
        json.dumps(
            jsonable_encoder(
                {c.key: getattr(row, c.key) for c in row.__table__.columns}
            )
        )
    )


async def page(session, model, filters=(), limit=50, cursor=None):
    from sqlalchemy import select

    query = select(model).where(*filters).order_by(model.id).limit(limit + 1)
    if cursor:
        query = query.where(model.id > cursor)
    rows = list((await session.scalars(query)).all())
    return {
        "items": [record(x) for x in rows[:limit]],
        "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
    }
