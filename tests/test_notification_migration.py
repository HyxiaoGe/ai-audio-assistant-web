"""通知重构迁移：回填映射纯函数 + offline up/down SQL 正确性。

本仓 ORM 用 Postgres 专属类型且无真实 DB 夹具（见
tests/services/test_task_list_status_filter.py），故迁移测试走：
  1) 回填映射抽成纯函数，直接断言；
  2) alembic offline `--sql`（postgresql 方言）断言 DDL。
不起真实 DB。
"""

from __future__ import annotations

import importlib
import subprocess
from types import ModuleType


def _load_migration() -> ModuleType:
    return importlib.import_module("alembic.versions.9a1b2c3d4e5f_notification_refactor_schema")


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


_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
    "REDIS_URL": "redis://localhost:6379/0",
}


def _alembic_sql(direction: str, rev_range: str) -> str:
    import os
    import sys

    env = {**os.environ, **_ENV}
    out = subprocess.run(
        [sys.executable, "-m", "alembic", direction, rev_range, "--sql"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.lower()


def test_upgrade_sql_emits_expected_ddl() -> None:
    sql = _alembic_sql("upgrade", "e8f9a0b1c2d3:9a1b2c3d4e5f")

    # 加列
    assert "alter table notifications add column type" in sql
    assert "alter table notifications add column dedup_key" in sql
    # 回填 UPDATE 含 CASE 兜底
    assert "update notifications set type" in sql
    assert "else 'task_completed' end" in sql
    # type 置 NOT NULL
    assert "alter column type set not null" in sql
    # dedup_key 部分唯一索引
    assert "create unique index ix_notifications_dedup_key" in sql
    assert "where dedup_key is not null" in sql
    # 重建未读索引：去掉 dismissed 条件
    assert "drop index ix_notifications_unread" in sql
    assert "create index ix_notifications_unread" in sql
    # 删 cleanup 索引
    assert "drop index ix_notifications_cleanup" in sql
    # title/message 改可空
    assert "alter column title drop not null" in sql
    assert "alter column message drop not null" in sql
    # 删旧列
    assert "drop column action" in sql
    assert "drop column dismissed_at" in sql
    assert "drop column expires_at" in sql
    # FK 改 CASCADE
    assert "drop constraint notifications_task_id_fkey" in sql
    assert "on delete cascade" in sql


def test_downgrade_sql_reverses_changes() -> None:
    sql = _alembic_sql("downgrade", "9a1b2c3d4e5f:e8f9a0b1c2d3")

    # 还原列
    assert "drop column type" in sql
    assert "drop column dedup_key" in sql
    # 还原被删列
    assert "add column action" in sql
    assert "add column dismissed_at" in sql
    assert "add column expires_at" in sql
    # FK 还原 SET NULL
    assert "on delete set null" in sql
    # 还原旧未读部分索引：必须带回 dismissed 条件（这是 up/down 唯一谓词差异处）
    assert "create index ix_notifications_unread" in sql
    assert "dismissed_at is null" in sql
    # 还原 cleanup 索引
    assert "create index ix_notifications_cleanup" in sql
    # 还原 dedup 唯一索引被删
    assert "drop index ix_notifications_dedup_key" in sql


def test_single_alembic_head_is_new_revision() -> None:
    import os
    import sys

    env = {**os.environ, **_ENV}
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.returncode == 0, out.stderr
    heads = [ln for ln in out.stdout.splitlines() if ln.strip()]
    # 必须恰好单 head，且为新迁移（否则说明迁移链分叉）
    assert len(heads) == 1, f"alembic 出现多 head：{out.stdout}"
    assert "9a1b2c3d4e5f" in heads[0]
