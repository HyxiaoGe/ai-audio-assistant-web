"""Tests for the prompt template library system."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.schemas.template import (
    CategoryItem,
    TemplateCreateRequest,
    TemplateDetailResponse,
    TemplateListItem,
    TemplateUpdateRequest,
    ToggleResponse,
)
from app.services.template_service import CATEGORIES, TemplateService

# ============================================================================
# Trending Score Computation
# ============================================================================


class TestTrendingScore:
    def test_basic_formula(self) -> None:
        """Verify (like*3 + use*1 + fav*2) / (hours + 2)^1.5"""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(hours=10)
        score = TemplateService.compute_trending_score(
            like_count=10, use_count=100, favorite_count=5, created_at=created_at
        )
        # numerator = 10*3 + 100*1 + 5*2 = 140
        # denominator = (10 + 2)^1.5 = 12^1.5
        expected = 140 / (12**1.5)
        assert abs(score - expected) < 0.001

    def test_zero_engagement(self) -> None:
        now = datetime.now(timezone.utc)
        score = TemplateService.compute_trending_score(0, 0, 0, now)
        assert score == 0.0

    def test_newer_scores_higher(self) -> None:
        """A newer template with same engagement should score higher."""
        now = datetime.now(timezone.utc)
        recent = now - timedelta(hours=1)
        old = now - timedelta(hours=100)

        score_recent = TemplateService.compute_trending_score(10, 50, 5, recent)
        score_old = TemplateService.compute_trending_score(10, 50, 5, old)
        assert score_recent > score_old

    def test_likes_weight_more_than_uses(self) -> None:
        """Likes (weight=3) should contribute more than uses (weight=1)."""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(hours=10)

        score_likes = TemplateService.compute_trending_score(100, 0, 0, created_at)
        score_uses = TemplateService.compute_trending_score(0, 100, 0, created_at)
        assert score_likes > score_uses

    def test_naive_datetime_handled(self) -> None:
        """Naive datetimes should be treated as UTC."""
        naive_dt = datetime(2026, 1, 1, 0, 0, 0)
        score = TemplateService.compute_trending_score(10, 10, 10, naive_dt)
        assert score > 0


# ============================================================================
# Schema Validation
# ============================================================================


class TestSchemas:
    def test_template_list_item_from_attributes(self) -> None:
        """TemplateListItem can be created from ORM-like objects."""
        mock_obj = MagicMock()
        mock_obj.id = str(uuid4())
        mock_obj.display_name_en = "Test"
        mock_obj.display_name_zh = "测试"
        mock_obj.preview_image_url = None
        mock_obj.category = "portrait"
        mock_obj.tags = ["test"]
        mock_obj.difficulty = "beginner"
        mock_obj.use_count = 10
        mock_obj.like_count = 5
        mock_obj.favorite_count = 3
        mock_obj.source = "curated"
        mock_obj.trending_score = 1.5
        mock_obj.created_at = datetime.now(timezone.utc)

        item = TemplateListItem.model_validate(mock_obj, from_attributes=True)
        assert item.display_name_en == "Test"
        assert item.category == "portrait"

    def test_template_detail_response_defaults(self) -> None:
        """TemplateDetailResponse has default is_liked/is_favorited = False."""
        detail = TemplateDetailResponse(
            id=str(uuid4()),
            prompt_text="test prompt",
            display_name_en="Test",
            display_name_zh="测试",
            category="portrait",
            tags=[],
            style_keywords=[],
            parameters={},
            difficulty="beginner",
            language="bilingual",
            source="curated",
            use_count=0,
            like_count=0,
            favorite_count=0,
            trending_score=0.0,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert detail.is_liked is False
        assert detail.is_favorited is False

    def test_toggle_response(self) -> None:
        resp = ToggleResponse(action="added", count=5)
        assert resp.action == "added"
        assert resp.count == 5

    def test_category_item(self) -> None:
        item = CategoryItem(category="portrait", count=10)
        assert item.category == "portrait"
        assert item.count == 10

    def test_create_request_defaults(self) -> None:
        req = TemplateCreateRequest(
            prompt_text="test",
            display_name_en="Test",
            display_name_zh="测试",
            category="portrait",
        )
        assert req.difficulty == "beginner"
        assert req.language == "bilingual"
        assert req.source == "curated"
        assert req.tags == []
        assert req.style_keywords == []
        assert req.parameters == {}

    def test_update_request_all_optional(self) -> None:
        req = TemplateUpdateRequest()
        data = req.model_dump(exclude_unset=True)
        assert data == {}

    def test_update_request_partial(self) -> None:
        req = TemplateUpdateRequest(display_name_en="Updated Name")
        data = req.model_dump(exclude_unset=True)
        assert data == {"display_name_en": "Updated Name"}


# ============================================================================
# Categories Constant
# ============================================================================


class TestCategories:
    def test_ten_categories(self) -> None:
        assert len(CATEGORIES) == 10

    def test_expected_categories(self) -> None:
        expected = {
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
        }
        assert set(CATEGORIES) == expected


# ============================================================================
# Service Layer (mocked DB)
# ============================================================================


class TestTemplateServiceListTemplates:
    @pytest.mark.asyncio
    async def test_list_templates_basic(self) -> None:
        """list_templates returns paginated results."""
        mock_db = AsyncMock()
        mock_template = MagicMock()
        mock_template.id = str(uuid4())
        mock_template.display_name_en = "Test"
        mock_template.display_name_zh = "测试"
        mock_template.preview_image_url = None
        mock_template.category = "portrait"
        mock_template.tags = ["test"]
        mock_template.difficulty = "beginner"
        mock_template.use_count = 10
        mock_template.like_count = 5
        mock_template.favorite_count = 3
        mock_template.source = "curated"
        mock_template.trending_score = 1.5
        mock_template.created_at = datetime.now(timezone.utc)

        # Mock scalar (count)
        mock_db.scalar = AsyncMock(return_value=1)

        # Mock execute for the query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_template]
        mock_db.execute = AsyncMock(return_value=mock_result)

        items, total = await TemplateService.list_templates(mock_db)
        assert total == 1
        assert len(items) == 1
        assert items[0].display_name_en == "Test"


class TestTemplateServiceToggleLike:
    @pytest.mark.asyncio
    async def test_toggle_like_add(self) -> None:
        """toggle_like adds a like when none exists."""
        template_id = str(uuid4())
        user_id = str(uuid4())

        mock_db = AsyncMock()

        # First execute: select template
        mock_template = MagicMock()
        mock_template.id = template_id
        mock_template.like_count = 1
        mock_template.use_count = 0
        mock_template.favorite_count = 0
        mock_template.created_at = datetime.now(timezone.utc)

        # Second execute: select existing like (None)
        mock_no_like = MagicMock()
        mock_no_like.scalar_one_or_none.return_value = None

        # Third+ execute: update + insert
        call_count = 0

        async def mock_execute(stmt: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                result = MagicMock()
                result.scalar_one_or_none.return_value = mock_template
                return result
            elif call_count == 2:
                return mock_no_like
            return MagicMock()

        mock_db.execute = mock_execute
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Mock refresh_trending_score
        with patch.object(TemplateService, "refresh_trending_score", new_callable=AsyncMock):
            result = await TemplateService.toggle_like(mock_db, template_id, user_id)

        assert result.action == "added"


class TestTemplateServiceToggleFavorite:
    @pytest.mark.asyncio
    async def test_toggle_favorite_add(self) -> None:
        """toggle_favorite adds a favorite when none exists."""
        template_id = str(uuid4())
        user_id = str(uuid4())

        mock_db = AsyncMock()

        mock_template = MagicMock()
        mock_template.id = template_id
        mock_template.favorite_count = 1
        mock_template.use_count = 0
        mock_template.like_count = 0
        mock_template.created_at = datetime.now(timezone.utc)

        mock_no_fav = MagicMock()
        mock_no_fav.scalar_one_or_none.return_value = None

        call_count = 0

        async def mock_execute(stmt: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                result = MagicMock()
                result.scalar_one_or_none.return_value = mock_template
                return result
            elif call_count == 2:
                return mock_no_fav
            return MagicMock()

        mock_db.execute = mock_execute
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch.object(TemplateService, "refresh_trending_score", new_callable=AsyncMock):
            result = await TemplateService.toggle_favorite(mock_db, template_id, user_id)

        assert result.action == "added"


class TestTemplateServiceGetDetail:
    @pytest.mark.asyncio
    async def test_get_detail_not_found(self) -> None:
        """get_template_detail raises BusinessError when not found."""
        from app.core.exceptions import BusinessError

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(BusinessError):
            await TemplateService.get_template_detail(mock_db, str(uuid4()))

    @pytest.mark.asyncio
    async def test_get_detail_anonymous(self) -> None:
        """get_template_detail works without user_id (no like/fav check)."""
        mock_db = AsyncMock()
        template_id = str(uuid4())

        mock_template = MagicMock(spec=[])
        mock_template.id = template_id
        mock_template.prompt_text = "test"
        mock_template.display_name_en = "Test"
        mock_template.display_name_zh = "测试"
        mock_template.description_en = None
        mock_template.description_zh = None
        mock_template.preview_image_url = None
        mock_template.category = "portrait"
        mock_template.tags = []
        mock_template.style_keywords = []
        mock_template.parameters = {}
        mock_template.difficulty = "beginner"
        mock_template.language = "bilingual"
        mock_template.source = "curated"
        mock_template.use_count = 0
        mock_template.like_count = 0
        mock_template.favorite_count = 0
        mock_template.trending_score = 0.0
        mock_template.is_active = True
        mock_template.created_by = None
        mock_template.created_at = datetime.now(timezone.utc)
        mock_template.updated_at = datetime.now(timezone.utc)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute = AsyncMock(return_value=mock_result)

        detail = await TemplateService.get_template_detail(mock_db, template_id)
        assert detail.id == template_id
        assert detail.is_liked is False
        assert detail.is_favorited is False


class TestTemplateServiceCreate:
    @pytest.mark.asyncio
    async def test_create_template(self) -> None:
        """create_template persists and returns the template."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        template_id = str(uuid4())
        now = datetime.now(timezone.utc)

        async def mock_refresh(obj: object) -> None:
            obj.id = template_id  # type: ignore[attr-defined]
            obj.created_at = now  # type: ignore[attr-defined]
            obj.updated_at = now  # type: ignore[attr-defined]
            obj.deleted_at = None  # type: ignore[attr-defined]
            obj.use_count = 0  # type: ignore[attr-defined]
            obj.like_count = 0  # type: ignore[attr-defined]
            obj.favorite_count = 0  # type: ignore[attr-defined]
            obj.trending_score = 0.0  # type: ignore[attr-defined]
            obj.is_active = True  # type: ignore[attr-defined]

        mock_db.refresh = mock_refresh

        data = TemplateCreateRequest(
            prompt_text="test prompt",
            display_name_en="Test",
            display_name_zh="测试",
            category="portrait",
        )

        result = await TemplateService.create_template(mock_db, data, user_id=str(uuid4()))
        assert result.display_name_en == "Test"
        mock_db.add.assert_called_once()


class TestTemplateServiceDelete:
    @pytest.mark.asyncio
    async def test_delete_template_not_found(self) -> None:
        """delete_template raises BusinessError when not found."""
        from app.core.exceptions import BusinessError

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(BusinessError):
            await TemplateService.delete_template(mock_db, str(uuid4()))

    @pytest.mark.asyncio
    async def test_delete_template_sets_deleted_at(self) -> None:
        """delete_template sets deleted_at on the template."""
        mock_db = AsyncMock()
        mock_template = MagicMock()
        mock_template.deleted_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        await TemplateService.delete_template(mock_db, str(uuid4()))
        assert mock_template.deleted_at is not None
