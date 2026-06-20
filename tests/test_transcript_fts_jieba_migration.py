"""转写 FTS 切 jiebacfg 迁移(c1d2e3f4a5b6):alembic offline up/down SQL 正确性。不起真实 DB。

单 head 不变量由既有 migration 测试(notification/publisher_profile/summaries_images)统一守卫,
本文件只验本迁移的升/降级 SQL:升级把 idx_transcripts_fts 重建到 jiebacfg、降级还原 'simple'。
"""

from __future__ import annotations

import os
import subprocess
import sys

_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
    "REDIS_URL": "redis://localhost:6379/0",
}
_PREV = "b1c2d3e4f5a6"
_REV = "c1d2e3f4a5b6"


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


def test_upgrade_rebuilds_fts_index_with_jiebacfg() -> None:
    sql = _alembic_sql("upgrade", f"{_PREV}:{_REV}")
    assert "drop index" in sql and "idx_transcripts_fts" in sql
    assert "create index" in sql
    assert "using gin" in sql
    assert "to_tsvector('jiebacfg', content)" in sql
    assert "'simple'" not in sql


def test_downgrade_restores_simple_fts_index() -> None:
    sql = _alembic_sql("downgrade", f"{_REV}:{_PREV}")
    assert "drop index" in sql and "idx_transcripts_fts" in sql
    assert "to_tsvector('simple', content)" in sql
    assert "jiebacfg" not in sql
