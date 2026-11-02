# Database migrations

Run from `server/`, with dependencies installed and `DATABASE_URL` set to a direct
Postgres connection. Connection handling is shared with the application, including
Neon TLS options. Database credentials are never written to Alembic configuration.

For a fresh database:

```sh
python -m alembic upgrade head
python -m alembic current
```

For an existing database created using `db/agentlab_schema.sql`, first take a backup
and restore it to an isolated copy. On that copy, run:

```sh
python -m migrations.adopt
python -m alembic upgrade head
```

The adoption command compares columns, types, defaults, nullability, primary keys,
foreign keys, unique/check constraints, indexes, views, the immutability function,
and triggers against frozen migration schemas. It creates a disposable comparison
schema inside the same transaction and removes it before recording history.

| Existing SQL database | Adoption behavior |
| --- | --- |
| Original fourteen tables, four span types, no saved credentials | Verify and stamp `0001_baseline`; `upgrade head` adds keys and team span types |
| Fifteen tables with saved credentials, four span types | Verify and stamp `0002_saved_credentials`; `upgrade head` adds team span types |
| Current fifteen tables with saved credentials and six span types | Verify and stamp `0003_team_span_types` |
| Team-only fourteen tables with six span types, as on main at `cfac9c8` | Verify the exact legacy shape, create the missing saved-credentials table/index, verify the complete head schema, then stamp `0003_team_span_types` |

The team-only path is a guarded upgrade as well as adoption: it creates missing
key storage before recording history. It preserves existing router/agent spans,
their parent links, and all other application records; it never temporarily narrows
the span constraint. The command uses a savepoint so a drift check, DDL failure,
or final verification failure rolls back scratch objects, new key storage, and
history together. Unknown schema drift is rejected before target DDL runs.
Databases that already have Alembic history must use `upgrade head` directly.
Do not replace these checks with an unverified `alembic stamp`.

After validating the copy, repeat the same commands on the intended database.
Application startup does not run migrations or change database structure.

For an explicitly named schema, use matching options for both commands:

```sh
python -m migrations.adopt --schema agentlab
python -m alembic -x schema=agentlab upgrade head
```

`0001_baseline` is an immutable snapshot of the original fourteen-table SQL schema.
`0002_saved_credentials` adds only encrypted saved credentials and their expiry
index. `0003_team_span_types` expands `spans_type_check` to accept `router` and
`agent` alongside `llm`, `retrieval`, `tool`, and `validation`. The team configuration
uses existing `app_versions.config` JSONB; this migration adds no team tables,
diagnosis-rule values, or repair patch types.

Keep `db/agentlab_schema.sql` aligned with the latest migration for SQL bootstrap
users and tests; never edit the frozen baseline or earlier revisions for later
features. Downgrading `0003_team_span_types` locks the span table and refuses if
router/agent rows exist, preserving those rows and the current schema. When no team
spans exist, it restores the four-type check without deleting old spans or keys.
Downgrading `0002_saved_credentials` deletes saved credentials, so use upgrades for
normal deployments and keep the same `APP_ENCRYPTION_KEY` across restarts.
