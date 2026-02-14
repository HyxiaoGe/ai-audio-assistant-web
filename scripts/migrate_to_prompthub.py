#!/usr/bin/env python3
"""
Migration script: upload local prompt templates to PromptHub service.

Usage:
    python scripts/migrate_to_prompthub.py                          # Run migration
    python scripts/migrate_to_prompthub.py --dry-run                # Preview only
    python scripts/migrate_to_prompthub.py --base-url http://x:8000 # Custom URL
    python scripts/migrate_to_prompthub.py --api-key ph-xxx         # Custom key
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT_DIR / "app" / "prompts" / "templates"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate")

# ---------------------------------------------------------------------------
# Project definitions
# ---------------------------------------------------------------------------
PROJECTS = [
    {
        "slug": "audio-shared",
        "name": "音频共享模块",
        "description": "跨项目共享的通用提示词模块",
    },
    {
        "slug": "audio-summary",
        "name": "音频摘要",
        "description": "摘要生成提示词（overview / key_points / action_items）",
    },
    {
        "slug": "audio-segmentation",
        "name": "章节划分",
        "description": "章节划分提示词（segment）",
    },
    {
        "slug": "audio-visual",
        "name": "可视化生成",
        "description": "可视化内容生成提示词（mindmap / timeline / flowchart / outline）",
    },
    {
        "slug": "audio-images",
        "name": "AI 配图生成",
        "description": "AI 图像生成提示词（base_prompt）",
    },
]

# Content styles used in system role templates
ALL_CONTENT_STYLES = [
    "meeting",
    "lecture",
    "podcast",
    "interview",
    "tutorial",
    "review",
    "news",
    "explainer",
    "documentary",
    "video",
    "general",
]


# ===================================================================
# Helpers
# ===================================================================
def load_json(path: Path) -> Dict:
    """Load a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def convert_format_vars(text: str) -> str:
    """Convert Python str.format variables to Jinja2 syntax.

    {transcript}      -> {{ transcript }}
    {quality_notice}   -> {{ quality_notice }}

    But NOT double-braces like {{IMAGE:...}} — those are handled separately.
    """
    # First, handle the IMAGE placeholders: {{IMAGE:...}} -> {IMAGE:...}
    # These are Python str.format escaped braces meaning literal {IMAGE:...}
    text = re.sub(r"\{\{IMAGE:", "{IMAGE:", text)
    # Fix the closing }} for IMAGE tags (they end with }})
    # Pattern: {IMAGE: ... }}  -> {IMAGE: ... }
    text = re.sub(r"(\{IMAGE:[^}]+)\}\}", r"\1}", text)

    # Now convert remaining single-brace variables to Jinja2
    # {transcript} -> {{ transcript }}
    # {quality_notice} -> {{ quality_notice }}
    # But skip JSON example blocks with {{ }} (already converted IMAGE ones above)
    # We match {word_chars} that are actual template variables
    text = re.sub(
        r"\{(\w+)\}",
        r"{{ \1 }}",
        text,
    )

    return text


def wrap_json_examples_raw(text: str) -> str:
    """Wrap JSON output examples in {% raw %}...{% endraw %} blocks.

    Segmentation templates contain JSON examples with {{ }} that are
    Python str.format escaped braces for literal output.  In Jinja2
    these would be interpreted as expressions, so we wrap them.
    """
    # The segmentation templates have ```json ... ``` blocks with {{ and }}
    # We need to find these blocks and wrap them

    # Strategy: find ```json ... ``` blocks that contain {{ or }}
    def _wrap_block(m: re.Match) -> str:
        block = m.group(0)
        # Convert {{ to { and }} to } inside the block (they were Python escapes)
        inner = block
        inner = inner.replace("{{", "{")
        inner = inner.replace("}}", "}")
        return "{% raw %}\n" + inner + "\n{% endraw %}"

    # Match ```json ... ``` blocks
    result = re.sub(
        r"```json\n.*?```",
        _wrap_block,
        text,
        flags=re.DOTALL,
    )
    return result


def locale_short(locale: str) -> str:
    """zh-CN -> zh, en-US -> en."""
    return locale.split("-")[0]


# ===================================================================
# Shared prompt builders
# ===================================================================

def build_system_role_template(system_data: Dict, locale: str) -> str:
    """Build a Jinja2 conditional system role template from system config.

    Combines all content_style roles into {% if/elif/else %} blocks.
    """
    lines: list[str] = []
    first = True

    for style in ALL_CONTENT_STYLES:
        style_cfg = system_data.get(style)
        if not isinstance(style_cfg, dict):
            continue

        keyword = "if" if first else "elif"
        if style == "general":
            # general is the fallback
            lines.append("{% else %}")
        else:
            lines.append(f'{{% {keyword} content_style == "{style}" %}}')

        role = style_cfg.get("role", "")
        lines.append(role)

        style_desc = style_cfg.get("style")
        if style_desc:
            if locale.startswith("zh"):
                lines.append(f"\n风格要求：{style_desc}")
            else:
                lines.append(f"\nStyle: {style_desc}")

        tolerance = style_cfg.get("tolerance")
        if tolerance:
            if locale.startswith("zh"):
                lines.append(f"\n容错说明：{tolerance}")
            else:
                lines.append(f"\nError tolerance: {tolerance}")

        first = False

    lines.append("{% endif %}")

    # Add constraints
    constraints = system_data.get("constraints", [])
    if constraints:
        if locale.startswith("zh"):
            lines.append("\n\n约束条件：")
        else:
            lines.append("\n\nConstraints:")
        for c in constraints:
            lines.append(f"- {c}")

    return "\n".join(lines)


def extract_image_requirements_zh(templates: Dict) -> str:
    """Extract the common Chinese image requirements section from overview templates."""
    # Use the meeting template as reference (most complete version)
    # The section starts with "## 配图要求（必须）" and goes to end
    for style in ["meeting", "lecture", "podcast"]:
        tpl = templates.get(style, "")
        match = re.search(r"(## 配图要求（必须）.*)", tpl, re.DOTALL)
        if match:
            section = match.group(1)
            # Convert {{IMAGE: to {IMAGE: (these are Python format escapes)
            section = re.sub(r"\{\{IMAGE:", "{IMAGE:", section)
            section = re.sub(r"(\{IMAGE:[^}]+)\}\}", r"\1}", section)
            return section
    return ""


def extract_image_requirements_en(templates: Dict) -> str:
    """Extract the common English image requirements section from overview templates."""
    for style in ["lecture", "podcast", "video"]:
        tpl = templates.get(style, "")
        match = re.search(r"(## Image Requirements.*)", tpl, re.DOTALL)
        if match:
            section = match.group(1)
            section = re.sub(r"\{\{IMAGE:", "{IMAGE:", section)
            section = re.sub(r"(\{IMAGE:[^}]+)\}\}", r"\1}", section)
            return section
    return ""


def extract_format_rules_zh() -> str:
    """Build a generic Chinese format rules shared template."""
    return (
        "格式要求：\n"
        "1. 严格按照上述Markdown格式输出\n"
        "2. 保持简洁专业，每个部分控制在合理长度\n"
        "3. 如果某个部分没有相关内容，可以省略该部分\n"
        "4. 使用合适的emoji和符号增强可读性"
    )


def extract_format_rules_en() -> str:
    """Build a generic English format rules shared template."""
    return (
        "Format requirements:\n"
        "1. Follow the Markdown format strictly\n"
        "2. Keep it concise and professional\n"
        "3. Skip sections with no relevant content\n"
        "4. Use appropriate formatting for readability"
    )


# ===================================================================
# Template processors for each category
# ===================================================================

def remove_image_req_section_zh(text: str) -> str:
    """Remove the image requirements section from Chinese template and replace with variable."""
    pattern = r"\n*## 配图要求（必须）.*"
    if re.search(pattern, text, re.DOTALL):
        text = re.sub(pattern, "\n\n{{ image_requirements }}", text, flags=re.DOTALL)
    return text


def remove_image_req_section_en(text: str) -> str:
    """Remove the image requirements section from English template and replace with variable."""
    pattern = r"\n*## Image Requirements.*"
    if re.search(pattern, text, re.DOTALL):
        text = re.sub(pattern, "\n\n{{ image_requirements }}", text, flags=re.DOTALL)
    return text


def remove_format_rules_zh(text: str) -> str:
    """Replace Chinese format rules section with variable reference."""
    # Match "格式要求：\n1. ...\n2. ...\n..." until next section or image req or end
    pattern = r"\n*格式要求：\n(?:\d+\.\s+[^\n]+\n?)+"
    if re.search(pattern, text):
        text = re.sub(pattern, "\n\n{{ format_rules }}\n", text)
    return text


def remove_format_rules_en(text: str) -> str:
    """Replace English format rules section with variable reference."""
    pattern = r"\n*Format requirements:\n(?:\d+\.\s+[^\n]+\n?)+"
    if re.search(pattern, text):
        text = re.sub(pattern, "\n\n{{ format_rules }}\n", text)
    return text


def process_summary_template(text: str, locale: str) -> str:
    """Process a summary template: convert vars, extract shared sections."""
    is_zh = locale.startswith("zh")

    # Replace image requirements section with variable
    if is_zh:
        text = remove_image_req_section_zh(text)
    else:
        text = remove_image_req_section_en(text)

    # Replace format rules section with variable
    if is_zh:
        text = remove_format_rules_zh(text)
    else:
        text = remove_format_rules_en(text)

    # Convert variables
    text = convert_format_vars(text)

    return text.strip()


def process_segmentation_template(text: str) -> str:
    """Process a segmentation template: convert vars, wrap JSON examples."""
    # First wrap JSON example blocks in {% raw %}
    text = wrap_json_examples_raw(text)

    # Convert remaining variables (transcript, quality_notice)
    # But we need to be careful not to touch content inside {% raw %} blocks
    parts = re.split(r"(\{% raw %\}.*?\{% endraw %\})", text, flags=re.DOTALL)
    processed = []
    for part in parts:
        if part.startswith("{% raw %}"):
            processed.append(part)
        else:
            processed.append(convert_format_vars(part))
    text = "".join(processed)

    return text.strip()


def process_visual_template(text: str) -> str:
    """Process a visual template: convert variables."""
    text = convert_format_vars(text)
    return text.strip()


def process_images_template(text: str) -> str:
    """Process an images base_prompt template: convert all variables."""
    text = convert_format_vars(text)
    return text.strip()


# ===================================================================
# API client
# ===================================================================

class PromptHubClient:
    """Simple HTTP client for PromptHub API."""

    def __init__(self, base_url: str, api_key: str, dry_run: bool = False):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dry_run = dry_run
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        # Cache: slug -> id
        self._project_ids: Dict[str, str] = {}
        self._prompt_slugs: Dict[str, set] = {}  # project_slug -> set of prompt slugs

    def check_connectivity(self) -> bool:
        """Verify PromptHub service is reachable."""
        try:
            resp = self.client.get("/openapi.json")
            resp.raise_for_status()
            log.info("PromptHub service reachable at %s", self.base_url)
            return True
        except Exception as e:
            log.error("Cannot reach PromptHub at %s: %s", self.base_url, e)
            return False

    def _get_existing_projects(self) -> None:
        """Load existing projects to enable idempotency."""
        if self.dry_run:
            return
        try:
            resp = self.client.get("/api/v1/projects")
            resp.raise_for_status()
            data = resp.json()
            projects = data if isinstance(data, list) else data.get("data", data.get("items", []))
            for p in projects:
                slug = p.get("slug", "")
                pid = p.get("id", "")
                if slug and pid:
                    self._project_ids[slug] = pid
            log.info("Found %d existing projects", len(self._project_ids))
        except Exception as e:
            log.warning("Could not fetch existing projects: %s", e)

    def _get_existing_prompts(self, project_slug: str, project_id: str) -> None:
        """Load existing prompts for a project to enable idempotency."""
        if self.dry_run:
            return
        try:
            resp = self.client.get(
                "/api/v1/prompts", params={"project_id": project_id, "page_size": 100}
            )
            resp.raise_for_status()
            data = resp.json()
            prompts = data if isinstance(data, list) else data.get("data", data.get("items", []))
            slugs = set()
            for p in prompts:
                s = p.get("slug", "")
                if s:
                    slugs.add(s)
            self._prompt_slugs[project_slug] = slugs
            log.info("  Project '%s' has %d existing prompts", project_slug, len(slugs))
        except Exception as e:
            log.warning("Could not fetch existing prompts for %s: %s", project_slug, e)
            self._prompt_slugs[project_slug] = set()

    def create_project(self, slug: str, name: str, description: str) -> Optional[str]:
        """Create a project. Returns project ID or None."""
        if slug in self._project_ids:
            log.info("  Project '%s' already exists (id=%s), skipping", slug, self._project_ids[slug])
            return self._project_ids[slug]

        payload = {"slug": slug, "name": name, "description": description}

        if self.dry_run:
            log.info("  [DRY-RUN] Would create project: %s (%s)", slug, name)
            self._project_ids[slug] = f"dry-run-{slug}"
            return self._project_ids[slug]

        try:
            resp = self.client.post("/api/v1/projects", json=payload)
            resp.raise_for_status()
            data = resp.json()
            # Handle both {id: ...} and {data: {id: ...}} response formats
            pid = data.get("id") or data.get("data", {}).get("id", "")
            self._project_ids[slug] = pid
            log.info("  Created project '%s' (id=%s)", slug, pid)
            return pid
        except httpx.HTTPStatusError as e:
            log.error("  Failed to create project '%s': %s - %s", slug, e.response.status_code, e.response.text)
            return None
        except Exception as e:
            log.error("  Failed to create project '%s': %s", slug, e)
            return None

    def create_prompt(
        self,
        project_slug: str,
        slug: str,
        name: str,
        content: str,
        description: str = "",
        variables: Optional[List[Dict[str, Any]]] = None,
        tags: Optional[List[str]] = None,
        is_shared: bool = False,
    ) -> Optional[str]:
        """Create a prompt. Returns prompt ID or None."""
        existing = self._prompt_slugs.get(project_slug, set())
        if slug in existing:
            log.info("    Prompt '%s' already exists, skipping", slug)
            return "existing"

        project_id = self._project_ids.get(project_slug)
        if not project_id:
            log.error("    No project_id for '%s', cannot create prompt '%s'", project_slug, slug)
            return None

        payload: Dict[str, Any] = {
            "project_id": project_id,
            "slug": slug,
            "name": name,
            "content": content,
            "is_shared": is_shared,
        }
        if description:
            payload["description"] = description
        if variables:
            payload["variables"] = variables
        if tags:
            payload["tags"] = tags

        if self.dry_run:
            content_preview = content[:80].replace("\n", "\\n")
            log.info(
                "    [DRY-RUN] Would create prompt: %s (shared=%s, vars=%d, tags=%s) content=%s...",
                slug,
                is_shared,
                len(variables or []),
                tags,
                content_preview,
            )
            return "dry-run"

        try:
            resp = self.client.post("/api/v1/prompts", json=payload)
            resp.raise_for_status()
            data = resp.json()
            pid = data.get("id") or data.get("data", {}).get("id", "")
            log.info("    Created prompt '%s' (id=%s)", slug, pid)
            existing.add(slug)
            return pid
        except httpx.HTTPStatusError as e:
            log.error("    Failed to create prompt '%s': %s - %s", slug, e.response.status_code, e.response.text)
            return None
        except Exception as e:
            log.error("    Failed to create prompt '%s': %s", slug, e)
            return None

    def verify_project(self, project_slug: str) -> int:
        """Verify prompt count for a project. Returns count."""
        if self.dry_run:
            return 0
        project_id = self._project_ids.get(project_slug)
        if not project_id:
            return 0
        try:
            resp = self.client.get(
                "/api/v1/prompts",
                params={"project_id": project_id, "page_size": 100},
            )
            resp.raise_for_status()
            data = resp.json()
            # Use meta.total if available, otherwise count returned items
            meta = data.get("meta", {})
            if "total" in meta:
                return meta["total"]
            prompts = data if isinstance(data, list) else data.get("data", data.get("items", []))
            return len(prompts)
        except Exception:
            return 0


# ===================================================================
# Migration steps
# ===================================================================

def step_create_projects(client: PromptHubClient) -> bool:
    """Step 3: Create all 5 projects."""
    log.info("=" * 60)
    log.info("Step 3: Creating projects")
    log.info("=" * 60)

    success = True
    for proj in PROJECTS:
        pid = client.create_project(proj["slug"], proj["name"], proj["description"])
        if not pid:
            success = False
    return success


def step_create_shared(client: PromptHubClient) -> int:
    """Step 4: Create audio-shared prompts (6 shared prompts)."""
    log.info("=" * 60)
    log.info("Step 4: Creating audio-shared prompts")
    log.info("=" * 60)

    created = 0
    project_slug = "audio-shared"

    # Load source data
    summary_zh = load_json(TEMPLATES_DIR / "summary" / "zh-CN.json")
    summary_en = load_json(TEMPLATES_DIR / "summary" / "en-US.json")

    # --- shared-system-role-zh ---
    content = build_system_role_template(summary_zh["system"], "zh-CN")
    variables = [
        {
            "name": "content_style",
            "type": "string",
            "required": False,
            "default": "general",
            "description": "内容风格",
        }
    ]
    if client.create_prompt(
        project_slug,
        "shared-system-role-zh",
        "中文系统角色",
        content,
        description="中文系统角色模板，根据 content_style 条件分支选择对应角色",
        variables=variables,
        tags=["shared", "system-role", "zh-CN"],
        is_shared=True,
    ):
        created += 1

    # --- shared-system-role-en ---
    content = build_system_role_template(summary_en["system"], "en-US")
    if client.create_prompt(
        project_slug,
        "shared-system-role-en",
        "English System Role",
        content,
        description="English system role template with content_style conditional branches",
        variables=variables,
        tags=["shared", "system-role", "en-US"],
        is_shared=True,
    ):
        created += 1

    # --- shared-image-req-zh ---
    overview_templates_zh = summary_zh["prompts"]["overview"]["templates"]
    content = extract_image_requirements_zh(overview_templates_zh)
    if content and client.create_prompt(
        project_slug,
        "shared-image-req-zh",
        "中文配图要求",
        content,
        description="中文摘要配图要求段落，在需要配图的模板中通过 image_requirements 变量引用",
        tags=["shared", "image-requirements", "zh-CN"],
        is_shared=True,
    ):
        created += 1

    # --- shared-image-req-en ---
    overview_templates_en = summary_en["prompts"]["overview"]["templates"]
    content = extract_image_requirements_en(overview_templates_en)
    if content and client.create_prompt(
        project_slug,
        "shared-image-req-en",
        "English Image Requirements",
        content,
        description="English image requirements section for summary templates",
        tags=["shared", "image-requirements", "en-US"],
        is_shared=True,
    ):
        created += 1

    # --- shared-format-rules-zh ---
    content = extract_format_rules_zh()
    if client.create_prompt(
        project_slug,
        "shared-format-rules-zh",
        "中文格式要求",
        content,
        description="中文通用格式要求，在各模板中通过 format_rules 变量引用",
        tags=["shared", "format-rules", "zh-CN"],
        is_shared=True,
    ):
        created += 1

    # --- shared-format-rules-en ---
    content = extract_format_rules_en()
    if client.create_prompt(
        project_slug,
        "shared-format-rules-en",
        "English Format Rules",
        content,
        description="English common format rules, referenced via format_rules variable",
        tags=["shared", "format-rules", "en-US"],
        is_shared=True,
    ):
        created += 1

    log.info("  Shared prompts created: %d/6", created)
    return created


def step_create_summary(client: PromptHubClient) -> int:
    """Step 5: Create audio-summary prompts."""
    log.info("=" * 60)
    log.info("Step 5: Creating audio-summary prompts")
    log.info("=" * 60)

    created = 0
    project_slug = "audio-summary"

    # Load config for model_params tags
    config = load_json(TEMPLATES_DIR / "summary" / "config.json")
    model_params = config.get("model_params", {})

    locales = {
        "zh-CN": load_json(TEMPLATES_DIR / "summary" / "zh-CN.json"),
        "en-US": load_json(TEMPLATES_DIR / "summary" / "en-US.json"),
    }

    # Common variables for summary templates
    base_variables = [
        {"name": "transcript", "type": "string", "required": True, "description": "转写文本"},
        {"name": "quality_notice", "type": "string", "required": False, "default": "", "description": "音频质量提示"},
        {"name": "format_rules", "type": "string", "required": False, "default": "", "description": "格式要求"},
    ]
    overview_variables = base_variables + [
        {"name": "image_requirements", "type": "string", "required": False, "default": "", "description": "配图要求"},
    ]

    for locale, data in locales.items():
        loc_short = locale_short(locale)
        prompts_data = data["prompts"]

        # --- overview ---
        overview_cfg = prompts_data.get("overview", {})
        templates = overview_cfg.get("templates", {})
        mp = model_params.get("overview", {})
        temp_tag = f"temp-{mp.get('temperature', 0.6)}"
        tokens_tag = f"tokens-{mp.get('max_tokens', 1500)}"

        for style, tpl in templates.items():
            slug = f"summary-overview-{style}-{loc_short}"
            content = process_summary_template(tpl, locale)
            tags = ["summary", "overview", style, locale, temp_tag, tokens_tag]
            name = f"Summary Overview ({style}, {loc_short})"

            if client.create_prompt(
                project_slug, slug, name, content,
                description=overview_cfg.get("description", ""),
                variables=overview_variables,
                tags=tags,
            ):
                created += 1

        # --- key_points ---
        kp_cfg = prompts_data.get("key_points", {})
        templates = kp_cfg.get("templates", {})
        mp = model_params.get("key_points", {})
        temp_tag = f"temp-{mp.get('temperature', 0.5)}"
        tokens_tag = f"tokens-{mp.get('max_tokens', 1200)}"

        for style, tpl in templates.items():
            slug = f"summary-keypoints-{style}-{loc_short}"
            content = convert_format_vars(tpl)
            # key_points don't have image requirements, but may have format rules
            if locale.startswith("zh"):
                content = remove_format_rules_zh(content)
            else:
                content = remove_format_rules_en(content)
            content = content.strip()
            tags = ["summary", "key_points", style, locale, temp_tag, tokens_tag]
            name = f"Summary Key Points ({style}, {loc_short})"

            if client.create_prompt(
                project_slug, slug, name, content,
                description=kp_cfg.get("description", ""),
                variables=base_variables,
                tags=tags,
            ):
                created += 1

        # --- action_items ---
        ai_cfg = prompts_data.get("action_items", {})
        tpl = ai_cfg.get("template", "")
        if tpl:
            slug = f"summary-actionitems-{loc_short}"
            content = convert_format_vars(tpl)
            if locale.startswith("zh"):
                content = remove_format_rules_zh(content)
            else:
                content = remove_format_rules_en(content)
            content = content.strip()
            mp = model_params.get("action_items", {})
            temp_tag = f"temp-{mp.get('temperature', 0.3)}"
            tokens_tag = f"tokens-{mp.get('max_tokens', 1000)}"
            tags = ["summary", "action_items", locale, temp_tag, tokens_tag]
            name = f"Summary Action Items ({loc_short})"

            if client.create_prompt(
                project_slug, slug, name, content,
                description=ai_cfg.get("description", ""),
                variables=base_variables,
                tags=tags,
            ):
                created += 1

    log.info("  Summary prompts created: %d", created)
    return created


def step_create_segmentation(client: PromptHubClient) -> int:
    """Step 6: Create audio-segmentation prompts."""
    log.info("=" * 60)
    log.info("Step 6: Creating audio-segmentation prompts")
    log.info("=" * 60)

    created = 0
    project_slug = "audio-segmentation"

    data = load_json(TEMPLATES_DIR / "segmentation" / "zh-CN.json")
    config = load_json(TEMPLATES_DIR / "segmentation" / "config.json")

    mp = config.get("model_params", {}).get("segment", {})
    temp_tag = f"temp-{mp.get('temperature', 0.3)}"
    tokens_tag = f"tokens-{mp.get('max_tokens', 1500)}"

    variables = [
        {"name": "transcript", "type": "string", "required": True, "description": "转写文本"},
        {"name": "quality_notice", "type": "string", "required": False, "default": "", "description": "音频质量提示"},
    ]

    segment_cfg = data["prompts"].get("segment", {})
    templates = segment_cfg.get("templates", {})

    for style, tpl in templates.items():
        slug = f"segmentation-segment-{style}-zh"
        content = process_segmentation_template(tpl)
        tags = ["segmentation", "segment", style, "zh-CN", temp_tag, tokens_tag]
        name = f"Segmentation ({style}, zh)"

        if client.create_prompt(
            project_slug, slug, name, content,
            description=segment_cfg.get("description", ""),
            variables=variables,
            tags=tags,
        ):
            created += 1

    log.info("  Segmentation prompts created: %d", created)
    return created


def step_create_visual(client: PromptHubClient) -> int:
    """Step 7: Create audio-visual prompts."""
    log.info("=" * 60)
    log.info("Step 7: Creating audio-visual prompts")
    log.info("=" * 60)

    created = 0
    project_slug = "audio-visual"

    data = load_json(TEMPLATES_DIR / "visual" / "zh-CN.json")
    config = load_json(TEMPLATES_DIR / "visual" / "config.json")

    variables = [
        {"name": "transcript", "type": "string", "required": True, "description": "转写文本"},
        {"name": "quality_notice", "type": "string", "required": False, "default": "", "description": "音频质量提示"},
    ]

    prompt_types = config.get("prompt_types", {})
    prompts_data = data.get("prompts", {})

    for visual_type, type_cfg in prompts_data.items():
        templates = type_cfg.get("templates", {})
        # Get model params from config
        mp = prompt_types.get(visual_type, {}).get("model_params", {})
        temp_tag = f"temp-{mp.get('temperature', 0.3)}"
        tokens_tag = f"tokens-{mp.get('max_tokens', 1500)}"

        for style, tpl in templates.items():
            slug = f"visual-{visual_type}-{style}-zh"
            content = process_visual_template(tpl)
            tags = ["visual", visual_type, style, "zh-CN", temp_tag, tokens_tag]
            name = f"Visual {visual_type.title()} ({style}, zh)"

            if client.create_prompt(
                project_slug, slug, name, content,
                description=type_cfg.get("description", ""),
                variables=variables,
                tags=tags,
            ):
                created += 1

    log.info("  Visual prompts created: %d", created)
    return created


def step_create_images(client: PromptHubClient) -> int:
    """Step 8: Create audio-images prompts."""
    log.info("=" * 60)
    log.info("Step 8: Creating audio-images prompts")
    log.info("=" * 60)

    created = 0
    project_slug = "audio-images"

    variables = [
        {"name": "image_type", "type": "string", "required": True, "description": "图片类型名称"},
        {"name": "content_style_name", "type": "string", "required": True, "description": "内容风格名称"},
        {"name": "visual_style_prompt", "type": "string", "required": True, "description": "视觉风格描述"},
        {"name": "primary_color", "type": "string", "required": True, "description": "主色调"},
        {"name": "secondary_color", "type": "string", "required": True, "description": "强调色"},
        {"name": "background_color", "type": "string", "required": True, "description": "背景色"},
        {"name": "description", "type": "string", "required": True, "description": "图片描述"},
        {"name": "key_texts_formatted", "type": "string", "required": True, "description": "关键文字"},
        {"name": "layout_instructions", "type": "string", "required": True, "description": "布局要求"},
    ]

    for locale in ["zh-CN", "en-US"]:
        loc_short = locale_short(locale)
        data = load_json(TEMPLATES_DIR / "images" / f"{locale}.json")
        base_prompt = data.get("base_prompt", "")
        if not base_prompt:
            continue

        slug = f"images-baseprompt-{loc_short}"
        content = process_images_template(base_prompt)
        tags = ["images", "base-prompt", locale]
        name = f"Image Base Prompt ({loc_short})"

        if client.create_prompt(
            project_slug, slug, name, content,
            description="AI 配图生成基础提示词模板",
            variables=variables,
            tags=tags,
        ):
            created += 1

    log.info("  Images prompts created: %d", created)
    return created


def step_verify(client: PromptHubClient, counts: Dict[str, int]) -> None:
    """Step 9: Verify all prompts were created."""
    log.info("=" * 60)
    log.info("Step 9: Verification")
    log.info("=" * 60)

    if client.dry_run:
        log.info("  [DRY-RUN] Skipping verification")
        return

    # Map migration step names to project slugs
    step_to_project = {
        "shared": "audio-shared",
        "summary": "audio-summary",
        "segmentation": "audio-segmentation",
        "visual": "audio-visual",
        "images": "audio-images",
    }
    total_expected = sum(counts.values())
    total_actual = 0

    for step_name, project_slug in step_to_project.items():
        exp_count = counts.get(step_name, 0)
        actual = client.verify_project(project_slug)
        status = "OK" if actual >= exp_count else "MISMATCH"
        log.info("  %s: expected=%d, actual=%d [%s]", project_slug, exp_count, actual, status)
        total_actual += actual

    log.info("  Total: expected=%d, actual=%d", total_expected, total_actual)
    if total_actual >= total_expected:
        log.info("  Verification PASSED")
    else:
        log.warning("  Verification FAILED: some prompts may be missing")


# ===================================================================
# Main
# ===================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate local prompts to PromptHub")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="PromptHub base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        default="ph-dev-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        help="PromptHub API key",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without executing",
    )
    args = parser.parse_args()

    log.info("PromptHub Migration Script")
    log.info("  Base URL: %s", args.base_url)
    log.info("  Dry run: %s", args.dry_run)
    log.info("  Templates dir: %s", TEMPLATES_DIR)

    # Verify templates directory exists
    if not TEMPLATES_DIR.exists():
        log.error("Templates directory not found: %s", TEMPLATES_DIR)
        sys.exit(1)

    client = PromptHubClient(args.base_url, args.api_key, args.dry_run)

    # Step 1: Check connectivity
    if not args.dry_run:
        log.info("=" * 60)
        log.info("Step 1: Checking PromptHub connectivity")
        log.info("=" * 60)
        if not client.check_connectivity():
            log.error("Cannot reach PromptHub service. Aborting.")
            sys.exit(1)

    # Step 2: Load existing state for idempotency
    log.info("=" * 60)
    log.info("Step 2: Loading existing state")
    log.info("=" * 60)
    client._get_existing_projects()
    for slug in [p["slug"] for p in PROJECTS]:
        pid = client._project_ids.get(slug)
        if pid:
            client._get_existing_prompts(slug, pid)

    # Step 3: Create projects
    if not step_create_projects(client):
        log.error("Failed to create all projects. Continuing anyway...")

    # Load prompt state for newly created projects
    for slug in [p["slug"] for p in PROJECTS]:
        pid = client._project_ids.get(slug)
        if pid and slug not in client._prompt_slugs:
            client._get_existing_prompts(slug, pid)

    # Step 4-8: Create prompts
    counts = {}
    counts["shared"] = step_create_shared(client)
    counts["summary"] = step_create_summary(client)
    counts["segmentation"] = step_create_segmentation(client)
    counts["visual"] = step_create_visual(client)
    counts["images"] = step_create_images(client)

    total = sum(counts.values())
    log.info("=" * 60)
    log.info("Migration Summary")
    log.info("=" * 60)
    for k, v in counts.items():
        log.info("  %s: %d prompts", k, v)
    log.info("  Total: %d prompts", total)

    # Step 9: Verify
    step_verify(client, counts)

    log.info("Done!")


if __name__ == "__main__":
    main()
