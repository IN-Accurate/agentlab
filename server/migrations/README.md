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
and triggers against frozen migration schemas. It creates a temporary comparison
schema inside the same transaction and removes it before recording history. It
accepts the original fourteen-table bootstrap or the current bootstrap including
saved credentials, stamps the matching revision, and preserves application data.
It refuses schema drift and databases that already have Alembic history. Do not
replace this check with an unverified `alembic stamp`.

After validating the copy, repeat the same commands on the intended database.
Application startup does not run migrations or change database structure.

For an explicitly named schema, use matching options for both commands:

```sh
python -m migrations.adopt --schema agentlab
python -m alembic -x schema=agentlab upgrade head
```

`0001_baseline` is an immutable snapshot of the original fourteen-table SQL schema.
`0002_saved_credentials` adds only encrypted saved credentials and their expiry
index. Keep `db/agentlab_schema.sql` aligned with the latest migration for SQL
bootstrap users and tests; never edit the frozen baseline for later features.
Downgrading `0002_saved_credentials` deletes saved credentials, so use upgrades for
normal deployments and keep the same `APP_ENCRYPTION_KEY` across restarts.

No team schema has been invented here. The team migration requires Sumit's agreed
tables and new span, diagnosis-rule, and patch values.
