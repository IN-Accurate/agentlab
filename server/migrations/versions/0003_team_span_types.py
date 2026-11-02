"""Allow the router and agent spans used by the merged team implementation."""

from alembic import op

revision = "0003_team_span_types"
down_revision = "0002_saved_credentials"
branch_labels = None
depends_on = None

BASELINE_TYPES = "type IN ('llm', 'retrieval', 'tool', 'validation')"
TEAM_TYPES = "type IN ('llm', 'retrieval', 'tool', 'validation', 'router', 'agent')"


def upgrade():
    op.drop_constraint("spans_type_check", "spans", type_="check")
    op.create_check_constraint("spans_type_check", "spans", TEAM_TYPES)


def downgrade():
    # Hold the lock through validation and constraint replacement so a concurrent
    # request cannot insert a team span between the guard and the downgrade.
    op.execute("LOCK TABLE spans IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM spans WHERE type IN ('router', 'agent')) THEN
                RAISE EXCEPTION 'Cannot downgrade: router or agent spans still exist';
            END IF;
        END;
        $$"""
    )
    op.drop_constraint("spans_type_check", "spans", type_="check")
    op.create_check_constraint("spans_type_check", "spans", BASELINE_TYPES)
