"""Tests for the AI template generation pipeline."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.prompt_template import PromptTemplate
from app.schemas.template_generate import (
    EnhanceResponse,
    GenerateResponse,
    GenerateStats,
    VariantResponse,
)
from app.services.template_generator import CATEGORY_STYLES, TemplateGenerator

# ============================================================================
# Fixtures
# ============================================================================


def _make_template(**overrides: Any) -> PromptTemplate:
    """Create a PromptTemplate with sensible defaults."""
    defaults: dict[str, Any] = {
        "id": str(uuid4()),
        "prompt_text": "A cinematic portrait with golden hour lighting",
        "display_name_en": "Golden Hour Portrait",
        "display_name_zh": "黄金时刻人像",
        "description_en": "Cinematic portrait",
        "description_zh": "电影级人像",
        "category": "portrait",
        "tags": ["portrait", "cinematic"],
        "style_keywords": ["cinematic", "golden-hour"],
        "difficulty": "beginner",
        "language": "bilingual",
        "source": "curated",
        "use_count": 0,
        "like_count": 0,
        "favorite_count": 0,
        "trending_score": 0.0,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "deleted_at": None,
        "created_by": None,
    }
    defaults.update(overrides)
    tpl = MagicMock(spec=PromptTemplate)
    for k, v in defaults.items():
        setattr(tpl, k, v)
    return tpl


def _mock_llm_response(data: Any) -> str:
    """Return a JSON string as if from an LLM."""
    return json.dumps(data)


# ============================================================================
# Tag Extraction
# ============================================================================


class TestExtractTags:
    @pytest.mark.asyncio
    async def test_extract_tags_parses_json(self) -> None:
        """LLM returns JSON with tags and style_keywords → parsed correctly."""
        db = AsyncMock()
        gen = TemplateGenerator(db)

        llm_output = _mock_llm_response(
            {
                "tags": ["portrait", "golden-hour", "cinematic"],
                "style_keywords": ["cinematic", "film grain"],
            }
        )

        with (
            patch.object(
                gen, "_get_meta_prompt", new_callable=AsyncMock, return_value="system msg"
            ),
            patch.object(gen, "_call_llm", new_callable=AsyncMock, return_value=llm_output),
        ):
            tags, style_kw = await gen._extract_tags("A cinematic portrait...")

        assert tags == ["portrait", "golden-hour", "cinematic"]
        assert style_kw == ["cinematic", "film grain"]

    @pytest.mark.asyncio
    async def test_extract_tags_handles_markdown_fence(self) -> None:
        """LLM wraps JSON in markdown code fence → still parsed."""
        db = AsyncMock()
        gen = TemplateGenerator(db)

        llm_output = '```json\n{"tags": ["landscape"], "style_keywords": ["epic"]}\n```'

        with (
            patch.object(
                gen, "_get_meta_prompt", new_callable=AsyncMock, return_value="system msg"
            ),
            patch.object(gen, "_call_llm", new_callable=AsyncMock, return_value=llm_output),
        ):
            tags, style_kw = await gen._extract_tags("An epic landscape")

        assert tags == ["landscape"]
        assert style_kw == ["epic"]


# ============================================================================
# Quality Evaluation
# ============================================================================


class TestEvaluateQuality:
    @pytest.mark.asyncio
    async def test_quality_pass(self) -> None:
        """Score >= 7.0 should pass quality check."""
        db = AsyncMock()
        gen = TemplateGenerator(db)

        llm_output = _mock_llm_response({"score": 8.5, "reasoning": "Well structured"})

        with (
            patch.object(
                gen, "_get_meta_prompt", new_callable=AsyncMock, return_value="system msg"
            ),
            patch.object(gen, "_call_llm", new_callable=AsyncMock, return_value=llm_output),
        ):
            score = await gen._evaluate_quality("A detailed prompt text")

        assert score == 8.5

    @pytest.mark.asyncio
    async def test_quality_fail(self) -> None:
        """Score < 7.0 should be below threshold."""
        db = AsyncMock()
        gen = TemplateGenerator(db)

        llm_output = _mock_llm_response({"score": 4.0, "reasoning": "Too vague"})

        with (
            patch.object(
                gen, "_get_meta_prompt", new_callable=AsyncMock, return_value="system msg"
            ),
            patch.object(gen, "_call_llm", new_callable=AsyncMock, return_value=llm_output),
        ):
            score = await gen._evaluate_quality("A vague prompt")

        assert score == 4.0
        assert score < 7.0  # Below threshold


# ============================================================================
# Generate for Category
# ============================================================================


class TestGenerateForCategory:
    @pytest.mark.asyncio
    async def test_generates_and_saves_passing_templates(self) -> None:
        """Templates passing quality check should be saved to DB."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        gen = TemplateGenerator(db)

        batch_data = [
            {
                "prompt_text": "A beautiful portrait with soft lighting",
                "display_name_en": "Soft Light Portrait",
                "display_name_zh": "柔光人像",
                "description_en": "Soft light portrait",
                "description_zh": "柔光人像描述",
                "difficulty": "beginner",
            },
            {
                "prompt_text": "Another portrait in dramatic style",
                "display_name_en": "Dramatic Portrait",
                "display_name_zh": "戏剧人像",
                "description_en": "Dramatic portrait",
                "description_zh": "戏剧人像描述",
                "difficulty": "intermediate",
            },
        ]

        with (
            patch.object(
                gen,
                "_generate_batch",
                new_callable=AsyncMock,
                return_value=batch_data,
            ),
            patch.object(
                gen,
                "_evaluate_quality",
                new_callable=AsyncMock,
                side_effect=[8.0, 5.0],  # first passes (>=7), second fails (<7)
            ),
            patch.object(
                gen,
                "_extract_tags",
                new_callable=AsyncMock,
                return_value=(["portrait", "soft-light"], ["soft", "cinematic"]),
            ),
        ):
            # Pass explicit single style to avoid multi-style batch splitting
            stats = await gen.generate_templates_for_category(
                "portrait", count=2, styles=["cinematic editorial"]
            )

        assert stats.category == "portrait"
        assert stats.generated == 2
        assert stats.passed_quality == 1  # only first passed
        assert stats.saved == 1
        assert db.add.call_count == 1
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_save_when_all_fail_quality(self) -> None:
        """When all templates fail quality, nothing should be committed."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        gen = TemplateGenerator(db)

        batch_data = [
            {
                "prompt_text": "A vague prompt",
                "display_name_en": "Vague",
                "display_name_zh": "模糊",
                "difficulty": "beginner",
            },
        ]

        with (
            patch.object(gen, "_generate_batch", new_callable=AsyncMock, return_value=batch_data),
            patch.object(gen, "_evaluate_quality", new_callable=AsyncMock, return_value=3.0),
        ):
            stats = await gen.generate_templates_for_category("portrait", count=1)

        assert stats.saved == 0
        db.commit.assert_not_awaited()


# ============================================================================
# Enhance Template
# ============================================================================


class TestEnhanceTemplate:
    @pytest.mark.asyncio
    async def test_enhance_updates_prompt(self) -> None:
        """Enhance should update the template's prompt_text in DB."""
        template = _make_template()
        original_prompt = template.prompt_text

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = template
        db.execute = AsyncMock(return_value=result_mock)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        gen = TemplateGenerator(db)

        enhance_data = {
            "enhanced_prompt": "Enhanced version with more detail and style cues",
            "improvements": ["Added lighting details", "Improved composition terms"],
        }

        with (
            patch.object(
                gen, "_get_meta_prompt", new_callable=AsyncMock, return_value="system msg"
            ),
            patch.object(
                gen,
                "_call_llm",
                new_callable=AsyncMock,
                return_value=_mock_llm_response(enhance_data),
            ),
        ):
            result = await gen.enhance_template(str(template.id))

        assert isinstance(result, EnhanceResponse)
        assert result.original_prompt == original_prompt
        assert result.enhanced_prompt == "Enhanced version with more detail and style cues"
        assert len(result.improvements) == 2
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enhance_not_found_raises(self) -> None:
        """Enhance should raise when template doesn't exist."""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        gen = TemplateGenerator(db)

        from app.core.exceptions import BusinessError

        with pytest.raises(BusinessError):
            await gen.enhance_template(str(uuid4()))


# ============================================================================
# Generate Style Variants
# ============================================================================


class TestGenerateVariants:
    @pytest.mark.asyncio
    async def test_creates_variant_per_style(self) -> None:
        """Each target style should produce one new template."""
        template = _make_template()

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = template
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.commit = AsyncMock()

        # Mock refresh to populate fields that would normally be set by the DB
        async def _fake_refresh(obj: Any) -> None:
            if isinstance(obj, PromptTemplate):
                if obj.id is None:
                    obj.id = str(uuid4())
                if obj.created_at is None:
                    obj.created_at = datetime.now(timezone.utc)
                if obj.updated_at is None:
                    obj.updated_at = datetime.now(timezone.utc)
                if obj.use_count is None:
                    obj.use_count = 0
                if obj.like_count is None:
                    obj.like_count = 0
                if obj.favorite_count is None:
                    obj.favorite_count = 0
                if obj.trending_score is None:
                    obj.trending_score = 0.0
                if obj.is_active is None:
                    obj.is_active = True
                if obj.parameters is None:
                    obj.parameters = {}

        db.refresh = AsyncMock(side_effect=_fake_refresh)

        gen = TemplateGenerator(db)

        variant_data = {
            "prompt_text": "Cyberpunk version of the portrait",
            "display_name_en": "Cyberpunk Portrait",
            "display_name_zh": "赛博朋克人像",
        }

        with (
            patch.object(
                gen, "_get_meta_prompt", new_callable=AsyncMock, return_value="system msg"
            ),
            patch.object(
                gen,
                "_call_llm",
                new_callable=AsyncMock,
                return_value=_mock_llm_response(variant_data),
            ),
            patch.object(
                gen,
                "_extract_tags",
                new_callable=AsyncMock,
                return_value=(["cyberpunk", "neon"], ["cyberpunk"]),
            ),
        ):
            result = await gen.generate_style_variants(
                str(template.id),
                target_styles=["cyberpunk"],
            )

        assert isinstance(result, VariantResponse)
        assert result.source_template_id == str(template.id)
        assert len(result.variants_created) == 1
        assert db.add.call_count == 1


# ============================================================================
# Batch Generate Stats
# ============================================================================


class TestBatchGenerate:
    @pytest.mark.asyncio
    async def test_aggregates_stats(self) -> None:
        """Batch generate should aggregate stats across categories."""
        db = AsyncMock()
        gen = TemplateGenerator(db)

        stats_a = GenerateStats(category="portrait", generated=10, passed_quality=8, saved=8)
        stats_b = GenerateStats(category="landscape", generated=10, passed_quality=7, saved=7)

        with patch.object(
            gen,
            "generate_templates_for_category",
            new_callable=AsyncMock,
            side_effect=[stats_a, stats_b],
        ):
            result = await gen.batch_generate(
                categories=["portrait", "landscape"],
                count_per_category=10,
            )

        assert isinstance(result, GenerateResponse)
        assert len(result.stats) == 2
        assert result.total_generated == 20
        assert result.total_saved == 15


# ============================================================================
# Category Styles Coverage
# ============================================================================


class TestCategoryStyles:
    def test_all_categories_have_styles(self) -> None:
        """Every known category should have styles defined."""
        from app.services.template_service import CATEGORIES

        for category in CATEGORIES:
            assert category in CATEGORY_STYLES, f"Missing styles for {category}"
            assert len(CATEGORY_STYLES[category]) >= 4, f"Too few styles for {category}"

    def test_ten_categories(self) -> None:
        """CATEGORY_STYLES should cover exactly 10 categories."""
        assert len(CATEGORY_STYLES) == 10

    def test_eight_styles_each(self) -> None:
        """Each category should have exactly 8 styles."""
        for category, styles in CATEGORY_STYLES.items():
            assert len(styles) == 8, f"{category} has {len(styles)} styles, expected 8"


# ============================================================================
# JSON Parsing
# ============================================================================


class TestParseJson:
    def test_plain_json(self) -> None:
        gen = TemplateGenerator(AsyncMock())
        result = gen._parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_fenced_json(self) -> None:
        gen = TemplateGenerator(AsyncMock())
        raw = '```json\n{"key": "value"}\n```'
        result = gen._parse_json(raw)
        assert result == {"key": "value"}

    def test_markdown_fenced_no_language(self) -> None:
        gen = TemplateGenerator(AsyncMock())
        raw = "```\n[1, 2, 3]\n```"
        result = gen._parse_json(raw)
        assert result == [1, 2, 3]

    def test_invalid_json_raises(self) -> None:
        gen = TemplateGenerator(AsyncMock())
        with pytest.raises(json.JSONDecodeError):
            gen._parse_json("not json at all")
