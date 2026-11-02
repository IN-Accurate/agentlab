import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.models import Base

config = context.config
target_metadata = Base.metadata


def do_run_migrations(connection):
    schema = context.get_x_argument(as_dictionary=True).get("schema")
    if schema:
        quoted = connection.dialect.identifier_preparer.quote_schema(schema)
        connection.exec_driver_sql(f"SET LOCAL search_path TO {quoted}")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=schema,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    settings = Settings()
    url, connect_args = settings.connection()
    engine = create_async_engine(
        url,
        connect_args=connect_args,
        poolclass=pool.NullPool,
        hide_parameters=True,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = config.attributes.get("connection")
    if connection is None:
        asyncio.run(run_async_migrations())
    else:
        do_run_migrations(connection)
