"""AI-powered template generation pipeline.

Uses PromptHub meta prompts + LLM to batch-generate, enhance, and create
style variants of image prompt templates.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any

from prompthub import AsyncPromptHubClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode
from app.models.prompt_template import PromptTemplate
from app.schemas.template import TemplateDetailResponse
from app.schemas.template_generate import (
    EnhanceResponse,
    GenerateResponse,
    GenerateStats,
    VariantResponse,
)
from app.services.template_service import TemplateService

logger = logging.getLogger(__name__)

# LLM model used for generation
_LLM_PROVIDER = "openrouter"
_LLM_MODEL_ID = "anthropic/claude-sonnet-4.5"

# Quality threshold (0-10 scale, PromptHub default scoring_scale=10)
_QUALITY_THRESHOLD = 7.0

# Category → sub-styles mapping (10 categories × 8 styles each)
CATEGORY_STYLES: dict[str, list[str]] = {
    "portrait": [
        "cinematic editorial",
        "classic studio",
        "environmental street",
        "fine art surrealist",
        "fashion editorial",
        "low-key dramatic",
        "high-key beauty",
        "vintage film",
    ],
    "landscape": [
        "golden hour epic",
        "misty mountain",
        "seascape long exposure",
        "desert minimal",
        "aurora night sky",
        "tropical paradise",
        "urban skyline",
        "autumn forest",
    ],
    "illustration": [
        "children's book watercolor",
        "dark fantasy ink",
        "botanical scientific",
        "retro comic pop art",
        "art nouveau decorative",
        "minimal line art",
        "isometric pixel",
        "concept art painterly",
    ],
    "product": [
        "clean studio white",
        "lifestyle contextual",
        "luxury dark mood",
        "floating levitation",
        "flat lay arrangement",
        "macro close-up",
        "outdoor natural",
        "tech minimalist",
    ],
    "architecture": [
        "brutalist concrete",
        "futuristic parametric",
        "japanese zen minimal",
        "gothic cathedral",
        "art deco glamour",
        "industrial warehouse",
        "modern glass steel",
        "ancient ruins",
    ],
    "anime": [
        "shonen action",
        "slice of life pastel",
        "mecha sci-fi",
        "studio ghibli whimsical",
        "dark seinen mature",
        "chibi kawaii",
        "cyberpunk neon",
        "fantasy isekai",
    ],
    "fantasy": [
        "high fantasy epic",
        "dark gothic horror",
        "steampunk victorian",
        "fairy tale whimsical",
        "cosmic eldritch",
        "mythological classical",
        "underwater kingdom",
        "crystal magic",
    ],
    "graphic-design": [
        "swiss international",
        "bauhaus geometric",
        "vaporwave retro",
        "glassmorphism UI",
        "bold typography",
        "gradient abstract",
        "paper cutout layered",
        "neon glow dark",
    ],
    "food": [
        "overhead flat lay",
        "dark moody rustic",
        "bright airy minimal",
        "street food documentary",
        "fine dining plated",
        "baking process",
        "cocktail dramatic",
        "ingredient close-up",
    ],
    "abstract": [
        "fluid paint marbling",
        "geometric fractals",
        "glitch digital",
        "smoke and light",
        "organic biomorphic",
        "color field minimal",
        "texture macro",
        "kinetic motion blur",
    ],
}

# Default aspect ratios per category (must match PromptHub enum: 1:1, 16:9, 9:16, 4:3, 3:4)
_CATEGORY_ASPECT_RATIOS: dict[str, str] = {
    "portrait": "3:4",
    "landscape": "16:9",
    "illustration": "1:1",
    "product": "1:1",
    "architecture": "16:9",
    "anime": "3:4",
    "fantasy": "16:9",
    "graphic-design": "1:1",
    "food": "1:1",
    "abstract": "1:1",
}


class TemplateGenerator:
    """AI template generation pipeline using PromptHub meta prompts + LLM."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._project_id = settings.PROMPTHUB_IMAGE_GEN_PROJECT_ID
        self._client: AsyncPromptHubClient | None = None

    async def _get_client(self) -> AsyncPromptHubClient:
        """Lazily create the AsyncPromptHubClient."""
        if self._client is None:
            if not settings.PROMPTHUB_BASE_URL or not settings.PROMPTHUB_API_KEY:
                raise BusinessError(
                    ErrorCode.SYSTEM_ERROR,
                    reason="PromptHub not configured",
                )
            self._client = AsyncPromptHubClient(
                base_url=settings.PROMPTHUB_BASE_URL,
                api_key=settings.PROMPTHUB_API_KEY,
                cache_ttl=settings.PROMPTHUB_CACHE_TTL,
            )
        return self._client

    async def _get_meta_prompt(self, slug: str, variables: dict[str, Any]) -> str:
        """Fetch a meta prompt from PromptHub by slug and render with variables."""
        client = await self._get_client()
        prompt = await client.prompts.get_by_slug(slug, project_id=self._project_id)
        rendered = await client.prompts.render(prompt.id, variables=variables)
        return rendered.rendered_content

    async def _call_llm(
        self,
        system: str,
        user_prompt: str,
        temperature: float = 0.9,
    ) -> str:
        """Call LLM via SmartFactory and return raw text output."""
        llm = await SmartFactory.get_service("llm", provider=_LLM_PROVIDER, model_id=_LLM_MODEL_ID)
        return await llm.generate(
            prompt=user_prompt,
            system_message=system,
            temperature=temperature,
            max_tokens=2000,
        )

    def _parse_json(self, raw: str) -> Any:
        """Extract and parse JSON from LLM output (handles markdown fences)."""
        text = raw.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)

    # ------------------------------------------------------------------
    # Tag extraction
    # ------------------------------------------------------------------

    async def _extract_tags(self, prompt_text: str) -> tuple[list[str], list[str]]:
        """Extract tags and style keywords from a prompt using PromptHub meta prompt."""
        system = await self._get_meta_prompt(
            "tag-extract-en",
            {"image_prompt": prompt_text},
        )
        user_prompt = (
            "Analyze the image prompt above and return a JSON object with:\n"
            '- "tags": array of 3-6 descriptive tags (lowercase, hyphenated)\n'
            '- "style_keywords": array of 2-4 artistic style keywords\n'
            "Return only valid JSON, no explanation."
        )
        raw = await self._call_llm(system, user_prompt, temperature=0.3)
        data = self._parse_json(raw)
        tags = [str(t) for t in data.get("tags", [])]
        style_keywords = [str(k) for k in data.get("style_keywords", [])]
        return tags, style_keywords

    # ------------------------------------------------------------------
    # Quality evaluation
    # ------------------------------------------------------------------

    async def _evaluate_quality(self, prompt_text: str) -> float:
        """Evaluate prompt quality using PromptHub meta prompt. Returns 0-10 score."""
        system = await self._get_meta_prompt(
            "quality-eval-en",
            {"image_prompt": prompt_text},
        )
        user_prompt = (
            "Evaluate the image prompt above for quality. Return a JSON object:\n"
            '- "score": number 0-10 (10 = excellent)\n'
            '- "reasoning": brief explanation\n'
            "Return only valid JSON."
        )
        raw = await self._call_llm(system, user_prompt, temperature=0.2)
        data = self._parse_json(raw)
        return float(data.get("score", 0))

    # ------------------------------------------------------------------
    # Generate templates for a category
    # ------------------------------------------------------------------

    async def generate_templates_for_category(
        self,
        category: str,
        count: int = 10,
        styles: list[str] | None = None,
    ) -> GenerateStats:
        """Generate templates for a single category with quality filtering."""
        available_styles = styles or CATEGORY_STYLES.get(category, ["general"])
        aspect_ratio = _CATEGORY_ASPECT_RATIOS.get(category, "1:1")

        # Distribute count across styles
        per_style = max(1, math.ceil(count / len(available_styles)))
        styles_to_use = available_styles[: math.ceil(count / per_style)]

        generated = 0
        passed = 0
        saved = 0

        for style in styles_to_use:
            remaining = count - saved
            if remaining <= 0:
                break
            batch_size = min(per_style, remaining)

            try:
                templates = await self._generate_batch(
                    category=category,
                    style=style,
                    aspect_ratio=aspect_ratio,
                    batch_size=batch_size,
                )
                generated += len(templates)

                for tpl_data in templates:
                    prompt_text = tpl_data.get("prompt_text", "")
                    if not prompt_text:
                        continue

                    # Quality check
                    try:
                        score = await self._evaluate_quality(prompt_text)
                    except Exception:
                        logger.warning("Quality eval failed, skipping template")
                        continue

                    if score < _QUALITY_THRESHOLD:
                        logger.info(
                            "Template rejected: score=%.1f < %.1f",
                            score,
                            _QUALITY_THRESHOLD,
                        )
                        continue
                    passed += 1

                    # Extract tags
                    try:
                        tags, style_keywords = await self._extract_tags(prompt_text)
                    except Exception:
                        logger.warning("Tag extraction failed, using defaults")
                        tags = [category, style.split()[0].lower()]
                        style_keywords = [style]

                    # Create DB record
                    now = datetime.now(timezone.utc)
                    new_template = PromptTemplate(
                        prompt_text=prompt_text,
                        display_name_en=tpl_data.get(
                            "display_name_en", f"{style} {category}"
                        ).strip(),
                        display_name_zh=tpl_data.get(
                            "display_name_zh", f"{style} {category}"
                        ).strip(),
                        description_en=tpl_data.get("description_en"),
                        description_zh=tpl_data.get("description_zh"),
                        category=category,
                        tags=tags,
                        style_keywords=style_keywords,
                        difficulty=tpl_data.get("difficulty", "intermediate"),
                        language="bilingual",
                        source="ai_generated",
                        trending_score=TemplateService.compute_trending_score(0, 0, 0, now),
                    )
                    self._db.add(new_template)
                    saved += 1

            except Exception:
                logger.exception("Failed to generate batch for %s/%s", category, style)
                continue

        if saved > 0:
            await self._db.commit()

        return GenerateStats(
            category=category,
            generated=generated,
            passed_quality=passed,
            saved=saved,
        )

    async def _generate_batch(
        self,
        category: str,
        style: str,
        aspect_ratio: str,
        batch_size: int,
    ) -> list[dict[str, Any]]:
        """Generate a batch of raw template data via LLM."""
        description = f"{category} image, {style} style"
        system = await self._get_meta_prompt(
            "desc-generate-en",
            {
                "user_description": description,
                "target_model": "stable-diffusion",
                "aspect_ratio": aspect_ratio,
                "detail_level": "detailed",
            },
        )
        user_prompt = (
            f"Generate {batch_size} unique image prompt templates for "
            f'"{category}" category in "{style}" style.\n\n'
            "For each template, return a JSON array of objects with:\n"
            '- "prompt_text": the full image generation prompt (detailed, 40-80 words)\n'
            '- "display_name_en": short English display name (3-6 words)\n'
            '- "display_name_zh": short Chinese display name (3-8 characters)\n'
            '- "description_en": one-sentence English description\n'
            '- "description_zh": one-sentence Chinese description\n'
            '- "difficulty": "beginner", "intermediate", or "advanced"\n\n'
            "Return only a valid JSON array, no explanation."
        )
        raw = await self._call_llm(system, user_prompt)
        result = self._parse_json(raw)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "templates" in result:
            return result["templates"]
        return []

    # ------------------------------------------------------------------
    # Enhance an existing template
    # ------------------------------------------------------------------

    async def enhance_template(self, template_id: str) -> EnhanceResponse:
        """Enhance an existing template's prompt text using LLM."""
        result = await self._db.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id,
                PromptTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND)

        original = template.prompt_text
        system = await self._get_meta_prompt(
            "desc-enhance-en",
            {"original_prompt": original},
        )
        user_prompt = (
            "Enhance the image prompt above to be more detailed and effective. "
            "Return a JSON object with:\n"
            '- "enhanced_prompt": the improved prompt text\n'
            '- "improvements": array of brief descriptions of what was improved\n'
            "Return only valid JSON."
        )
        raw = await self._call_llm(system, user_prompt, temperature=0.7)
        data = self._parse_json(raw)

        enhanced = data.get("enhanced_prompt", original)
        improvements = [str(i) for i in data.get("improvements", [])]

        # Update in DB
        template.prompt_text = enhanced
        await self._db.commit()
        await self._db.refresh(template)

        return EnhanceResponse(
            template_id=str(template.id),
            original_prompt=original,
            enhanced_prompt=enhanced,
            improvements=improvements,
        )

    # ------------------------------------------------------------------
    # Generate style variants
    # ------------------------------------------------------------------

    async def generate_style_variants(
        self,
        template_id: str,
        target_styles: list[str],
    ) -> VariantResponse:
        """Generate style variants of an existing template."""
        result = await self._db.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == template_id,
                PromptTemplate.deleted_at.is_(None),
            )
        )
        template = result.scalar_one_or_none()
        if not template:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND)

        new_variants: list[PromptTemplate] = []

        for style in target_styles:
            system = await self._get_meta_prompt(
                "style-transfer-en",
                {
                    "original_prompt": template.prompt_text,
                    "target_style": style,
                },
            )
            user_prompt = (
                f'Transfer the image prompt above into "{style}" style. '
                "Return a JSON object with:\n"
                '- "prompt_text": the style-transferred prompt\n'
                '- "display_name_en": short English name for this variant\n'
                '- "display_name_zh": short Chinese name for this variant\n'
                "Return only valid JSON."
            )
            raw = await self._call_llm(system, user_prompt, temperature=0.8)
            data = self._parse_json(raw)

            prompt_text = data.get("prompt_text", "")
            if not prompt_text:
                continue

            # Extract tags
            try:
                tags, style_keywords = await self._extract_tags(prompt_text)
            except Exception:
                tags = list(template.tags) if template.tags else [template.category]
                style_keywords = [style]

            variant = PromptTemplate(
                prompt_text=prompt_text,
                display_name_en=data.get("display_name_en", f"{style} variant"),
                display_name_zh=data.get("display_name_zh", f"{style}变体"),
                description_en=f"Style variant ({style}) of {template.display_name_en}",
                description_zh=f"{template.display_name_zh}的{style}风格变体",
                category=template.category,
                tags=tags,
                style_keywords=style_keywords,
                difficulty=template.difficulty,
                language="bilingual",
                source="ai_generated",
            )
            self._db.add(variant)
            new_variants.append(variant)

        await self._db.commit()

        # Refresh all variants to get generated IDs
        variants: list[TemplateDetailResponse] = []
        for v in new_variants:
            await self._db.refresh(v)
            variants.append(TemplateDetailResponse.model_validate(v))

        return VariantResponse(
            source_template_id=str(template.id),
            variants_created=variants,
        )

    # ------------------------------------------------------------------
    # Batch generate
    # ------------------------------------------------------------------

    async def batch_generate(
        self,
        categories: list[str] | None = None,
        count_per_category: int = 10,
    ) -> GenerateResponse:
        """Batch-generate templates across multiple categories."""
        target_categories = categories or list(CATEGORY_STYLES.keys())
        all_stats: list[GenerateStats] = []
        total_generated = 0
        total_saved = 0

        for category in target_categories:
            logger.info("Generating templates for category: %s", category)
            stats = await self.generate_templates_for_category(
                category=category,
                count=count_per_category,
            )
            all_stats.append(stats)
            total_generated += stats.generated
            total_saved += stats.saved
            logger.info(
                "Category %s: generated=%d, passed=%d, saved=%d",
                category,
                stats.generated,
                stats.passed_quality,
                stats.saved,
            )

        return GenerateResponse(
            stats=all_stats,
            total_generated=total_generated,
            total_saved=total_saved,
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the PromptHub client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
