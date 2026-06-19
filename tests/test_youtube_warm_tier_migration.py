"""warm-tier 迁移:alembic offline up/down SQL 正确性 + 单 head。不起真实 DB。

Tier 2 懒加载主体的「纯 schema」一步:给 youtube_subscriptions 加系统独占的冷热位
is_warm(布尔,默认 false)+ last_active_at(行为时间戳),并回填祖父留热的存量行。
此步不改任何查询(无人按 is_warm 过滤),线上行为不变,仅铺地基。
"""

from __future__ import annotations

import os
import subprocess
import sys

_NEW_REV = "f9a0b1c2d3e4"
_PREV_HEAD = "f6a7b8c9d0e1"

_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
    "REDIS_URL": "redis://localhost:6379/0",
}


def _alembic_sql(direction: str, rev_range: str) -> str:
    env = {**os.environ, **_ENV}
    out = subprocess.run(
        [sys.executable, "-m", "alembic", direction, rev_range, "--sql"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.lower()


def test_upgrade_sql_adds_warm_columns_index_and_backfill() -> None:
    sql = _alembic_sql("upgrade", f"{_PREV_HEAD}:{_NEW_REV}")
    # 两列
    assert "add column is_warm" in sql
    assert "add column last_active_at" in sql
    # 调度热路径专用部分索引:仅覆盖 warm 且用户未手动关同步的行
    assert "create index idx_youtube_subscriptions_warm" in sql
    assert "is_warm = true and sync_enabled = true" in sql
    # 回填:祖父留热(同步过/已星标/已开自动转写),last_active_at 取 videos_synced_at 兜底 updated_at
    assert "set is_warm = true" in sql
    assert "coalesce(videos_synced_at, updated_at)" in sql


def test_downgrade_sql_drops_warm_columns_and_index() -> None:
    sql = _alembic_sql("downgrade", f"{_NEW_REV}:{_PREV_HEAD}")
    assert "drop index idx_youtube_subscriptions_warm" in sql
    assert "drop column last_active_at" in sql
    assert "drop column is_warm" in sql


def test_single_alembic_head_no_fork() -> None:
    # warm-tier 已不是最新 head(其上有溯源字段迁移 c4d5e6f7a8b9),故此处只校验迁移链无分叉
    # (单 head),不再 pin 具体 head 字符串——head pin 的 canary 留给 notification /
    # summaries_images 两个测试,避免今后每加一条迁移都要改这里。
    env = {**os.environ, **_ENV}
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.returncode == 0, out.stderr
    heads = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert len(heads) == 1, f"alembic 出现多 head:{out.stdout}"
