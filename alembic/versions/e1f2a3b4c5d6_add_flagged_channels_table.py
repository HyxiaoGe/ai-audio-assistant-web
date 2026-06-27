"""add flagged_channels table

Revision ID: e1f2a3b4c5d6
Revises: d7b9f3a1c5e2
Create Date: 2026-06-27 12:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d7b9f3a1c5e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flagged_channels",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("match_field", sa.String(length=16), nullable=False),
        sa.Column("match_value", sa.String(length=256), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=True),
        sa.Column("channel_handle", sa.String(length=128), nullable=True),
        sa.Column("channel_name", sa.String(length=256), nullable=True),
        sa.Column("block_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_video_id", sa.String(length=32), nullable=True),
        sa.Column("last_title", sa.String(length=256), nullable=True),
        sa.Column("last_flagged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("resolved_by", sa.String(length=64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_field", "match_value", name="uk_flagged_channels"),
    )
    op.create_index(
        "idx_flagged_channels_pending",
        "flagged_channels",
        ["status"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("idx_flagged_channels_pending", table_name="flagged_channels")
    op.drop_table("flagged_channels")
