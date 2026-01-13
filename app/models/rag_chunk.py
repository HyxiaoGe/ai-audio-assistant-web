from __future__ import annotations

from typing import Optional

from sqlalchemy import DECIMAL, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class RagChunk(BaseRecord):
    __tablename__ = "rag_chunks"
    __table_args__ = (UniqueConstraint("task_id", "chunk_index", name="uk_rag_chunks_task"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transcript_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("transcripts.id", ondelete="SET NULL"), nullable=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3), nullable=True)
    end_time: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 3), nullable=True)
    speaker_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    embedding: Mapped[Optional[list[float]]] = mapped_column(ARRAY(Float), nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    embedding_dim: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
