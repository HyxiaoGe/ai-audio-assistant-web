"""通知重构迁移：回填映射纯函数 + offline up/down SQL 正确性。

本仓 ORM 用 Postgres 专属类型且无真实 DB 夹具（见
tests/services/test_task_list_status_filter.py），故迁移测试走：
  1) 回填映射抽成纯函数，直接断言；
  2) alembic offline `--sql`（postgresql 方言）断言 DDL。
不起真实 DB。
"""

from __future__ import annotations

import importlib


def _load_migration():
    return importlib.import_module(
        "alembic.versions.9a1b2c3d4e5f_notification_refactor_schema"
    )

def test_backfill_rules_map_known_pairs() -> None:
    mig = _load_migration()

    # 完成 / 失败 → 对应 type
    assert mig.backfill_type_for("task", "completed") == "task_completed"
    assert mig.backfill_type_for("task", "failed") == "task_failed"

def test_backfill_unknown_pair_falls_back_to_default() -> None:
    mig = _load_migration()

    # 已下线的 auto_transcribe_started（action=progress）及任何未知组合 → 默认兜底
    assert mig.backfill_type_for("task", "progress") == mig._DEFAULT_BACKFILL_TYPE
    assert mig.backfill_type_for("youtube", "whatever") == mig._DEFAULT_BACKFILL_TYPE
    assert mig._DEFAULT_BACKFILL_TYPE == "task_completed"

def test_backfill_sql_case_covers_all_rules() -> None:
    mig = _load_migration()
    sql = mig._backfill_type_case_sql().lower()

    # 每条规则都进了 CASE WHEN
    assert "when category = 'task' and action = 'completed' then 'task_completed'" in sql
    assert "when category = 'task' and action = 'failed' then 'task_failed'" in sql
    # 兜底 ELSE
    assert "else 'task_completed'" in sql
