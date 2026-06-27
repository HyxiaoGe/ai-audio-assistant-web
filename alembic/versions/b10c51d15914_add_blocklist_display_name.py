"""add display_name to youtube_blocklist

Revision ID: b10c51d15914
Revises: e1f2a3b4c5d6
Create Date: 2026-06-27

display_name:频道黑名单条目的人类可读频道名快照,纯展示列。
不参与匹配/唯一键/归一化,nullable(取不到名留空,前端回落 raw_value)。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b10c51d15914"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("youtube_blocklist", sa.Column("display_name", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("youtube_blocklist", "display_name")
