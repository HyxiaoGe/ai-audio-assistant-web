"""add youtube_allowlist table

Revision ID: c9d8e7f6a5b4
Revises: b10c51d15914
Create Date: 2026-06-29

频道放行表(软删,频道专用):命中者在搜索展示态绕过 CMS。镜像 youtube_blocklist 去掉 kind/term。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c9d8e7f6a5b4"
down_revision = "b10c51d15914"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "youtube_allowlist",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("match_field", sa.String(length=16), nullable=False),
        sa.Column("raw_value", sa.String(length=256), nullable=False),
        sa.Column("normalized_value", sa.String(length=256), nullable=False),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_field", "normalized_value", name="uk_youtube_allowlist_entry"),
    )
    op.create_index(
        "idx_youtube_allowlist_active",
        "youtube_allowlist",
        ["match_field"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_youtube_allowlist_active", table_name="youtube_allowlist")
    op.drop_table("youtube_allowlist")
