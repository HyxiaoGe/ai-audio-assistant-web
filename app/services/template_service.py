"""Template library service layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.prompt_template import PromptTemplate
from app.models.user_template_favorite import UserTemplateFavorite
from app.models.user_template_like import UserTemplateLike
from app.models.user_template_usage import UserTemplateUsage
from app.schemas.template import (
    CategoryItem,
    TemplateCreateRequest,
    TemplateDetailResponse,
    TemplateListItem,
    TemplateUpdateRequest,
    ToggleResponse,
)

CATEGORIES = [
    "portrait",
    "landscape",
    "illustration",
    "product",
    "architecture",
    "anime",
    "fantasy",
    "graphic-design",
    "food",
    "abstract",
]


class TemplateService:
    @staticmethod
    async def list_templates(
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
        difficulty: Optional[str] = None,
        language: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "trending",
    ) -> tuple[list[TemplateListItem], int]:
        """List templates with filtering, searching, and sorting."""
        query = select(PromptTemplate).where(
            PromptTemplate.deleted_at.is_(None),
            PromptTemplate.is_active.is_(True),
        )

        if category:
            query = query.where(PromptTemplate.category == category)

        if tags:
            query = query.where(PromptTemplate.tags.overlap(tags))

        if difficulty:
            query = query.where(PromptTemplate.difficulty == difficulty)

        if language:
            query = query.where(PromptTemplate.language == language)

        if search:
            pattern = f"%{search}%"
            query = query.where(
                PromptTemplate.display_name_en.ilike(pattern)
                | PromptTemplate.display_name_zh.ilike(pattern)
                | PromptTemplate.prompt_text.ilike(pattern)
            )

        # Total count
        count_query = select(func.count()).select_from(query.subquery())
        total = await db.scalar(count_query) or 0

        # Sorting
        sort_map = {
            "trending": PromptTemplate.trending_score.desc(),
            "newest": PromptTemplate.created_at.desc(),
            "most_used": PromptTemplate.use_count.desc(),
            "most_liked": PromptTemplate.like_count.desc(),
        }
        order = sort_map.get(sort_by, PromptTemplate.trending_score.desc())
        query = query.order_by(order)

        # Pagination
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(query)
        templates = result.scalars().all()

        items = [TemplateListItem.model_validate(t) for t in templates]
        return items, total

    @staticmethod
    async def get_categories(db: AsyncSession) -> list[CategoryItem]:
        """Get all categories with template counts."""
        query = (
            select(PromptTemplate.category, func.count().label("count"))
            .where(
                PromptTemplate.deleted_at.is_(None),
                PromptTemplate.is_active.is_(True),
            )
            .group_by(PromptTemplate.category)
            .order_by(func.count().desc())
        )
        result = await db.execute(query)
        rows = result.all()
        return [CategoryItem(category=row[0], count=row[1]) for row in rows]

    @staticmethod
    async def get_template_detail(
        db: AsyncSession,
        template_id: str,
        user_id: Optional[str] = None,
    ) -> TemplateDetailResponse:
        """Get full template detail with user-specific like/favorite status."""
        result = await db.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id,
                PromptTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND)

        response = TemplateDetailResponse.model_validate(template)

        if user_id:
            # Check like status
            like_result = await db.execute(
                select(UserTemplateLike).where(
                    UserTemplateLike.user_id == user_id,
                    UserTemplateLike.template_id == template_id,
                )
            )
            response.is_liked = like_result.scalar_one_or_none() is not None

            # Check favorite status
            fav_result = await db.execute(
                select(UserTemplateFavorite).where(
                    UserTemplateFavorite.user_id == user_id,
                    UserTemplateFavorite.template_id == template_id,
                )
            )
            response.is_favorited = fav_result.scalar_one_or_none() is not None

        return response

    @staticmethod
    async def record_usage(
        db: AsyncSession,
        template_id: str,
        user_id: Optional[str] = None,
    ) -> TemplateDetailResponse:
        """Record template usage and increment use_count."""
        # Verify template exists
        result = await db.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id,
                PromptTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND)

        # Insert usage record
        usage = UserTemplateUsage(template_id=template_id, user_id=user_id)
        db.add(usage)

        # Increment use_count
        await db.execute(
            update(PromptTemplate)
            .where(PromptTemplate.id == template_id)
            .values(use_count=PromptTemplate.use_count + 1)
        )

        await db.commit()

        # Refresh trending score
        await TemplateService.refresh_trending_score(db, template_id)

        await db.refresh(template)
        return TemplateDetailResponse.model_validate(template)

    @staticmethod
    async def toggle_like(
        db: AsyncSession,
        template_id: str,
        user_id: str,
    ) -> ToggleResponse:
        """Toggle like on a template. Returns action and new count."""
        # Verify template exists
        result = await db.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id,
                PromptTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND)

        # Check existing like
        existing = await db.execute(
            select(UserTemplateLike).where(
                UserTemplateLike.user_id == user_id,
                UserTemplateLike.template_id == template_id,
            )
        )
        existing_like = existing.scalar_one_or_none()

        if existing_like:
            # Remove like
            await db.execute(
                delete(UserTemplateLike).where(
                    UserTemplateLike.user_id == user_id,
                    UserTemplateLike.template_id == template_id,
                )
            )
            await db.execute(
                update(PromptTemplate)
                .where(PromptTemplate.id == template_id)
                .values(like_count=PromptTemplate.like_count - 1)
            )
            action = "removed"
        else:
            # Add like
            like = UserTemplateLike(user_id=user_id, template_id=template_id)
            db.add(like)
            await db.execute(
                update(PromptTemplate)
                .where(PromptTemplate.id == template_id)
                .values(like_count=PromptTemplate.like_count + 1)
            )
            action = "added"

        await db.commit()

        # Refresh trending score
        await TemplateService.refresh_trending_score(db, template_id)

        await db.refresh(template)
        return ToggleResponse(action=action, count=template.like_count)

    @staticmethod
    async def toggle_favorite(
        db: AsyncSession,
        template_id: str,
        user_id: str,
    ) -> ToggleResponse:
        """Toggle favorite on a template. Returns action and new count."""
        # Verify template exists
        result = await db.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id,
                PromptTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND)

        # Check existing favorite
        existing = await db.execute(
            select(UserTemplateFavorite).where(
                UserTemplateFavorite.user_id == user_id,
                UserTemplateFavorite.template_id == template_id,
            )
        )
        existing_fav = existing.scalar_one_or_none()

        if existing_fav:
            # Remove favorite
            await db.execute(
                delete(UserTemplateFavorite).where(
                    UserTemplateFavorite.user_id == user_id,
                    UserTemplateFavorite.template_id == template_id,
                )
            )
            await db.execute(
                update(PromptTemplate)
                .where(PromptTemplate.id == template_id)
                .values(favorite_count=PromptTemplate.favorite_count - 1)
            )
            action = "removed"
        else:
            # Add favorite
            fav = UserTemplateFavorite(user_id=user_id, template_id=template_id)
            db.add(fav)
            await db.execute(
                update(PromptTemplate)
                .where(PromptTemplate.id == template_id)
                .values(favorite_count=PromptTemplate.favorite_count + 1)
            )
            action = "added"

        await db.commit()

        # Refresh trending score
        await TemplateService.refresh_trending_score(db, template_id)

        await db.refresh(template)
        return ToggleResponse(action=action, count=template.favorite_count)

    @staticmethod
    async def get_user_favorites(
        db: AsyncSession,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TemplateListItem], int]:
        """Get user's favorited templates, paginated."""
        query = (
            select(PromptTemplate)
            .join(
                UserTemplateFavorite,
                UserTemplateFavorite.template_id == PromptTemplate.id,
            )
            .where(
                UserTemplateFavorite.user_id == user_id,
                PromptTemplate.deleted_at.is_(None),
            )
        )

        count_query = select(func.count()).select_from(query.subquery())
        total = await db.scalar(count_query) or 0

        query = query.order_by(UserTemplateFavorite.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(query)
        templates = result.scalars().all()

        items = [TemplateListItem.model_validate(t) for t in templates]
        return items, total

    @staticmethod
    async def get_recommendations(
        db: AsyncSession,
        based_on: Optional[str] = None,
        tags: Optional[list[str]] = None,
        limit: int = 10,
    ) -> list[TemplateListItem]:
        """Get recommended templates based on a source template's tags or explicit tags."""
        target_tags: list[str] = []

        if based_on:
            result = await db.execute(
                select(PromptTemplate.tags).where(
                    PromptTemplate.id == based_on,
                    PromptTemplate.deleted_at.is_(None),
                )
            )
            row = result.scalar_one_or_none()
            if row:
                target_tags = list(row)

        if tags:
            target_tags.extend(tags)

        if not target_tags:
            # Fallback: return top trending
            query = (
                select(PromptTemplate)
                .where(
                    PromptTemplate.deleted_at.is_(None),
                    PromptTemplate.is_active.is_(True),
                )
                .order_by(PromptTemplate.trending_score.desc())
                .limit(limit)
            )
            result = await db.execute(query)
            templates = result.scalars().all()
            return [TemplateListItem.model_validate(t) for t in templates]

        query = (
            select(PromptTemplate)
            .where(
                PromptTemplate.deleted_at.is_(None),
                PromptTemplate.is_active.is_(True),
                PromptTemplate.tags.overlap(target_tags),
            )
            .order_by(
                PromptTemplate.trending_score.desc(),
            )
            .limit(limit)
        )

        # Exclude the source template
        if based_on:
            query = query.where(PromptTemplate.id != based_on)

        result = await db.execute(query)
        templates = result.scalars().all()
        return [TemplateListItem.model_validate(t) for t in templates]

    @staticmethod
    async def create_template(
        db: AsyncSession,
        data: TemplateCreateRequest,
        user_id: Optional[str] = None,
    ) -> TemplateDetailResponse:
        """Create a new template."""
        template = PromptTemplate(
            **data.model_dump(),
            created_by=user_id,
        )
        db.add(template)
        await db.commit()
        await db.refresh(template)
        return TemplateDetailResponse.model_validate(template)

    @staticmethod
    async def update_template(
        db: AsyncSession,
        template_id: str,
        data: TemplateUpdateRequest,
    ) -> TemplateDetailResponse:
        """Update a template (partial update)."""
        result = await db.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id,
                PromptTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND)

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(template, key, value)

        await db.commit()
        await db.refresh(template)
        return TemplateDetailResponse.model_validate(template)

    @staticmethod
    async def delete_template(
        db: AsyncSession,
        template_id: str,
    ) -> None:
        """Soft delete a template."""
        result = await db.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id,
                PromptTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND)

        template.deleted_at = datetime.now(timezone.utc)
        await db.commit()

    @staticmethod
    def compute_trending_score(
        like_count: int,
        use_count: int,
        favorite_count: int,
        created_at: datetime,
    ) -> float:
        """Compute trending score: (like*3 + use*1 + fav*2) / (hours + 2)^1.5"""
        now = datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        hours = (now - created_at).total_seconds() / 3600
        numerator = like_count * 3 + use_count * 1 + favorite_count * 2
        denominator = (hours + 2) ** 1.5
        return numerator / denominator

    @staticmethod
    async def refresh_trending_score(
        db: AsyncSession,
        template_id: str,
    ) -> None:
        """Recompute and update the trending score for a single template."""
        result = await db.execute(select(PromptTemplate).where(PromptTemplate.id == template_id))
        template = result.scalar_one_or_none()
        if not template:
            return

        score = TemplateService.compute_trending_score(
            template.like_count,
            template.use_count,
            template.favorite_count,
            template.created_at,
        )
        await db.execute(
            update(PromptTemplate)
            .where(PromptTemplate.id == template_id)
            .values(trending_score=score)
        )
        await db.commit()
