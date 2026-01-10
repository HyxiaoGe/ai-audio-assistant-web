from __future__ import annotations

from typing import Optional

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
        Index(
            "idx_transcripts_fts",
            text("to_tsvector('simple', content)"),
            postgresql_using="gin",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )

    speaker_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    speaker_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    end_time: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    words: Mapped[Optional[list[dict[str, object]]]] = mapped_column(JSONB, nullable=True)

    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    is_edited: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    original_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
