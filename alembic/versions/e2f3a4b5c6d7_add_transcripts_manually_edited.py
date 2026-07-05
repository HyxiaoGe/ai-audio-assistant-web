"""add transcripts.manually_edited column

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-05

区分转写段落的编辑来源:manually_edited=True 表示用户手动编辑,
False(默认)表示 AI 校对或原始内容。is_edited 只表示「内容已不同于 ASR 原文」
(AI 校对与人工编辑都会置 True),此列进一步区分是谁改的,前端据此显示「已编辑」
而非「AI 已校对」,避免手动编辑被误标为 AI 校对。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column("manually_edited", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("transcripts", "manually_edited")
