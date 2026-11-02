import io
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from migrations.adopt import (
    BASELINE_REVISION,
    HEAD_REVISION,
    SAVED_CREDENTIALS_REVISION,
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
    assert (
        script.get_revision(HEAD_REVISION).down_revision == SAVED_CREDENTIALS_REVISION
    )
    assert (
        script.get_revision(SAVED_CREDENTIALS_REVISION).down_revision
        == BASELINE_REVISION
    )
    output = io.StringIO()
    config.output_buffer = output
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE projects" in sql
    assert "CREATE TRIGGER no_app_version_update" in sql
    assert "CREATE TABLE saved_credentials" in sql
    assert "idx_saved_credentials_expires_at" in sql
    assert "'router', 'agent'" in sql
    assert "COMMIT;" in sql
    assert "SET LOCAL search_path" not in sql
    assert "CREATE TABLE alembic_version" in sql


@pytest.mark.parametrize("suffix", ["plain", 'odd";colon:%'])
async def test_offline_upgrade_executes_in_only_the_requested_schema(
    migration_database, suffix
):
    async with migration_database.engine.connect() as connection:
        target = "test_offline_" + uuid4().hex + "_" + suffix
        default = "test_offline_default_" + uuid4().hex
        reference = "test_offline_reference_" + uuid4().hex
        schemas = (target, default, reference)
        quote = connection.dialect.identifier_preparer.quote_schema
        try:
            for schema in schemas:
                await connection.exec_driver_sql(f"CREATE SCHEMA {quote(schema)}")
            await connection.exec_driver_sql(f"SET search_path TO {quote(default)}")
            await connection.commit()

            config = migration_config(schema=target)
            output = io.StringIO()
            config.output_buffer = output
            command.upgrade(config, "head", sql=True)
            sql = output.getvalue()
            assert f"SET LOCAL search_path TO {quote(target)};" in sql
            assert f"CREATE TABLE {quote(target)}.alembic_version" in sql
            assert sql.index("BEGIN;") < sql.index("SET LOCAL search_path")
            assert sql.index("SET LOCAL search_path") < sql.index(
                "CREATE TABLE projects"
            )

            # Execute the exact rendered script, including its BEGIN/COMMIT, on
            # an idle connection so its transaction and SET LOCAL are exercised.
            raw = await connection.get_raw_connection()
            await raw.driver_connection.execute(sql)
            assert await connection.scalar(text("SELECT current_schema()")) == default
            default_tables = await connection.run_sync(
                lambda sync: inspect(sync).get_table_names(schema=default)
            )
            assert default_tables == []
            assert (await connection.run_sync(schema_snapshot, default))[
                "functions"
            ] == []
            assert (
                await connection.scalar(
                    text(f"SELECT version_num FROM {quote(target)}.alembic_version")
                )
                == HEAD_REVISION
            )

            await upgrade(connection, reference)
            await connection.exec_driver_sql(
                f"SET LOCAL search_path TO {quote(target)}"
            )
            offline = await connection.run_sync(schema_snapshot, target)
            await connection.exec_driver_sql(
                f"SET LOCAL search_path TO {quote(reference)}"
            )
            online = await connection.run_sync(schema_snapshot, reference)
            assert offline["tables"] == online["tables"]
            assert offline["views"] == online["views"]
            for kind in ("triggers", "functions"):
                assert [row[0] for row in offline[kind]] == [
                    row[0] for row in online[kind]
                ]
        finally:
            await connection.rollback()
            for schema in schemas:
                await connection.exec_driver_sql(
                    f"DROP SCHEMA IF EXISTS {quote(schema)} CASCADE"
                )
            await connection.commit()


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


async def seed_spans(connection, kinds):
    project, version, trace = uuid4(), uuid4(), uuid4()
    await connection.execute(
        text("INSERT INTO projects(id, name) VALUES (:id, 'Existing team')"),
        {"id": project},
    )
    await connection.execute(
        text("INSERT INTO app_versions(id, project_id) VALUES (:id, :project)"),
        {"id": version, "project": project},
    )
    await connection.execute(
        text("INSERT INTO traces(id, app_version_id) VALUES (:id, :version)"),
        {"id": trace, "version": version},
    )
    parent = None
    for index, kind in enumerate(kinds):
        identifier = uuid4()
        await connection.execute(
            text(
                "INSERT INTO spans(id, trace_id, parent_span_id, sdk_span_id, type) "
                "VALUES (:id, :trace, :parent, :sdk, :kind)"
            ),
            {
                "id": identifier,
                "trace": trace,
                "parent": parent,
                "sdk": f"span-{index}",
                "kind": kind,
            },
        )
        parent = identifier
    return trace


async def existing_rows(connection):
    return {
        table: (
            await connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
        ).all()
        for table in ("projects", "app_versions", "traces", "spans")
    }


async def seed_saved_credential(connection):
    await connection.exec_driver_sql(
        "INSERT INTO saved_credentials "
        "(provider, model, ciphertext, key_hint, key_fingerprint, expires_at) "
        "VALUES ('custom', 'existing-model', 'encrypted-test-value', 'hint', "
        "'{}', now() + interval '1 hour')"
    )


async def test_saved_key_revision_adopts_and_upgrades_without_data_loss(
    migration_database,
):
    connection = migration_database
    schema = await new_schema(connection)
    await upgrade(connection, schema, SAVED_CREDENTIALS_REVISION)
    await connection.exec_driver_sql(f'DROP TABLE "{schema}".alembic_version')
    await seed_spans(connection, ["llm", "retrieval", "tool", "validation"])
    await seed_saved_credential(connection)
    before = await existing_rows(connection)
    keys = (await connection.execute(text("SELECT * FROM saved_credentials"))).all()
    assert (
        await connection.run_sync(adopt_existing, schema) == SAVED_CREDENTIALS_REVISION
    )
    await upgrade(connection, schema)
    assert await existing_rows(connection) == before
    assert (
        await connection.execute(text("SELECT * FROM saved_credentials"))
    ).all() == keys


async def test_team_only_sql_adoption_creates_keys_and_preserves_team_spans(
    migration_database,
):
    connection = migration_database
    schema = await new_schema(connection)
    await bootstrap_sql(connection, schema)
    await connection.exec_driver_sql("DROP TABLE saved_credentials")
    await seed_spans(connection, ["router", "agent", "llm", "retrieval"])
    before = await existing_rows(connection)
    assert await connection.run_sync(adopt_existing, schema) == HEAD_REVISION
    await upgrade(connection, schema)
    assert await existing_rows(connection) == before
    assert await connection.scalar(text("SELECT count(*) FROM saved_credentials")) == 0
    assert (
        await connection.scalar(text("SELECT version_num FROM alembic_version"))
        == HEAD_REVISION
    )
    canonical = await new_schema(connection)
    await bootstrap_sql(connection, canonical)
    assert await connection.run_sync(
        schema_snapshot, schema
    ) == await connection.run_sync(schema_snapshot, canonical)


async def test_team_only_drift_is_rejected_before_target_writes(migration_database):
    connection = migration_database
    schema = await new_schema(connection)
    await bootstrap_sql(connection, schema)
    await connection.exec_driver_sql("DROP TABLE saved_credentials")
    await connection.exec_driver_sql("ALTER TABLE projects ADD COLUMN drift text")
    await seed_spans(connection, ["router", "agent"])
    before = await connection.run_sync(schema_snapshot, schema)
    rows = await existing_rows(connection)
    with pytest.raises(RuntimeError, match="differs"):
        await connection.run_sync(adopt_existing, schema)
    assert await connection.run_sync(schema_snapshot, schema) == before
    assert await existing_rows(connection) == rows
    assert "alembic_version" not in await connection.run_sync(
        lambda sync: inspect(sync).get_table_names(schema=schema)
    )


async def test_team_adoption_rolls_back_partial_key_storage_and_history(
    migration_database,
):
    connection = migration_database
    schema = await new_schema(connection)
    await bootstrap_sql(connection, schema)
    await connection.exec_driver_sql("DROP TABLE saved_credentials")
    await seed_spans(connection, ["router", "agent", "llm"])
    before = await connection.run_sync(schema_snapshot, schema)
    rows = await existing_rows(connection)
    schemas = await connection.run_sync(lambda sync: inspect(sync).get_schema_names())
    indexes = 0

    def fail_target_index(conn, cursor, statement, parameters, context, executemany):
        nonlocal indexes
        if "CREATE INDEX idx_saved_credentials_expires_at" in statement:
            indexes += 1
            # First is the disposable reference schema; second is the legacy DB.
            if indexes == 2:
                raise RuntimeError("injected index creation failure")

    event.listen(connection.sync_connection, "before_cursor_execute", fail_target_index)
    try:
        with pytest.raises(RuntimeError, match="injected index creation failure"):
            await connection.run_sync(adopt_existing, schema)
    finally:
        event.remove(
            connection.sync_connection, "before_cursor_execute", fail_target_index
        )
    assert indexes == 2
    assert await connection.run_sync(schema_snapshot, schema) == before
    assert await existing_rows(connection) == rows
    assert (
        await connection.run_sync(lambda sync: inspect(sync).get_schema_names())
        == schemas
    )
    assert "alembic_version" not in await connection.run_sync(
        lambda sync: inspect(sync).get_table_names(schema=schema)
    )


async def test_team_span_migration_retains_all_types_and_rejects_unknown(
    migration_database,
):
    connection = migration_database
    schema = await new_schema(connection)
    await upgrade(connection, schema, SAVED_CREDENTIALS_REVISION)
    trace = await seed_spans(connection, ["llm", "retrieval", "tool", "validation"])
    before = await existing_rows(connection)
    await upgrade(connection, schema)
    assert await existing_rows(connection) == before
    for kind in ("router", "agent"):
        await connection.execute(
            text(
                "INSERT INTO spans(trace_id, sdk_span_id, type) VALUES (:trace, :kind, :kind)"
            ),
            {"trace": trace, "kind": kind},
        )
    assert await connection.scalar(text("SELECT count(*) FROM spans")) == 6
    nested = await connection.begin_nested()
    with pytest.raises(DBAPIError, match="spans_type_check"):
        await connection.execute(
            text(
                "INSERT INTO spans(trace_id, sdk_span_id, type) VALUES (:trace, 'bad', 'unknown')"
            ),
            {"trace": trace},
        )
    await nested.rollback()


async def test_team_downgrade_refuses_existing_team_rows_and_retains_keys(
    migration_database,
):
    connection = migration_database
    schema = await new_schema(connection)
    await upgrade(connection, schema)
    await seed_spans(connection, ["router", "agent", "llm"])
    await seed_saved_credential(connection)
    before = await connection.run_sync(schema_snapshot, schema)
    rows = await existing_rows(connection)
    nested = await connection.begin_nested()
    with pytest.raises(DBAPIError, match="router or agent spans still exist"):
        await connection.run_sync(
            lambda sync: command.downgrade(
                migration_config(sync, schema), BASELINE_REVISION
            )
        )
    await nested.rollback()
    assert await connection.run_sync(schema_snapshot, schema) == before
    assert await existing_rows(connection) == rows
    assert await connection.scalar(text("SELECT count(*) FROM saved_credentials")) == 1
    assert (
        await connection.scalar(text("SELECT version_num FROM alembic_version"))
        == HEAD_REVISION
    )


async def test_team_downgrade_without_team_rows_retains_old_spans_and_keys(
    migration_database,
):
    connection = migration_database
    schema = await new_schema(connection)
    await upgrade(connection, schema)
    await seed_spans(connection, ["llm", "retrieval", "tool", "validation"])
    await seed_saved_credential(connection)
    before = await existing_rows(connection)
    await connection.run_sync(
        lambda sync: command.downgrade(
            migration_config(sync, schema), SAVED_CREDENTIALS_REVISION
        )
    )
    assert await existing_rows(connection) == before
    assert await connection.scalar(text("SELECT count(*) FROM saved_credentials")) == 1
    assert (
        await connection.scalar(text("SELECT version_num FROM alembic_version"))
        == SAVED_CREDENTIALS_REVISION
    )
    await upgrade(connection, schema)
