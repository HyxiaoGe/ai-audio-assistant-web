from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class PromptTemplate(BaseModel):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        Index(
            "ix_prompt_templates_category",
            "category",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_prompt_templates_trending",
            "trending_score",
            postgresql_where=text("deleted_at IS NULL AND is_active = TRUE"),
        ),
        Index(
            "ix_prompt_templates_tags",
            "tags",
            postgresql_using="gin",
        ),
        Index(
            "ix_prompt_templates_use_count",
            "use_count",
            postgresql_where=text("deleted_at IS NULL AND is_active = TRUE"),
        ),
    )

    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    display_name_en: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name_zh: Mapped[str] = mapped_column(String(200), nullable=False)
    description_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_zh: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    preview_image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'::varchar[]"), nullable=False
    )
    style_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'::varchar[]"), nullable=False
    )
    parameters: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    difficulty: Mapped[str] = mapped_column(String(20), default="beginner", nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="bilingual", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="curated", nullable=False)
    use_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    like_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    favorite_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    trending_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0.0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    created_by: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
