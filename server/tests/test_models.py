from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, update
from sqlalchemy.exc import DBAPIError

from app.errors import AppError
from app.models import AppVersion, Base, Project
from app.schemas import TraceCreate, VersionCreate


@pytest.mark.asyncio
async def test_models_match_existing_sql(api):
    _, app = api

    def compare(connection):
        inspector = inspect(connection)
        assert set(inspector.get_table_names()) == set(Base.metadata.tables)
        for name, table in Base.metadata.tables.items():
            actual = {item["name"]: item for item in inspector.get_columns(name)}
            assert set(actual) == set(table.columns.keys())
            for column in table.columns:
                reflected = actual[column.name]
                assert reflected["nullable"] == column.nullable, (name, column.name)
                assert str(
                    reflected["type"].compile(dialect=connection.dialect)
                ) == str(column.type.compile(dialect=connection.dialect)), (
                    name,
                    column.name,
                )
            actual_foreign = {
                (
                    tuple(item["constrained_columns"]),
                    item["referred_table"],
                    tuple(item["referred_columns"]),
                    item["options"].get("ondelete"),
                )
                for item in inspector.get_foreign_keys(name)
            }
            expected_foreign = {
                (
                    tuple(c.parent.name for c in fk.elements),
                    fk.referred_table.name,
                    tuple(c.column.name for c in fk.elements),
                    fk.ondelete,
                )
                for fk in table.foreign_key_constraints
            }
            assert actual_foreign == expected_foreign, name
            assert {
                item["name"]
                for item in inspector.get_indexes(name)
                if not item.get("duplicates_constraint")
            } == {index.name for index in table.indexes}, name
            actual_unique = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints(name)
            }
            expected_unique = {
                tuple(c.name for c in constraint.columns)
                for constraint in table.constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            }
            assert actual_unique == expected_unique, name
            expected_checks = [
                c
                for c in table.constraints
                if c.__class__.__name__ == "CheckConstraint"
            ]
            assert len(inspector.get_check_constraints(name)) == len(expected_checks), (
                name
            )

    async with app.state.engine.connect() as connection:
        await connection.run_sync(compare)


@pytest.mark.asyncio
async def test_existing_versions_cannot_be_updated(api):
    _, app = api
    async with app.state.sessions() as session:
        async with session.begin():
            project = Project(name="Acme Support")
            session.add(project)
            await session.flush()
            version = AppVersion(
                project_id=project.id, config={"system_prompt": "Original"}
            )
            session.add(version)
            await session.flush()
        version_id = version.id
        version.config = {"system_prompt": "Changed"}
        with pytest.raises(AppError, match="new version"):
            await session.flush()
        await session.rollback()
        unchanged = await session.get(AppVersion, version_id)
        assert unchanged.config == {"system_prompt": "Original"}


def test_trace_schema_rejects_cycles_naive_time_and_nan():
    payload = {
        "id": str(uuid4()),
        "app_version_id": str(uuid4()),
        "spans": [
            {"sdk_span_id": "a", "parent_sdk_span_id": "a", "type": "llm"},
        ],
    }
    with pytest.raises(ValidationError, match="cycle"):
        TraceCreate.model_validate(payload)
    payload["spans"] = [
        {"sdk_span_id": "a", "type": "llm", "started_at": "2026-01-01T00:00:00"}
    ]
    with pytest.raises(ValidationError):
        TraceCreate.model_validate(payload)
    payload["spans"] = [
        {
            "sdk_span_id": "a",
            "type": "retrieval",
            "chunks": [{"rank": 1, "text": "Context", "score": float("nan")}],
        }
    ]
    with pytest.raises(ValidationError):
        TraceCreate.model_validate(payload)


def test_version_config_rejects_credentials_and_unknown_fields():
    with pytest.raises(ValidationError):
        VersionCreate(config={"api_key": "REPLACE_ME"})
    with pytest.raises(ValidationError):
        VersionCreate(
            config={"agent": {"type": "openai_compatible", "password": "REPLACE_ME"}}
        )


@pytest.mark.asyncio
async def test_core_update_rejected_by_trigger(api):
    _, app = api
    async with app.state.sessions() as db, db.begin():
        project = Project(name="Acme Support")
        db.add(project)
        await db.flush()
        version = AppVersion(
            project_id=project.id, config={"system_prompt": "Original"}
        )
        db.add(version)
        await db.flush()
        version_id = version.id
    async with app.state.sessions() as db:
        with pytest.raises(DBAPIError, match="immutable_app_version"):
            await db.execute(
                update(AppVersion)
                .where(AppVersion.id == version_id)
                .values(config={"system_prompt": "Changed"})
            )
