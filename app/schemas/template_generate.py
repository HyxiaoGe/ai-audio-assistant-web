"""Schemas for AI template generation endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.template import TemplateDetailResponse

# ============================================================================
# Request Schemas
# ============================================================================


class GenerateRequest(BaseModel):
    """Request to generate templates for a single category."""

    category: str
    count: int = Field(default=10, ge=1, le=50)
    styles: list[str] | None = None


class BatchGenerateRequest(BaseModel):
    """Request to batch-generate templates across categories."""

    categories: list[str] | None = None  # None = all categories
    count_per_category: int = Field(default=10, ge=1, le=50)


class EnhanceRequest(BaseModel):
    """Request to enhance a template (no body needed, template_id from URL)."""

    pass


class VariantRequest(BaseModel):
    """Request to generate style variants of a template."""

    target_styles: list[str]


# ============================================================================
# Response Schemas
# ============================================================================


class GenerateStats(BaseModel):
    """Stats for a single category generation run."""

    category: str
    generated: int
    passed_quality: int
    saved: int


class GenerateResponse(BaseModel):
    """Response for template generation (single or batch)."""

    stats: list[GenerateStats]
    total_generated: int
    total_saved: int


class EnhanceResponse(BaseModel):
    """Response for template enhancement."""

    template_id: str
    original_prompt: str
    enhanced_prompt: str
    improvements: list[str]


class VariantResponse(BaseModel):
    """Response for style variant generation."""

    source_template_id: str
    variants_created: list[TemplateDetailResponse]
