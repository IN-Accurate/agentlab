"""Persist Fernet-encrypted BYOK credentials without storing plaintext keys."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_saved_credentials"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "saved_credentials",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("judge_model", sa.Text(), nullable=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.Text(), nullable=False),
        sa.Column("key_fingerprint", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_saved_credentials_expires_at", "saved_credentials", ["expires_at"]
    )


def downgrade():
    op.drop_index("idx_saved_credentials_expires_at", table_name="saved_credentials")
    op.drop_table("saved_credentials")
