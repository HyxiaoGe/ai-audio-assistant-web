"""提示词管理器 - 通过 PromptHub API 获取提示词模板"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
from jinja2 import BaseLoader, Environment, TemplateSyntaxError

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode

log = logging.getLogger(__name__)


class PromptManager:
    """提示词管理器

    从 PromptHub API 获取提示词模板，配置数据从本地 config.json 读取。
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

        # PromptHub config (lazy import settings to avoid circular import)
        from app.config import settings

        self._hub_url: Optional[str] = getattr(settings, "PROMPTHUB_BASE_URL", None)
        self._hub_key: Optional[str] = getattr(settings, "PROMPTHUB_API_KEY", None)
        self._hub_ttl: int = getattr(settings, "PROMPTHUB_CACHE_TTL", 300)
        self._hub_enabled: bool = bool(self._hub_url and self._hub_key)

        # PromptHub slug->id index (preloaded from list endpoint)
        self._hub_index: Dict[str, str] = {}  # slug -> prompt_id
        self._hub_index_expiry: float = 0.0

        # PromptHub content cache: slug -> (expiry_timestamp, content_string)
        self._hub_cache: Dict[str, Tuple[float, str]] = {}

        # HTTP client (lazy init)
        self._http: Optional[httpx.Client] = None

        # Jinja2 environment for rendering PromptHub templates
        self._jinja_env = Environment(loader=BaseLoader())

        if self._hub_enabled:
            log.info("PromptHub enabled: %s (TTL=%ds)", self._hub_url, self._hub_ttl)
        else:
            log.warning("PromptHub not configured — prompts will not be available")

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
        if content_style is None:
            content_style = (variables or {}).get("content_style", "meeting")

        if not self._hub_enabled:
            raise BusinessError(
                ErrorCode.SYSTEM_ERROR,
                reason="PromptHub not configured",
            )

        # 1. Fetch user_prompt template from PromptHub
        slug = self._build_prompt_slug(category, prompt_type, locale, content_style)
        template_content = self._fetch_hub_prompt(slug)
        if not template_content:
            raise BusinessError(
                ErrorCode.SYSTEM_ERROR,
                reason=f"Prompt not found in PromptHub: {slug}",
            )

        # 2. Get shared modules and merge with caller variables
        shared_vars = self._resolve_shared_vars(locale)
        all_vars = {**shared_vars, **(variables or {})}

        # 3. Render user_prompt with Jinja2
        user_prompt = self._render_jinja2(template_content, all_vars)

        # 4. Get system message from PromptHub
        system_message = self._get_system_from_hub(category, locale, content_style)
        if system_message is None:
            system_message = ""

        # 5. model_params from local config.json
        config_data = self._load_config(category)
        if "prompt_types" in config_data and prompt_type in config_data["prompt_types"]:
            model_params = config_data["prompt_types"][prompt_type].get("model_params", {})
        else:
            model_params = config_data.get("model_params", {}).get(prompt_type, {})

        return {
            "system": system_message,
            "user_prompt": user_prompt,
            "model_params": model_params,
            "metadata": {
                "category": category,
                "type": prompt_type,
                "locale": locale,
                "content_style": content_style,
                "version": config_data.get("version", "unknown"),
                "source": "prompthub",
            },
        }

    def clear_cache(self) -> None:
        """清除所有缓存"""
        self._config_cache.clear()
        self._hub_index.clear()
        self._hub_index_expiry = 0.0
        self._hub_cache.clear()

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
        config = self._load_config("images")
        lang = "zh" if locale.startswith("zh") else "en"

        # Style config
        style_config = config.get("content_style_mapping", {}).get(
            content_style, config["content_style_mapping"]["general"]
        )

        # Visual style prompt
        visual_style_key = style_config.get("visual_style", "flat_vector")
        visual_styles = config.get("visual_styles", {})
        lang_key = f"prompt_{lang}"
        visual_style_prompt = visual_styles.get(visual_style_key, {}).get(
            lang_key, visual_styles.get("flat_vector", {}).get(lang_key, "")
        )

        # Layout instructions from config.json
        layout_key = style_config.get("layout", "flexible")
        layout_templates = config.get("layout_templates", {}).get(lang, {})
        layout_instructions = layout_templates.get(
            layout_key, layout_templates.get("flexible", "")
        )

        # Colors
        colors = style_config.get("color_scheme", {})

        # Localized names from config.json
        image_type_name = config.get("image_type_names", {}).get(lang, {}).get(
            image_type, image_type
        )
        content_style_name = config.get("content_style_names", {}).get(lang, {}).get(
            content_style, content_style
        )

        # Format key texts
        if key_texts:
            key_texts_formatted = "\n".join([f"- {text}" for text in key_texts])
        else:
            if lang == "zh":
                key_texts_formatted = "- (根据主题自动生成合适的标签)"
            else:
                key_texts_formatted = "- (Auto-generate appropriate labels based on topic)"

        template_vars = {
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

        # Fetch base_prompt from PromptHub
        if self._hub_enabled:
            loc_short = locale.split("-")[0]
            slug = f"images-baseprompt-{loc_short}"
            hub_content = self._fetch_hub_prompt(slug)
            if hub_content:
                return self._render_jinja2(hub_content, template_vars)

        raise BusinessError(
            ErrorCode.SYSTEM_ERROR,
            reason="Failed to fetch image prompt from PromptHub",
        )

    # ====================================================================
    # PromptHub integration
    # ====================================================================

    def _get_http_client(self) -> httpx.Client:
        """Lazy-init HTTP client for PromptHub API."""
        if self._http is None:
            self._http = httpx.Client(
                base_url=self._hub_url,
                headers={
                    "Authorization": f"Bearer {self._hub_key}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
        return self._http

    def _ensure_hub_index(self) -> None:
        """Build slug->id index from PromptHub list endpoint (lightweight, no content)."""
        now = time.monotonic()
        if self._hub_index and self._hub_index_expiry > now:
            return

        try:
            client = self._get_http_client()
            index: Dict[str, str] = {}

            page = 1
            while True:
                resp = client.get(
                    "/api/v1/prompts",
                    params={"page": page, "page_size": 100},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("data", [])
                meta = data.get("meta", {})

                for item in items:
                    s = item.get("slug", "")
                    pid = item.get("id", "")
                    if s and pid:
                        index[s] = pid

                total_pages = meta.get("total_pages", 1)
                if page >= total_pages:
                    break
                page += 1

            self._hub_index = index
            self._hub_index_expiry = now + self._hub_ttl
            log.info("PromptHub index loaded: %d slugs", len(index))

        except Exception as e:
            log.warning("Failed to load PromptHub index: %s", e)
            self._hub_index_expiry = now + 30

    def _fetch_hub_prompt(self, slug: str) -> Optional[str]:
        """Fetch a prompt's content from PromptHub by slug, with TTL cache.

        Uses a two-phase approach:
        1. Preloaded slug->id index (from list endpoint)
        2. Per-slug content cache (from individual endpoint)
        """
        now = time.monotonic()

        # Check content cache first
        cached = self._hub_cache.get(slug)
        if cached and cached[0] > now:
            return cached[1] if cached[1] else None

        # Ensure slug->id index is loaded
        self._ensure_hub_index()

        prompt_id = self._hub_index.get(slug)
        if not prompt_id:
            log.debug("PromptHub: slug '%s' not in index", slug)
            self._hub_cache[slug] = (now + 60, "")  # cache miss briefly
            return None

        # Fetch content from individual endpoint
        try:
            client = self._get_http_client()
            resp = client.get(f"/api/v1/prompts/{prompt_id}")
            resp.raise_for_status()
            data = resp.json()
            item = data.get("data", data)
            content = item.get("content", "")

            self._hub_cache[slug] = (now + self._hub_ttl, content)
            return content if content else None

        except Exception as e:
            log.debug("PromptHub fetch error for '%s' (id=%s): %s", slug, prompt_id, e)
            return None

    def _render_jinja2(self, template_str: str, variables: Dict[str, Any]) -> str:
        """Render a Jinja2 template string with variables."""
        try:
            tpl = self._jinja_env.from_string(template_str)
            return tpl.render(**variables)
        except TemplateSyntaxError as e:
            log.warning("Jinja2 syntax error in template: %s", e)
            return self._simple_render(template_str, variables)

    @staticmethod
    def _simple_render(template_str: str, variables: Dict[str, Any]) -> str:
        """Simple fallback renderer: replace {{ var }} with values."""
        import re

        def _replace(m: re.Match) -> str:
            key = m.group(1).strip()
            return str(variables.get(key, m.group(0)))

        return re.sub(r"\{\{\s*(\w+)\s*\}\}", _replace, template_str)

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
        loc_short = locale.split("-")[0]
        shared: Dict[str, str] = {}

        fmt = self._fetch_hub_prompt(f"shared-format-rules-{loc_short}")
        if fmt:
            shared["format_rules"] = fmt

        img = self._fetch_hub_prompt(f"shared-image-req-{loc_short}")
        if img:
            shared["image_requirements"] = img

        return shared

    def _get_system_from_hub(
        self, category: str, locale: str, content_style: str
    ) -> Optional[str]:
        """Fetch and render system role from PromptHub."""
        loc_short = locale.split("-")[0]
        slug = f"shared-system-role-{loc_short}"
        content = self._fetch_hub_prompt(slug)
        if not content:
            return None

        return self._render_jinja2(content, {"content_style": content_style})

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
