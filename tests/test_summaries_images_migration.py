"""images 迁移：alembic offline up/down SQL 正确性 + 单 head。不起真实 DB。"""

from __future__ import annotations

import subprocess

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


def test_upgrade_sql_adds_images_jsonb_column() -> None:
    sql = _alembic_sql("upgrade", "9a1b2c3d4e5f:b7c3d9e1f2a0")
    assert "alter table summaries add column images jsonb" in sql


def test_downgrade_sql_drops_images_column() -> None:
    sql = _alembic_sql("downgrade", "b7c3d9e1f2a0:9a1b2c3d4e5f")
    assert "drop column images" in sql


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
    # 必须恰好单 head（否则说明迁移链分叉）。head 随新迁移前移至 /discover 黑名单表 d7b9f3a1c5e2。
    assert len(heads) == 1, f"alembic 出现多 head：{out.stdout}"
    assert "d7b9f3a1c5e2" in heads[0]
