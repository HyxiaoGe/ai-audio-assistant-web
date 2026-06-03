"""add images to summaries

Revision ID: b7c3d9e1f2a0
Revises: 9a1b2c3d4e5f
Create Date: 2026-06-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c3d9e1f2a0"
down_revision: str | None = "9a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 渐进式展示：overview 配图状态集。nullable，默认 NULL —— 不迁移历史数据（D2），
    # 旧 overview 的 content 已是 ![](url)，由前端 img 覆写直出，不享新 pending/failed 态。
    op.add_column(
        "summaries",
        sa.Column("images", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("summaries", "images")
