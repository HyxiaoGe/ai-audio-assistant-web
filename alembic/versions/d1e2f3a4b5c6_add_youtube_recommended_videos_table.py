"""add youtube_recommended_videos table

Revision ID: d1e2f3a4b5c6
Revises: c9d8e7f6a5b4
Create Date: 2026-07-01

/discover 热门推荐快照表:harvest 每 6h 全量替换,按 view_count 降序 rank。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c9d8e7f6a5b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "youtube_recommended_videos",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("channel", sa.String(length=256), nullable=True),
        sa.Column("channel_id", sa.String(length=64), nullable=True),
        sa.Column("handle", sa.String(length=128), nullable=True),
        sa.Column("thumbnail", sa.String(length=512), nullable=True),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("view_count", sa.BigInteger(), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("harvested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_youtube_recommended_rank", "youtube_recommended_videos", ["rank"])


def downgrade() -> None:
    op.drop_index("idx_youtube_recommended_rank", table_name="youtube_recommended_videos")
    op.drop_table("youtube_recommended_videos")
