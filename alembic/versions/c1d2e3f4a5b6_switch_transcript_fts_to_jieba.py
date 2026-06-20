"""switch transcripts FTS index from 'simple' to pg_jieba 'jiebacfg'

原 idx_transcripts_fts 建在 to_tsvector('simple', content) 上,'simple' 配置不切分中文
(整句被当成 1 个 token),导致「哪个视频提到 X」这类中文检索全不命中——该 GIN 索引对中文
形同虚设(dev 实证:'simple' 下 @@ '长城' 为 false,jiebacfg 下为 true)。

本迁移把该索引重建在 to_tsvector('jiebacfg', content) 上,启用 pg_jieba 中文分词,配合
transcript_search 的 websearch_to_tsquery('jiebacfg', ...) 查询。

前置依赖:目标库须已 `CREATE EXTENSION pg_jieba`。该扩展非 trusted,须由 superuser 手动建
(app 连接用户非 superuser,故不能放进本迁移)。共享 PG 镜像 postgres:15-cron-jieba 已内置
pg_jieba,扩展已在 audio_assistant 库手动创建。扩展缺失时 CREATE INDEX 会清晰报错。

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-06-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 前置:目标库须已 `CREATE EXTENSION pg_jieba`(扩展非 trusted,须 superuser 手动建,
    # 不放迁移)。缺失时下面 CREATE INDEX 会以 "text search configuration jiebacfg does not exist"
    # 失败。共享 PG(postgres:15-cron-jieba)已建好扩展。不在此做运行期存在性查询,以兼容
    # alembic 的离线 --sql 生成模式。
    op.drop_index("idx_transcripts_fts", table_name="transcripts", postgresql_using="gin")
    op.create_index(
        "idx_transcripts_fts",
        "transcripts",
        [sa.literal_column("to_tsvector('jiebacfg', content)")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("idx_transcripts_fts", table_name="transcripts", postgresql_using="gin")
    op.create_index(
        "idx_transcripts_fts",
        "transcripts",
        [sa.literal_column("to_tsvector('simple', content)")],
        postgresql_using="gin",
    )
