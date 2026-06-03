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
    raise NotImplementedError  # 在 Task 1.6 实现


def downgrade() -> None:
    raise NotImplementedError  # 在 Task 1.6 实现
