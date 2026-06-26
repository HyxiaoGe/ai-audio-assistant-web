"""add youtube_blocklist table

Revision ID: d7b9f3a1c5e2
Revises: ab7156a5abd5
Create Date: 2026-06-26 12:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d7b9f3a1c5e2"
down_revision = "ab7156a5abd5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "youtube_blocklist",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("match_field", sa.String(length=16), nullable=False),
        sa.Column("raw_value", sa.String(length=256), nullable=False),
        sa.Column("normalized_value", sa.String(length=256), nullable=False),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "match_field", "normalized_value", name="uk_youtube_blocklist_entry"),
    )
    op.create_index(
        "idx_youtube_blocklist_active",
        "youtube_blocklist",
        ["kind"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_youtube_blocklist_active", table_name="youtube_blocklist")
    op.drop_table("youtube_blocklist")
