"""youtube_allowlist 迁移:offline up/down SQL 正确性 + 单 head。不起真实 DB。"""

import os
import subprocess
import sys

_ENV = {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost/x"}


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **_ENV}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_upgrade_offline_sql_creates_table() -> None:
    out = _alembic("upgrade", "b10c51d15914:c9d8e7f6a5b4", "--sql")
    assert out.returncode == 0, out.stderr
    sql = out.stdout
    assert "CREATE TABLE youtube_allowlist" in sql
    assert "uk_youtube_allowlist_entry" in sql
    assert "idx_youtube_allowlist_active" in sql


def test_downgrade_offline_sql_drops_table() -> None:
    out = _alembic("downgrade", "c9d8e7f6a5b4:b10c51d15914", "--sql")
    assert out.returncode == 0, out.stderr
    assert "DROP TABLE youtube_allowlist" in out.stdout


def test_single_alembic_head_is_allowlist_revision() -> None:
    out = _alembic("heads")
    assert out.returncode == 0, out.stderr
    heads = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert len(heads) == 1, f"alembic 出现多 head:{out.stdout}"
    assert "d1e2f3a4b5c6" in heads[0]
