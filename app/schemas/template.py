"""Prompt template schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# ============================================================================
# Response Schemas
# ============================================================================


class TemplateListItem(BaseModel):
    """Compact template for list views."""

    id: str
    display_name_en: str
    display_name_zh: str
    preview_image_url: Optional[str] = None
    category: str
    tags: list[str] = Field(default_factory=list)
    difficulty: str
    use_count: int
    like_count: int
    favorite_count: int
    source: str
    trending_score: float
    created_at: datetime

    model_config = {"from_attributes": True}


class TemplateDetailResponse(BaseModel):
    """Full template detail."""

    id: str
    prompt_text: str
    display_name_en: str
    display_name_zh: str
    description_en: Optional[str] = None
    description_zh: Optional[str] = None
    preview_image_url: Optional[str] = None
    category: str
    tags: list[str] = Field(default_factory=list)
    style_keywords: list[str] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)
    difficulty: str
    language: str
    source: str
    use_count: int
    like_count: int
    favorite_count: int
    trending_score: float
    is_active: bool
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Computed at runtime per user
    is_liked: bool = False
    is_favorited: bool = False

    model_config = {"from_attributes": True}


# ============================================================================
# Request Schemas
# ============================================================================


class TemplateCreateRequest(BaseModel):
    """Request to create a template."""

    prompt_text: str
    display_name_en: str
    display_name_zh: str
    description_en: Optional[str] = None
    description_zh: Optional[str] = None
    preview_image_url: Optional[str] = None
    category: str
    tags: list[str] = Field(default_factory=list)
    style_keywords: list[str] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)
    difficulty: str = "beginner"
    language: str = "bilingual"
    source: str = "curated"


class TemplateUpdateRequest(BaseModel):
    """Request to update a template (all fields optional)."""

    prompt_text: Optional[str] = None
    display_name_en: Optional[str] = None
    display_name_zh: Optional[str] = None
    description_en: Optional[str] = None
    description_zh: Optional[str] = None
    preview_image_url: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    style_keywords: Optional[list[str]] = None
    parameters: Optional[dict] = None
    difficulty: Optional[str] = None
    language: Optional[str] = None
    source: Optional[str] = None
    is_active: Optional[bool] = None


# ============================================================================
# Utility Schemas
# ============================================================================


class CategoryItem(BaseModel):
    """Category with template count."""

    category: str
    count: int


class ToggleResponse(BaseModel):
    """Response for like/favorite toggle."""

    action: str  # "added" or "removed"
    count: int
