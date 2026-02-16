"""Prompt template library API endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_user, get_current_user, get_current_user_optional, get_db
from app.core.response import success
from app.models.user import User
from app.schemas.common import PageResponse
from app.schemas.template import (
    TemplateCreateRequest,
    TemplateListItem,
    TemplateUpdateRequest,
)
from app.schemas.template_generate import (
    BatchGenerateRequest,
    GenerateRequest,
    VariantRequest,
)
from app.services.template_generator import TemplateGenerator
from app.services.template_service import TemplateService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])


# ============================================================================
# Fixed-path endpoints (must come before parameterized)
# ============================================================================


@router.get("")
async def list_templates(
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category: Optional[str] = Query(default=None),
    tags: Optional[str] = Query(default=None, description="Comma-separated tags"),
    difficulty: Optional[str] = Query(default=None),
    language: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    sort_by: str = Query(default="trending"),
) -> JSONResponse:
    """List templates with filtering, searching, and sorting."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    items, total = await TemplateService.list_templates(
        db,
        page=page,
        page_size=page_size,
        category=category,
        tags=tag_list,
        difficulty=difficulty,
        language=language,
        search=search,
        sort_by=sort_by,
    )

    response = PageResponse[TemplateListItem](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
    return success(data=jsonable_encoder(response))


@router.get("/categories")
async def get_categories(
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get all categories with template counts."""
    categories = await TemplateService.get_categories(db)
    return success(data=jsonable_encoder(categories))


@router.get("/favorites")
async def get_user_favorites(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> JSONResponse:
    """Get current user's favorited templates."""
    items, total = await TemplateService.get_user_favorites(
        db, user_id=user.id, page=page, page_size=page_size
    )

    response = PageResponse[TemplateListItem](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
    return success(data=jsonable_encoder(response))


@router.get("/recommended")
async def get_recommendations(
    db: AsyncSession = Depends(get_db),
    based_on: Optional[str] = Query(default=None, description="Template ID to base on"),
    tags: Optional[str] = Query(default=None, description="Comma-separated tags"),
    limit: int = Query(default=10, ge=1, le=50),
) -> JSONResponse:
    """Get recommended templates based on tags or a source template."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    items = await TemplateService.get_recommendations(
        db, based_on=based_on, tags=tag_list, limit=limit
    )
    return success(data=jsonable_encoder(items))


@router.post("/generate")
async def generate_templates(
    data: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
) -> JSONResponse:
    """Generate AI templates for a single category (admin only)."""
    generator = TemplateGenerator(db)
    try:
        stats = await generator.generate_templates_for_category(
            category=data.category,
            count=data.count,
            styles=data.styles,
        )
        return success(
            data=jsonable_encoder(
                {"stats": [stats], "total_generated": stats.generated, "total_saved": stats.saved}
            )
        )
    finally:
        await generator.close()


@router.post("/batch-generate")
async def batch_generate(
    data: BatchGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
) -> JSONResponse:
    """Batch-generate AI templates across categories (admin only)."""
    generator = TemplateGenerator(db)
    try:
        result = await generator.batch_generate(
            categories=data.categories,
            count_per_category=data.count_per_category,
        )
        return success(data=jsonable_encoder(result))
    finally:
        await generator.close()


@router.post("")
async def create_template(
    data: TemplateCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
) -> JSONResponse:
    """Create a new template (admin only)."""
    template = await TemplateService.create_template(db, data, user_id=user.id)
    return success(data=jsonable_encoder(template))


# ============================================================================
# Parameterized endpoints
# ============================================================================


@router.get("/{template_id}")
async def get_template_detail(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
) -> JSONResponse:
    """Get full template detail."""
    user_id = user.id if user else None
    detail = await TemplateService.get_template_detail(db, template_id, user_id=user_id)
    return success(data=jsonable_encoder(detail))


@router.post("/{template_id}/use")
async def record_usage(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
) -> JSONResponse:
    """Record that a template was used."""
    user_id = user.id if user else None
    detail = await TemplateService.record_usage(db, template_id, user_id=user_id)
    return success(data=jsonable_encoder(detail))


@router.post("/{template_id}/like")
async def toggle_like(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Toggle like on a template."""
    result = await TemplateService.toggle_like(db, template_id, user_id=user.id)
    return success(data=jsonable_encoder(result))


@router.post("/{template_id}/favorite")
async def toggle_favorite(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Toggle favorite on a template."""
    result = await TemplateService.toggle_favorite(db, template_id, user_id=user.id)
    return success(data=jsonable_encoder(result))


@router.post("/{template_id}/enhance")
async def enhance_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
) -> JSONResponse:
    """Enhance a template's prompt using AI (admin only)."""
    generator = TemplateGenerator(db)
    try:
        result = await generator.enhance_template(template_id)
        return success(data=jsonable_encoder(result))
    finally:
        await generator.close()


@router.post("/{template_id}/variants")
async def generate_variants(
    template_id: str,
    data: VariantRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
) -> JSONResponse:
    """Generate style variants of a template (admin only)."""
    generator = TemplateGenerator(db)
    try:
        result = await generator.generate_style_variants(template_id, data.target_styles)
        return success(data=jsonable_encoder(result))
    finally:
        await generator.close()


@router.put("/{template_id}")
async def update_template(
    template_id: str,
    data: TemplateUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
) -> JSONResponse:
    """Update a template (admin only)."""
    detail = await TemplateService.update_template(db, template_id, data)
    return success(data=jsonable_encoder(detail))


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
) -> JSONResponse:
    """Soft-delete a template (admin only)."""
    await TemplateService.delete_template(db, template_id)
    return success(data={"message": "Template deleted"})
