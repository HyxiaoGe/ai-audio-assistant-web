from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class Transcript(BaseRecord):
    __tablename__ = "transcripts"
    __table_args__ = (
        Index("idx_transcripts_task", "task_id"),
        Index("idx_transcripts_sequence", "task_id", "sequence"),
        Index("idx_transcripts_speaker", "task_id", "speaker_id"),
        # 中文分词全文检索:用 pg_jieba 的 jiebacfg 配置('simple' 不切中文,整句=1 token,
        # 检索全不命中)。表达式须与搜索查询(transcript_search)所用配置一致,planner 才会用上此 GIN。
        # 前置:目标库须已 `CREATE EXTENSION pg_jieba`(由 superuser 手动建,app 用户无权);
        # 迁移 c1d2e3f4a5b6 负责重建该索引。
        Index(
            "idx_transcripts_fts",
            text("to_tsvector('jiebacfg', content)"),
            postgresql_using="gin",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )

    speaker_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    speaker_label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    end_time: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    words: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)

    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    is_edited: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    original_content: Mapped[str | None] = mapped_column(Text, nullable=True)
