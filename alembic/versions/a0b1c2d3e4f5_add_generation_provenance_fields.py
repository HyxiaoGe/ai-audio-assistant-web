"""add generation provenance fields (asr engine/variant + summary prompt_slug/tokens/quality)

转写/摘要/要点/待办/配图全链路溯源增强的「纯 schema」第一步。记录「谁/用什么生成的」
里目前算出来了却被扔掉的那部分:

- tasks.asr_engine:ASR 引擎/模型(如 tencent 的 16k_zh),运行时来自 service/ConfigManager,用完即弃。
- tasks.asr_variant:实际执行的 ASR 变体(file / file_fast),asr_usage 里有但 Task 上没有。
- summaries.prompt_slug:PromptHub 定位具体提示词的唯一键(如 summary-overview-meeting-zh),
  比硬编码的 prompt_version 有用得多;仅在内存流转,未落库。
- summaries.input_tokens / output_tokens:LiteLLM 返回的真实 token 用量(现 token_count 实为字符数)。
- summaries.quality_tier:转写质量分类(high/medium/low),驱动了「低质量→premium 模型」的升级决策,
  现仅 log。注意是分类**字符串**(TranscriptQuality.quality_score: str),非数值。

本迁移**只加列**(全部 nullable / 无 server_default),不改任何写入或查询逻辑——线上行为不变,
老数据留 NULL(经决策不回填:运行时已永久丢失,回填只能填猜测值会污染审计可信度)。
捕获写入、API 透出、前端徽章在后续 PR。配图 provider 走 Summary.images JSONB(无需迁移)。

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-06-19

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a0b1c2d3e4f5"
down_revision: str | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Task 级 ASR 溯源:provider 已有(Task.asr_provider),补 engine 与实际执行变体。
    op.add_column("tasks", sa.Column("asr_engine", sa.String(length=50), nullable=True))
    op.add_column("tasks", sa.Column("asr_variant", sa.String(length=20), nullable=True))
    # Summary(含 overview/key_points/action_items/chapters,同表按 summary_type 分行)溯源补全。
    op.add_column("summaries", sa.Column("prompt_slug", sa.String(length=100), nullable=True))
    op.add_column("summaries", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("summaries", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column("summaries", sa.Column("quality_tier", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("summaries", "quality_tier")
    op.drop_column("summaries", "output_tokens")
    op.drop_column("summaries", "input_tokens")
    op.drop_column("summaries", "prompt_slug")
    op.drop_column("tasks", "asr_variant")
    op.drop_column("tasks", "asr_engine")
