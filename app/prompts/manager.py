"""提示词管理器 - 通过 PromptHub SDK 获取提示词模板"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from prompthub import NotFoundError, PromptHubClient, PromptHubError

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode

log = logging.getLogger(__name__)


class PromptManager:
    """提示词管理器

    通过 PromptHub SDK 获取提示词模板，配置数据从本地 config.json 读取。
    """

    _instance: Optional[PromptManager] = None

    def __new__(cls) -> PromptManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        # Local config path (config.json files only)
        self.prompts_dir = Path(__file__).parent / "templates"

        # Config cache (permanent)
        self._config_cache: Dict[str, Dict] = {}

        # PromptHub SDK client (lazy import settings to avoid circular import)
        from app.config import settings

        hub_url: Optional[str] = getattr(settings, "PROMPTHUB_BASE_URL", None)
        hub_key: Optional[str] = getattr(settings, "PROMPTHUB_API_KEY", None)
        hub_ttl: int = getattr(settings, "PROMPTHUB_CACHE_TTL", 300)

        if not hub_url or not hub_key:
            self._client: Optional[PromptHubClient] = None
            log.warning("PromptHub not configured — prompts will not be available")
            return

        self._client = PromptHubClient(
            base_url=hub_url,
            api_key=hub_key,
            cache_ttl=hub_ttl,
        )
        log.info("PromptHub SDK initialized: %s (TTL=%ds)", hub_url, hub_ttl)

    # ====================================================================
    # Public API
    # ====================================================================

    def get_prompt(
        self,
        category: str,
        prompt_type: str,
        locale: str = "zh-CN",
        variables: Optional[Dict[str, Any]] = None,
        content_style: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取提示词

        Args:
            category: 类别（如 summary, segmentation, visual）
            prompt_type: 提示词类型（如 overview, key_points, action_items, segment）
            locale: 语言（如 zh-CN, en-US）
            variables: 模板变量（如 {transcript}, {quality_notice}）
            content_style: 内容风格（如 meeting, lecture, podcast）

        Returns:
            包含 system, user_prompt, model_params 的字典
        """
        if self._client is None:
            raise BusinessError(
                ErrorCode.SYSTEM_ERROR,
                reason="PromptHub not configured",
            )

        if content_style is None:
            content_style = (variables or {}).get("content_style", "meeting")

        slug = self._build_prompt_slug(category, prompt_type, locale, content_style)

        try:
            # SDK handles: slug lookup, HTTP call, caching, response parsing
            prompt = self._client.prompts.get_by_slug(slug)

            # Merge shared vars + caller vars, then render server-side
            shared_vars = self._resolve_shared_vars(locale)
            all_vars = {**shared_vars, **(variables or {})}

            rendered = self._client.prompts.render(prompt.id, variables=all_vars)
            user_prompt = rendered.rendered_content

        except NotFoundError:
            raise BusinessError(
                ErrorCode.SYSTEM_ERROR,
                reason=f"Prompt not found in PromptHub: {slug}",
            )
        except PromptHubError as e:
            raise BusinessError(
                ErrorCode.SYSTEM_ERROR,
                reason=f"PromptHub error [{e.code}]: {e.message}",
            )

        # System message
        system_message = self._get_system_from_hub(category, locale, content_style)

        # model_params from local config.json
        config_data = self._load_config(category)
        if "prompt_types" in config_data and prompt_type in config_data["prompt_types"]:
            model_params = config_data["prompt_types"][prompt_type].get("model_params", {})
        else:
            model_params = config_data.get("model_params", {}).get(prompt_type, {})

        return {
            "system": system_message or "",
            "user_prompt": user_prompt,
            "model_params": model_params,
            "metadata": {
                "category": category,
                "type": prompt_type,
                "locale": locale,
                "content_style": content_style,
                "version": config_data.get("version", "unknown"),
                "source": "prompthub-sdk",
            },
        }

    def clear_cache(self) -> None:
        """清除所有缓存"""
        self._config_cache.clear()
        # SDK cache is managed internally by TTLCache

    def get_visual_config(self, visual_type: str) -> Dict[str, Any]:
        """获取可视化类型的配置（从本地 config.json 读取）"""
        config = self._load_config("visual")
        prompt_types = config.get("prompt_types", {})
        if visual_type not in prompt_types:
            return {}
        return prompt_types[visual_type]

    def get_image_config(self, content_style: str) -> Dict[str, Any]:
        """获取内容风格对应的图片配置（从本地 config.json 读取）"""
        config = self._load_config("images")
        mapping = config.get("content_style_mapping", {})
        return mapping.get(content_style, mapping.get("general", {}))

    def get_image_prompt(
        self,
        content_style: str,
        image_type: str,
        description: str,
        key_texts: list[str],
        locale: str = "zh-CN",
    ) -> str:
        """获取图片生成提示词"""
        if self._client is None:
            raise BusinessError(
                ErrorCode.SYSTEM_ERROR,
                reason="PromptHub not configured",
            )

        config = self._load_config("images")
        lang = "zh" if locale.startswith("zh") else "en"

        # Style config
        style_config = config.get("content_style_mapping", {}).get(
            content_style, config["content_style_mapping"]["general"]
        )

        template_vars = self._build_image_template_vars(
            config, style_config, lang, image_type, content_style, description, key_texts
        )

        loc_short = locale.split("-")[0]
        slug = f"images-baseprompt-{loc_short}"

        try:
            prompt = self._client.prompts.get_by_slug(slug)
            rendered = self._client.prompts.render(prompt.id, variables=template_vars)
            return rendered.rendered_content
        except PromptHubError as e:
            raise BusinessError(
                ErrorCode.SYSTEM_ERROR,
                reason=f"Failed to fetch image prompt: {e.message}",
            )

    # ====================================================================
    # PromptHub SDK helpers
    # ====================================================================

    def _build_prompt_slug(
        self, category: str, prompt_type: str, locale: str, content_style: str
    ) -> str:
        """Build the PromptHub slug from parameters."""
        loc_short = locale.split("-")[0]
        type_slug = prompt_type.replace("_", "")  # key_points -> keypoints

        # action_items doesn't vary by content_style
        if prompt_type == "action_items":
            return f"{category}-{type_slug}-{loc_short}"

        return f"{category}-{type_slug}-{content_style}-{loc_short}"

    def _resolve_shared_vars(self, locale: str) -> Dict[str, str]:
        """Fetch shared modules (format_rules, image_requirements) from PromptHub."""
        if self._client is None:
            return {}

        loc_short = locale.split("-")[0]
        shared: Dict[str, str] = {}

        try:
            fmt = self._client.prompts.get_by_slug(f"shared-format-rules-{loc_short}")
            shared["format_rules"] = fmt.content
        except NotFoundError:
            pass

        try:
            img = self._client.prompts.get_by_slug(f"shared-image-req-{loc_short}")
            shared["image_requirements"] = img.content
        except NotFoundError:
            pass

        return shared

    def _get_system_from_hub(self, category: str, locale: str, content_style: str) -> Optional[str]:
        """Fetch and render system role from PromptHub."""
        if self._client is None:
            return None

        loc_short = locale.split("-")[0]
        slug = f"shared-system-role-{loc_short}"

        try:
            prompt = self._client.prompts.get_by_slug(slug)
            rendered = self._client.prompts.render(
                prompt.id, variables={"content_style": content_style}
            )
            return rendered.rendered_content
        except PromptHubError:
            return None

    def _build_image_template_vars(
        self,
        config: Dict,
        style_config: Dict,
        lang: str,
        image_type: str,
        content_style: str,
        description: str,
        key_texts: list[str],
    ) -> Dict[str, Any]:
        """Build template variables for image prompt rendering."""
        visual_style_key = style_config.get("visual_style", "flat_vector")
        visual_styles = config.get("visual_styles", {})
        lang_key = f"prompt_{lang}"
        visual_style_prompt = visual_styles.get(visual_style_key, {}).get(
            lang_key, visual_styles.get("flat_vector", {}).get(lang_key, "")
        )

        layout_key = style_config.get("layout", "flexible")
        layout_templates = config.get("layout_templates", {}).get(lang, {})
        layout_instructions = layout_templates.get(layout_key, layout_templates.get("flexible", ""))

        colors = style_config.get("color_scheme", {})

        image_type_name = (
            config.get("image_type_names", {}).get(lang, {}).get(image_type, image_type)
        )
        content_style_name = (
            config.get("content_style_names", {}).get(lang, {}).get(content_style, content_style)
        )

        if key_texts:
            key_texts_formatted = "\n".join([f"- {text}" for text in key_texts])
        else:
            if lang == "zh":
                key_texts_formatted = "- (根据主题自动生成合适的标签)"
            else:
                key_texts_formatted = "- (Auto-generate appropriate labels based on topic)"

        return {
            "image_type": image_type_name,
            "content_style_name": content_style_name,
            "visual_style_prompt": visual_style_prompt,
            "primary_color": colors.get("primary", "#3B82F6"),
            "secondary_color": colors.get("secondary", "#10B981"),
            "background_color": colors.get("background", "#FFFFFF"),
            "description": description,
            "key_texts_formatted": key_texts_formatted,
            "layout_instructions": layout_instructions,
        }

    # ====================================================================
    # Local config.json (model params, visual/image configs)
    # ====================================================================

    def _load_config(self, category: str) -> Dict:
        """加载配置文件（带缓存）"""
        cache_key = f"{category}:config"

        if cache_key in self._config_cache:
            return self._config_cache[cache_key]

        config_file = self.prompts_dir / category / "config.json"

        if not config_file.exists():
            return {
                "version": "1.0.0",
                "model_params": {},
            }

        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._config_cache[cache_key] = data
        return data


# 全局单例
_prompt_manager = PromptManager()


def get_prompt_manager() -> PromptManager:
    """获取提示词管理器单例"""
    return _prompt_manager
