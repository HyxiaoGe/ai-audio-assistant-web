"""add task public visibility

Revision ID: f6a7b8c9d0e1
Revises: b7c3d9e1f2a0
Create Date: 2026-06-10

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "b7c3d9e1f2a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("tasks", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    # 探索页列表查询专用部分索引:只覆盖公开未删行,按发布时间倒序
    op.create_index(
        "idx_tasks_public",
        "tasks",
        [sa.text("published_at DESC")],
        postgresql_where=sa.text("is_public = TRUE AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_tasks_public", table_name="tasks")
    op.drop_column("tasks", "published_at")
    op.drop_column("tasks", "is_public")
