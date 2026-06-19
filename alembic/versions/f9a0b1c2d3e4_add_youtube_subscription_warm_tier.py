"""add youtube subscription warm tier (is_warm + last_active_at)

Tier 2 懒加载主体的「纯 schema」一步:给 youtube_subscriptions 加系统独占的冷热位。

- is_warm:系统管理的「热(周期同步)/冷(懒加载)」位。**刻意不复用 sync_enabled**——
  后者是用户手动开关(PATCH settings 可写),且 sync_channel_videos 见 sync_enabled=false
  会直接 skip,冷频道复用它会让按需拉取失效。调度真正资格 = is_warm AND sync_enabled。
- last_active_at:最近一次真实用户行为(打开/转写/星标)的时间,供将来降级/观测用。

本迁移**只加列+索引+回填存量**,不改任何查询(无人按 is_warm 过滤),线上行为不变。

Revision ID: f9a0b1c2d3e4
Revises: f6a7b8c9d0e1
Create Date: 2026-06-19

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "youtube_subscriptions",
        sa.Column("is_warm", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "youtube_subscriptions",
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 调度热路径专用部分索引:check_scheduled_syncs 将来按 (is_warm AND sync_enabled) 选行,
    # 只覆盖热且用户未手动关同步的行,冷/手动关的行不进索引。
    op.create_index(
        "idx_youtube_subscriptions_warm",
        "youtube_subscriptions",
        ["next_sync_at"],
        postgresql_where=sa.text("is_warm = true AND sync_enabled = true"),
    )
    # 回填:祖父留热——凡同步过(videos_synced_at 非空)/已星标/已开自动转写的频道都置 is_warm=true,
    # 避免存量用户依赖的频道在 gating 上线后突然停同步。last_active_at 取 videos_synced_at 兜底 updated_at,
    # 给将来的降级时钟一个合理起点。其余(从未同步且未关注)留冷(server_default false)。
    # 只翻标志、绝不删 youtube_videos,故老用户 feed 当天不掉内容。
    op.execute(
        """
        UPDATE youtube_subscriptions
        SET is_warm = true,
            last_active_at = COALESCE(videos_synced_at, updated_at)
        WHERE videos_synced_at IS NOT NULL
           OR is_starred = true
           OR auto_transcribe = true
        """
    )


def downgrade() -> None:
    op.drop_index("idx_youtube_subscriptions_warm", table_name="youtube_subscriptions")
    op.drop_column("youtube_subscriptions", "last_active_at")
    op.drop_column("youtube_subscriptions", "is_warm")
