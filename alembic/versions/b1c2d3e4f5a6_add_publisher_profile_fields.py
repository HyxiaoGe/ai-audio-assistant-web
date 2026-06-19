"""add publisher profile fields (user_profiles.display_name / avatar_url)

探索广场要展示「内容由谁公开」的名称+头像。但 audio 后端拿不到任意用户的 name/avatar:
auth-service 只返回 token 持有者本人,JWT 不带 name/avatar claim,本地 UserProfile 也无相应列。
故在本地 user_profiles 落两列,由「发布任务时(管理员已鉴权)用其 token 调 /auth/userinfo」捕获:

- user_profiles.display_name:展示名(发布时捕获,快照式)。
- user_profiles.avatar_url:头像源 URL(Google/GitHub 图床),前端经现有同源头像代理加载。

本迁移**只加列**(nullable / 无 server_default),不改任何写入或查询逻辑——老数据留 NULL,
公开端点 owner 为 None,前端不渲染发布者(沿用「NULL 不显示」哲学)。捕获写入与 API 透出在本 PR 后续提交。

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-06-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("display_name", sa.String(length=200), nullable=True))
    op.add_column("user_profiles", sa.Column("avatar_url", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "avatar_url")
    op.drop_column("user_profiles", "display_name")
