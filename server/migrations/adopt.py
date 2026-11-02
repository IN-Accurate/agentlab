"""Verify an existing SQL-built database before recording Alembic history."""

import argparse
import asyncio
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from migrations.catalog import schema_snapshot

BASELINE_REVISION = "0001_baseline"
HEAD_REVISION = "0002_saved_credentials"


def migration_config(connection=None, schema=None):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
    if schema:
        config.cmd_opts = argparse.Namespace(x=[f"schema={schema}"])
    return config


def adopt_existing(connection, schema="public"):
    """Stamp only a schema exactly matching a known frozen revision.

    The caller owns the transaction. All reference objects live in a disposable
    scratch schema and are dropped before the stamp; existing records are never
    updated. Any failure rolls the transaction back, including the scratch schema.
    """
    inspector = inspect(connection)
    if schema not in inspector.get_schema_names():
        raise RuntimeError("Target schema does not exist; use alembic upgrade head")
    if "alembic_version" in inspector.get_table_names(schema=schema):
        raise RuntimeError("Database already has Alembic history; use alembic upgrade")
    actual = schema_snapshot(connection, schema)
    scratch = "agentlab_adopt_" + uuid4().hex
    quote = connection.dialect.identifier_preparer.quote_schema
    connection.exec_driver_sql(f"CREATE SCHEMA {quote(scratch)}")
    expected_config = migration_config(connection, scratch)
    command.upgrade(expected_config, BASELINE_REVISION)
    revision = BASELINE_REVISION
    if actual != schema_snapshot(connection, scratch):
        command.upgrade(expected_config, HEAD_REVISION)
        revision = HEAD_REVISION
        if actual != schema_snapshot(connection, scratch):
            raise RuntimeError(
                "Existing schema differs from both known SQL bootstrap schemas; "
                "no history was recorded. Reconcile drift on a database copy first."
            )
    connection.exec_driver_sql(f"DROP SCHEMA {quote(scratch)} CASCADE")
    command.stamp(migration_config(connection, schema), revision)
    return revision


async def run(schema):
    url, args = Settings().connection()
    engine = create_async_engine(url, connect_args=args, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            return await connection.run_sync(adopt_existing, schema)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", default="public")
    args = parser.parse_args()
    try:
        revision = asyncio.run(run(args.schema))
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    except Exception:
        # Driver errors can contain connection details. Keep CLI output secret-free.
        parser.exit(1, "Database adoption failed; existing schema was not stamped.\n")
    print(f"Schema verified and adopted at {revision}.")


if __name__ == "__main__":
    main()
