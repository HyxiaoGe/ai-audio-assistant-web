"""Tests for normalize_content_style integration at PromptManager boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from prompthub import NotFoundError
from prompthub.types import Prompt, RenderResult

FAKE_PROJECT_ID = UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime.now(UTC)


def _make_prompt(slug: str, content: str = "template content") -> Prompt:
    return Prompt(
        id=uuid4(),
        name=slug,
        slug=slug,
        content=content,
        format="text",
        template_engine="jinja2",
        variables=None,
        tags=None,
        category=None,
        project_id=FAKE_PROJECT_ID,
        is_shared=False,
        current_version="1.0.0",
        created_at=NOW,
        updated_at=NOW,
    )


def _make_render_result(content: str = "rendered output") -> RenderResult:
    return RenderResult(
        prompt_id=uuid4(),
        version="1.0.0",
        rendered_content=content,
        variables_used={},
    )


def _create_manager_with_mock() -> tuple[Any, MagicMock]:
    from app.prompts.manager import PromptManager

    PromptManager._instance = None
    if hasattr(PromptManager, "_initialized"):
        delattr(PromptManager, "_initialized")

    with patch("app.prompts.manager.PromptHubClient") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value = mock_client

        with patch("app.config.settings") as mock_settings:
            mock_settings.PROMPTHUB_BASE_URL = "http://fake:8000"
            mock_settings.PROMPTHUB_API_KEY = "ph-fake-key"
            mock_settings.PROMPTHUB_CACHE_TTL = 60

            manager = PromptManager.__new__(PromptManager)
            if hasattr(manager, "_initialized"):
                delattr(manager, "_initialized")
            manager.__init__()  # type: ignore[misc]

    return manager, mock_client


class TestGetPromptNormalizesAndInjectsContentStyle:
    def test_deprecated_style_normalized_before_slug_lookup(self) -> None:
        manager, mock_client = _create_manager_with_mock()

        captured_slugs: list[str] = []

        def get_by_slug_side_effect(slug: str, **kwargs: Any) -> Prompt:
            captured_slugs.append(slug)
            if slug in {
                "summary-overview-conversation-zh",
                "shared-system-role-zh",
                "shared-format-rules-zh",
                "shared-image-req-zh",
            }:
                return _make_prompt(slug)
            raise NotFoundError(code=40400, message=f"Not found: {slug}")

        mock_client.prompts.get_by_slug.side_effect = get_by_slug_side_effect
        mock_client.prompts.render.return_value = _make_render_result()

        manager.get_prompt("summary", "overview", "zh-CN", content_style="podcast")

        assert any(s == "summary-overview-conversation-zh" for s in captured_slugs)
        assert not any("podcast" in s for s in captured_slugs)

    def test_render_receives_normalized_content_style_var(self) -> None:
        manager, mock_client = _create_manager_with_mock()

        def get_by_slug_side_effect(slug: str, **kwargs: Any) -> Prompt:
            return _make_prompt(slug)

        mock_client.prompts.get_by_slug.side_effect = get_by_slug_side_effect
        mock_client.prompts.render.return_value = _make_render_result()

        manager.get_prompt("summary", "overview", "zh-CN", content_style="interview")

        first_call = mock_client.prompts.render.call_args_list[0]
        variables = first_call.kwargs["variables"]
        assert variables["content_style"] == "conversation"

    def test_metadata_content_style_is_normalized(self) -> None:
        manager, mock_client = _create_manager_with_mock()
        mock_client.prompts.get_by_slug.side_effect = lambda slug, **k: _make_prompt(slug)
        mock_client.prompts.render.return_value = _make_render_result()

        result = manager.get_prompt("summary", "overview", "zh-CN", content_style="video")
        assert result["metadata"]["content_style"] == "general"

    def test_render_receives_content_style_name(self) -> None:
        """通用模板引用 {{ content_style_name }}，必须由 manager 注入(caller 不传)。"""
        manager, mock_client = _create_manager_with_mock()
        mock_client.prompts.get_by_slug.side_effect = lambda slug, **k: _make_prompt(slug)
        mock_client.prompts.render.return_value = _make_render_result()

        manager.get_prompt(
            "summary",
            "overview",
            "zh-CN",
            content_style="lecture",
            variables={"transcript": "T", "quality_notice": ""},
        )

        first_call = mock_client.prompts.render.call_args_list[0]
        variables = first_call.kwargs["variables"]
        assert "content_style_name" in variables


class TestResolveContentStyleName:
    def test_known_style_returns_localized_name(self) -> None:
        """已知风格 key 返回 images/config 的 content_style_names 本地化名。"""
        manager, _ = _create_manager_with_mock()
        zh_name = manager._resolve_content_style_name("lecture", "zh-CN")
        # 取自真实 images/config.json 的 content_style_names.zh.lecture，非 key 本身
        assert zh_name != "lecture"
        assert isinstance(zh_name, str) and zh_name

    def test_unknown_style_falls_back_to_key(self) -> None:
        manager, _ = _create_manager_with_mock()
        assert manager._resolve_content_style_name("nonexistent-style", "zh-CN") == "nonexistent-style"


class TestImageConfigNormalizes:
    def test_get_image_config_normalizes_deprecated(self) -> None:
        manager, _ = _create_manager_with_mock()
        cfg = manager.get_image_config("podcast")
        assert isinstance(cfg, dict)

    def test_get_image_prompt_normalizes_deprecated(self) -> None:
        manager, mock_client = _create_manager_with_mock()
        mock_client.prompts.get_by_slug.return_value = _make_prompt("images-baseprompt-zh")
        mock_client.prompts.render.return_value = _make_render_result()

        out = manager.get_image_prompt(
            content_style="video",
            image_type="infographic",
            description="desc",
            key_texts=["A", "B"],
            locale="zh-CN",
        )
        assert out == "rendered output"
