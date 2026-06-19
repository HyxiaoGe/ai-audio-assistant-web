"""全链路溯源「纯 schema」迁移:alembic offline up/down SQL 正确性 + 单 head。不起真实 DB。

Tier:转写/摘要/要点/待办/配图溯源增强的第一步(纯加列,nullable,零行为变化)。
- tasks 加 asr_engine / asr_variant(ASR 引擎与变体,Task 级溯源)
- summaries 加 prompt_slug(PromptHub 定位键)、input_tokens / output_tokens(真实 token 用量)、
  quality_tier(质量分类 high/medium/low,驱动了模型升级决策)
本步不改任何查询/写入逻辑,仅铺地基;捕获写入在后续 PR。配图 provider 走 JSONB(无迁移)。
"""

from __future__ import annotations

import os
import subprocess
import sys

_NEW_REV = "a0b1c2d3e4f5"
_PREV_HEAD = "f9a0b1c2d3e4"

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


def test_upgrade_sql_adds_provenance_columns() -> None:
    sql = _alembic_sql("upgrade", f"{_PREV_HEAD}:{_NEW_REV}")
    # tasks:ASR 引擎/变体
    assert "add column asr_engine" in sql
    assert "add column asr_variant" in sql
    # summaries:prompt 定位键 + 真 token + 质量分类
    assert "add column prompt_slug" in sql
    assert "add column input_tokens" in sql
    assert "add column output_tokens" in sql
    assert "add column quality_tier" in sql


def test_downgrade_sql_drops_provenance_columns() -> None:
    sql = _alembic_sql("downgrade", f"{_NEW_REV}:{_PREV_HEAD}")
    assert "drop column asr_engine" in sql
    assert "drop column asr_variant" in sql
    assert "drop column prompt_slug" in sql
    assert "drop column input_tokens" in sql
    assert "drop column output_tokens" in sql
    assert "drop column quality_tier" in sql


def test_single_alembic_head_no_fork() -> None:
    # a0b1c2d3e4f5 已不是最新 head(其上有发布者身份字段迁移 b1c2d3e4f5a6),故此处只校验
    # 迁移链无分叉(单 head),不再 pin 具体 head 字符串——head pin 的 canary 留给 notification /
    # summaries_images 两处。
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
