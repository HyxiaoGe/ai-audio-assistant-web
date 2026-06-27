"""发布者身份「纯 schema」迁移:alembic offline up/down SQL 正确性 + 单 head。不起真实 DB。

探索广场要展示「内容由谁公开」的名称+头像,但 audio 后端拿不到任意用户的 name/avatar
(auth-service 只返回 token 持有者本人,JWT 无 name/avatar claim)。故在本地 user_profiles
落两列由发布时捕获:
- user_profiles.display_name:展示名(发布任务时从 /auth/userinfo 捕获)
- user_profiles.avatar_url:头像源 URL(Google/GitHub 图床,前端经同源代理加载)
纯加列,nullable,无 server_default;老数据 NULL → 前端不渲染发布者(沿用 NULL 不显示哲学)。
"""

from __future__ import annotations

import os
import subprocess
import sys

_NEW_REV = "b1c2d3e4f5a6"
_PREV_HEAD = "a0b1c2d3e4f5"

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


def test_upgrade_sql_adds_publisher_profile_columns() -> None:
    sql = _alembic_sql("upgrade", f"{_PREV_HEAD}:{_NEW_REV}")
    assert "add column display_name" in sql
    assert "add column avatar_url" in sql


def test_downgrade_sql_drops_publisher_profile_columns() -> None:
    sql = _alembic_sql("downgrade", f"{_NEW_REV}:{_PREV_HEAD}")
    assert "drop column display_name" in sql
    assert "drop column avatar_url" in sql


def test_single_alembic_head_is_new_revision() -> None:
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
    # head 已随新迁移前移至 youtube_blocklist.display_name 列 b10c51d15914。
    assert "b10c51d15914" in heads[0]
