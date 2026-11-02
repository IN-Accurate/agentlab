"""Schema signatures used to verify adoption without changing application data."""

from sqlalchemy import inspect, text


def schema_snapshot(connection, schema):
    inspector = inspect(connection)

    def normalize(value):
        # PostgreSQL includes schema qualification in trigger/function definitions.
        if isinstance(value, str):
            return value.replace(f'"{schema}".', "").replace(f"{schema}.", "")
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [normalize(item) for item in value]
        return value

    tables = {}
    for name in sorted(inspector.get_table_names(schema=schema)):
        if name == "alembic_version":
            continue
        columns = []
        for column in inspector.get_columns(name, schema=schema):
            columns.append(
                {
                    "name": column["name"],
                    "type": str(column["type"].compile(dialect=connection.dialect)),
                    "nullable": column["nullable"],
                    "default": column["default"],
                    "computed": column.get("computed"),
                    "identity": column.get("identity"),
                }
            )
        foreign_keys = inspector.get_foreign_keys(name, schema=schema)
        for key in foreign_keys:
            if key["referred_schema"] == schema:
                key["referred_schema"] = None
        tables[name] = {
            "columns": columns,
            "primary_key": inspector.get_pk_constraint(name, schema=schema),
            "foreign_keys": sorted(foreign_keys, key=lambda item: item["name"]),
            "unique": sorted(
                inspector.get_unique_constraints(name, schema=schema),
                key=lambda item: item["name"],
            ),
            "checks": sorted(
                inspector.get_check_constraints(name, schema=schema),
                key=lambda item: item["name"],
            ),
            "indexes": sorted(
                inspector.get_indexes(name, schema=schema),
                key=lambda item: item["name"],
            ),
        }
    triggers = connection.execute(
        text(
            "SELECT t.tgname, pg_get_triggerdef(t.oid) "
            "FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND NOT t.tgisinternal ORDER BY t.tgname"
        ),
        {"schema": schema},
    ).all()
    functions = connection.execute(
        text(
            "SELECT p.proname, pg_get_functiondef(p.oid) "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = :schema AND p.proname = 'reject_app_version_update' "
            "ORDER BY p.proname"
        ),
        {"schema": schema},
    ).all()
    return normalize(
        {
            "tables": tables,
            "triggers": [list(row) for row in triggers],
            "functions": [list(row) for row in functions],
            "views": sorted(inspector.get_view_names(schema=schema)),
        }
    )
