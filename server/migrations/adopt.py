"""Verify an existing SQL-built database before recording Alembic history."""

import argparse
import asyncio
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from migrations.catalog import schema_snapshot

BASELINE_REVISION = "0001_baseline"
SAVED_CREDENTIALS_REVISION = "0002_saved_credentials"
HEAD_REVISION = "0003_team_span_types"
KNOWN_REVISIONS = (BASELINE_REVISION, SAVED_CREDENTIALS_REVISION, HEAD_REVISION)


def migration_config(connection=None, schema=None):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
    if schema:
        config.cmd_opts = argparse.Namespace(x=[f"schema={schema}"])
    return config


def adopt_existing(connection, schema="public"):
    """Verify a known schema and record history, atomically within a savepoint.

    Exact team-only SQL databases receive the missing saved-credential table and
    index before head is verified and stamped. Existing application rows are never
    changed. The caller owns the outer transaction; any failure rolls back this
    entire operation, including scratch objects and partially applied key storage.
    """
    with connection.begin_nested():
        return _adopt_existing(connection, schema)


def _adopt_existing(connection, schema):
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
    revision = None
    for candidate in KNOWN_REVISIONS:
        command.upgrade(expected_config, candidate)
        expected = schema_snapshot(connection, scratch)
        if actual == expected:
            revision = candidate
            break
    if revision is None:
        # main before the saved-key merge has fourteen tables and the team's
        # expanded span check. It is not a historical Alembic revision, so first
        # materialize exactly the missing storage, then verify the complete head.
        team_only = deepcopy(expected)
        del team_only["tables"]["saved_credentials"]
        if actual != team_only:
            raise RuntimeError(
                "Existing schema differs from all known SQL bootstrap schemas; "
                "no history was recorded. Reconcile drift on a database copy first."
            )
        connection.exec_driver_sql(f"SET LOCAL search_path TO {quote(schema)}")
        key_revision = ScriptDirectory.from_config(expected_config).get_revision(
            SAVED_CREDENTIALS_REVISION
        )
        with Operations.context(MigrationContext.configure(connection)):
            key_revision.module.upgrade()
        if schema_snapshot(connection, schema) != expected:
            raise RuntimeError("Team database upgrade did not match the verified head")
        revision = HEAD_REVISION
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
