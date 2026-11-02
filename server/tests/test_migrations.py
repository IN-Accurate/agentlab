import io
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from migrations.adopt import (
    BASELINE_REVISION,
    HEAD_REVISION,
    adopt_existing,
    migration_config,
)
from migrations.catalog import schema_snapshot

ROOT = Path(__file__).parents[2]


@pytest.fixture
async def migration_database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable test database")
    if "test" not in (make_url(url).database or "").lower():
        pytest.fail("Use a dedicated database containing test in its name")
    engine_url, args = Settings(_env_file=None, database_url=url).connection()
    engine = create_async_engine(engine_url, connect_args=args, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                yield connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def new_schema(connection):
    schema = "test_migration_" + uuid4().hex
    await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    return schema


async def bootstrap_sql(connection, schema):
    await connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
    source = (ROOT / "db/agentlab_schema.sql").read_text(encoding="utf-8")
    source = source.replace("\nBEGIN;\n", "\n").replace("\nCOMMIT;\n", "\n")
    raw = await connection.get_raw_connection()
    await raw.driver_connection.execute(source)


async def upgrade(connection, schema, revision="head"):
    await connection.run_sync(
        lambda sync: command.upgrade(migration_config(sync, schema), revision)
    )


def test_migration_chain_and_offline_upgrade():
    config = migration_config()
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [HEAD_REVISION]
    assert script.get_revision(HEAD_REVISION).down_revision == BASELINE_REVISION
    output = io.StringIO()
    config.output_buffer = output
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE projects" in sql
    assert "CREATE TRIGGER no_app_version_update" in sql
    assert "CREATE TABLE saved_credentials" in sql
    assert "idx_saved_credentials_expires_at" in sql
    assert "COMMIT;" in sql


async def test_fresh_migrations_match_canonical_sql(migration_database):
    connection = migration_database
    migrated = await new_schema(connection)
    canonical = await new_schema(connection)
    await upgrade(connection, migrated)
    await bootstrap_sql(connection, canonical)
    actual = await connection.run_sync(schema_snapshot, migrated)
    expected = await connection.run_sync(schema_snapshot, canonical)
    assert actual == expected
    assert len(actual["tables"]) == 15
    assert actual["triggers"][0][0] == "no_app_version_update"


async def test_existing_baseline_adoption_preserves_data(migration_database):
    connection = migration_database
    schema = await new_schema(connection)
    # Reproduce the pre-Alembic SQL database, with no revision marker.
    await upgrade(connection, schema, BASELINE_REVISION)
    await connection.exec_driver_sql(f'DROP TABLE "{schema}".alembic_version')
    project_id, version_id = uuid4(), uuid4()
    await connection.execute(
        text("INSERT INTO projects(id, name) VALUES (:id, 'Existing project')"),
        {"id": project_id},
    )
    await connection.execute(
        text(
            "INSERT INTO app_versions(id, project_id, config) "
            'VALUES (:id, :project_id, \'{"system_prompt":"keep me"}\')'
        ),
        {"id": version_id, "project_id": project_id},
    )
    before = (await connection.execute(text("SELECT * FROM app_versions"))).all()
    assert await connection.run_sync(adopt_existing, schema) == BASELINE_REVISION
    await upgrade(connection, schema)
    await upgrade(connection, schema)  # Re-running an upgrade must be harmless.
    after = (await connection.execute(text("SELECT * FROM app_versions"))).all()
    assert after == before
    assert (
        await connection.scalar(text("SELECT name FROM projects")) == "Existing project"
    )
    assert (
        await connection.scalar(text("SELECT version_num FROM alembic_version"))
        == HEAD_REVISION
    )
    nested = await connection.begin_nested()
    with pytest.raises(DBAPIError, match="immutable_app_version"):
        await connection.execute(
            text("UPDATE app_versions SET config = '{}' WHERE id = :id"),
            {"id": version_id},
        )
    await nested.rollback()


async def test_current_sql_bootstrap_can_be_adopted(migration_database):
    connection = migration_database
    schema = await new_schema(connection)
    await bootstrap_sql(connection, schema)
    before = await connection.run_sync(schema_snapshot, schema)
    assert await connection.run_sync(adopt_existing, schema) == HEAD_REVISION
    await upgrade(connection, schema)
    assert await connection.run_sync(schema_snapshot, schema) == before


async def test_adoption_rejects_drift_without_stamping(migration_database):
    connection = migration_database
    schema = await new_schema(connection)
    await bootstrap_sql(connection, schema)
    await connection.exec_driver_sql("ALTER TABLE projects ADD COLUMN drift text")
    nested = await connection.begin_nested()
    with pytest.raises(RuntimeError, match="differs"):
        await connection.run_sync(adopt_existing, schema)
    await nested.rollback()
    tables = await connection.run_sync(
        lambda sync: inspect(sync).get_table_names(schema=schema)
    )
    assert "alembic_version" not in tables


async def test_adoption_rejects_existing_history(migration_database):
    connection = migration_database
    schema = await new_schema(connection)
    await upgrade(connection, schema)
    with pytest.raises(RuntimeError, match="already has Alembic history"):
        await connection.run_sync(adopt_existing, schema)


async def test_credential_downgrade_preserves_baseline(migration_database):
    connection = migration_database
    schema = await new_schema(connection)
    await upgrade(connection, schema)
    await connection.exec_driver_sql("INSERT INTO projects(name) VALUES ('Keep this')")
    await connection.run_sync(
        lambda sync: command.downgrade(
            migration_config(sync, schema), BASELINE_REVISION
        )
    )
    tables = await connection.run_sync(
        lambda sync: inspect(sync).get_table_names(schema=schema)
    )
    assert "saved_credentials" not in tables
    assert await connection.scalar(text("SELECT name FROM projects")) == "Keep this"
    await upgrade(connection, schema)
