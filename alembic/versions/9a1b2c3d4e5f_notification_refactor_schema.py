"""notification refactor schema

Revision ID: 9a1b2c3d4e5f
Revises: e8f9a0b1c2d3
Create Date: 2026-06-03 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "9a1b2c3d4e5f"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None

# 存量行 (category, action) -> 新 type 的回填规则（spec §7）。
# 已知历史产出：完成/失败两类持久化行；其余（含已下线的 auto_transcribe_started
# action=progress）无对应新 type，统一走默认兜底，避免回填后 type NOT NULL 违约。
_DEFAULT_BACKFILL_TYPE = "task_completed"
_TYPE_BACKFILL_RULES: tuple[tuple[str, str, str], ...] = (
    ("task", "completed", "task_completed"),
    ("task", "failed", "task_failed"),
)


def backfill_type_for(category: str, action: str) -> str:
    """纯函数：按规则把 (category, action) 映射到新 type，未命中走默认兜底。"""
    for cat, act, ntype in _TYPE_BACKFILL_RULES:
        if category == cat and action == act:
            return ntype
    return _DEFAULT_BACKFILL_TYPE


def _backfill_type_case_sql() -> str:
    """生成与 `backfill_type_for` 等价的 SQL CASE 表达式（迁移 UPDATE 用）。"""
    whens = " ".join(
        f"WHEN category = '{cat}' AND action = '{act}' THEN '{ntype}'"
        for cat, act, ntype in _TYPE_BACKFILL_RULES
    )
    return f"CASE {whens} ELSE '{_DEFAULT_BACKFILL_TYPE}' END"


def upgrade() -> None:
    # 1) 加列（type 先可空以便回填存量行；dedup_key 可空）
    op.add_column("notifications", sa.Column("type", sa.String(length=50), nullable=True))
    op.add_column("notifications", sa.Column("dedup_key", sa.String(length=255), nullable=True))

    # 2) 回填 type（(category, action) -> type，未知组合走默认兜底）
    op.execute(f"UPDATE notifications SET type = {_backfill_type_case_sql()}")

    # 3) type 置 NOT NULL
    op.alter_column("notifications", "type", existing_type=sa.String(length=50), nullable=False)

    # 4) dedup_key 部分唯一索引（原子去重）
    op.create_index(
        "ix_notifications_dedup_key",
        "notifications",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )

    # 5) 重建未读部分索引：去掉 dismissed 条件（纯未读/已读）
    op.drop_index(
        "ix_notifications_unread",
        table_name="notifications",
        postgresql_where=sa.text("read_at IS NULL AND dismissed_at IS NULL"),
    )
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )

    # 6) 删 cleanup 索引（TTL 缓做）
    op.drop_index(
        "ix_notifications_cleanup",
        table_name="notifications",
        postgresql_where=sa.text("read_at IS NOT NULL"),
    )

    # 7) title/message 改可空（主渲染走 type+params）
    op.alter_column("notifications", "title", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("notifications", "message", existing_type=sa.Text(), nullable=True)

    # 8) 删旧列（被 type 吸收 / 纯未读已读无软删 / 死列）
    op.drop_column("notifications", "action")
    op.drop_column("notifications", "dismissed_at")
    op.drop_column("notifications", "expires_at")

    # 9) task_id FK 改 CASCADE（删任务连带删通知）
    op.drop_constraint("notifications_task_id_fkey", "notifications", type_="foreignkey")
    op.create_foreign_key(
        "notifications_task_id_fkey",
        "notifications",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # 9') FK 还原 SET NULL
    op.drop_constraint("notifications_task_id_fkey", "notifications", type_="foreignkey")
    op.create_foreign_key(
        "notifications_task_id_fkey",
        "notifications",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 8') 还原被删列（旧 schema 为 NOT NULL，但存量行已无值；
    # 给 server_default 让历史回滚不违约，与原始定义保持一致语义）
    op.add_column(
        "notifications",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("action", sa.String(length=50), server_default=sa.text("'completed'"), nullable=False),
    )

    # 7') title/message 还 NOT NULL（先填空串兜底再置 NOT NULL）
    op.execute("UPDATE notifications SET title = '' WHERE title IS NULL")
    op.execute("UPDATE notifications SET message = '' WHERE message IS NULL")
    op.alter_column("notifications", "message", existing_type=sa.Text(), nullable=False)
    op.alter_column("notifications", "title", existing_type=sa.String(length=255), nullable=False)

    # 6') 还原 cleanup 索引
    op.create_index(
        "ix_notifications_cleanup",
        "notifications",
        ["read_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("read_at IS NOT NULL"),
    )

    # 5') 还原旧未读部分索引（含 dismissed 条件）
    op.drop_index(
        "ix_notifications_unread",
        table_name="notifications",
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL AND dismissed_at IS NULL"),
    )

    # 4') 删 dedup 唯一索引
    op.drop_index(
        "ix_notifications_dedup_key",
        table_name="notifications",
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )

    # 1') 删新增列
    op.drop_column("notifications", "dedup_key")
    op.drop_column("notifications", "type")
