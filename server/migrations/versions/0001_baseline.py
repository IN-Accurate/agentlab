"""Freeze the existing fourteen-table schema, including the immutability trigger."""

from alembic import op
from sqlalchemy import text

from migrations.baseline import baseline_statements

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    for statement in baseline_statements():
        op.execute(text(statement))


def downgrade():
    for name in (
        "verifications",
        "repairs",
        "replays",
        "diagnoses",
        "assertion_results",
        "run_results",
        "runs",
        "assertions",
        "test_cases",
        "retrieved_chunks",
        "spans",
        "traces",
        "app_versions",
        "projects",
    ):
        op.drop_table(name)
    op.execute("DROP FUNCTION reject_app_version_update()")
