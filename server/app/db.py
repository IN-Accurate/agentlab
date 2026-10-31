import json
from datetime import datetime
from uuid import UUID

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.errors import AppError


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
    row = await session.get(model, UUID(str(identifier)))
    if row is None:
        raise AppError("not_found", "Record not found", 404)
    return row


async def require_live(session, model, identifier):
    row = await require(session, model, identifier)
    if getattr(row, "archived_at", None) is not None:
        raise AppError("not_found", "Record not found", 404)
    return row


def record(row):
    return json.loads(
        json.dumps(
            jsonable_encoder(
                {c.key: getattr(row, c.key) for c in row.__table__.columns}
            )
        )
    )


def encode_cursor(created_at, identifier):
    return f"{created_at.isoformat()}|{identifier}"


def decode_cursor(cursor):
    try:
        stamped, identifier = cursor.rsplit("|", 1)
        return datetime.fromisoformat(stamped), UUID(identifier)
    except (ValueError, AttributeError, TypeError) as exc:
        raise AppError("invalid_cursor", "Cursor is malformed", 422) from exc


async def page(session, model, filters=(), limit=50, cursor=None):
    query = (
        select(model)
        .where(*filters)
        .order_by(model.created_at, model.id)
        .limit(limit + 1)
    )
    if cursor:
        stamped, identifier = decode_cursor(cursor)
        query = query.where(
            (model.created_at > stamped)
            | ((model.created_at == stamped) & (model.id > identifier))
        )
    rows = list((await session.scalars(query)).all())
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last.created_at, last.id)
    else:
        next_cursor = None
    return {
        "items": [record(x) for x in rows[:limit]],
        "next_cursor": next_cursor,
    }
