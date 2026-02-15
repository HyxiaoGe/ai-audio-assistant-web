"""Tests for PromptManager SDK integration.

Verifies that PromptManager correctly delegates to the PromptHub SDK
instead of making raw HTTP calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from prompthub import NotFoundError, PromptHubError
from prompthub.types import Prompt, RenderResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_UUID = UUID("11111111-1111-1111-1111-111111111111")
FAKE_PROJECT_ID = UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime.now(timezone.utc)


def _make_prompt(slug: str, content: str = "template content") -> Prompt:
    """Build a fake Prompt object."""
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
    """Build a fake RenderResult."""
    return RenderResult(
        prompt_id=FAKE_UUID,
        version="1.0.0",
        rendered_content=content,
        variables_used={},
    )


def _create_manager_with_mock() -> tuple[Any, MagicMock]:
    """Create a PromptManager with a mocked SDK client.

    Returns (manager, mock_client).
    """
    from app.prompts.manager import PromptManager

    # Reset singleton to allow fresh init
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
            # Clear _initialized so __init__ runs
            if hasattr(manager, "_initialized"):
                delattr(manager, "_initialized")
            manager.__init__()

    return manager, mock_client


# ---------------------------------------------------------------------------
# Tests: __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_client_created_when_configured(self) -> None:
        manager, mock_client = _create_manager_with_mock()
        assert manager._client is mock_client

    def test_client_none_when_not_configured(self) -> None:
        from app.prompts.manager import PromptManager

        PromptManager._instance = None

        with patch("app.config.settings") as mock_settings:
            mock_settings.PROMPTHUB_BASE_URL = None
            mock_settings.PROMPTHUB_API_KEY = None
            mock_settings.PROMPTHUB_CACHE_TTL = 300

            manager = PromptManager.__new__(PromptManager)
            if hasattr(manager, "_initialized"):
                delattr(manager, "_initialized")
            manager.__init__()

        assert manager._client is None


# ---------------------------------------------------------------------------
# Tests: _build_prompt_slug
# ---------------------------------------------------------------------------


class TestBuildPromptSlug:
    def test_normal_slug(self) -> None:
        manager, _ = _create_manager_with_mock()
        slug = manager._build_prompt_slug("summary", "overview", "zh-CN", "meeting")
        assert slug == "summary-overview-meeting-zh"

    def test_key_points_slug(self) -> None:
        manager, _ = _create_manager_with_mock()
        slug = manager._build_prompt_slug("summary", "key_points", "en-US", "podcast")
        assert slug == "summary-keypoints-podcast-en"

    def test_action_items_no_style(self) -> None:
        manager, _ = _create_manager_with_mock()
        slug = manager._build_prompt_slug(
            "summary", "action_items", "zh-CN", "meeting"
        )
        assert slug == "summary-actionitems-zh"


# ---------------------------------------------------------------------------
# Tests: get_prompt
# ---------------------------------------------------------------------------


class TestGetPrompt:
    def test_raises_when_client_none(self) -> None:
        from app.prompts.manager import PromptManager

        PromptManager._instance = None

        with patch("app.config.settings") as mock_settings:
            mock_settings.PROMPTHUB_BASE_URL = None
            mock_settings.PROMPTHUB_API_KEY = None
            mock_settings.PROMPTHUB_CACHE_TTL = 300

            manager = PromptManager.__new__(PromptManager)
            if hasattr(manager, "_initialized"):
                delattr(manager, "_initialized")
            manager.__init__()

        from app.core.exceptions import BusinessError

        with pytest.raises(BusinessError):
            manager.get_prompt("summary", "overview")

    def test_calls_sdk_get_by_slug_and_render(self) -> None:
        manager, mock_client = _create_manager_with_mock()

        # Setup mocks
        user_prompt_obj = _make_prompt("summary-overview-meeting-zh")
        system_prompt_obj = _make_prompt(
            "shared-system-role-zh", "You are an assistant"
        )
        format_rules_obj = _make_prompt("shared-format-rules-zh", "format rules text")
        image_req_obj = _make_prompt("shared-image-req-zh", "image req text")

        def get_by_slug_side_effect(slug: str, **kwargs: Any) -> Prompt:
            mapping: Dict[str, Prompt] = {
                "summary-overview-meeting-zh": user_prompt_obj,
                "shared-system-role-zh": system_prompt_obj,
                "shared-format-rules-zh": format_rules_obj,
                "shared-image-req-zh": image_req_obj,
            }
            if slug in mapping:
                return mapping[slug]
            raise NotFoundError(code=40400, message=f"Not found: {slug}")

        mock_client.prompts.get_by_slug.side_effect = get_by_slug_side_effect

        # render() returns different content based on prompt_id
        def render_side_effect(
            prompt_id: Any, variables: Any = None
        ) -> RenderResult:
            if prompt_id == user_prompt_obj.id:
                return _make_render_result("Rendered user prompt")
            elif prompt_id == system_prompt_obj.id:
                return _make_render_result("Rendered system role")
            return _make_render_result("Other rendered")

        mock_client.prompts.render.side_effect = render_side_effect

        result = manager.get_prompt(
            category="summary",
            prompt_type="overview",
            locale="zh-CN",
            variables={"transcript": "hello"},
            content_style="meeting",
        )

        # Verify result structure
        assert result["user_prompt"] == "Rendered user prompt"
        assert result["system"] == "Rendered system role"
        assert result["metadata"]["source"] == "prompthub-sdk"
        assert result["metadata"]["category"] == "summary"
        assert result["metadata"]["type"] == "overview"
        assert result["metadata"]["locale"] == "zh-CN"
        assert result["metadata"]["content_style"] == "meeting"

        # Verify SDK was called correctly
        mock_client.prompts.get_by_slug.assert_any_call(
            "summary-overview-meeting-zh"
        )
        mock_client.prompts.render.assert_any_call(
            user_prompt_obj.id,
            variables={
                "format_rules": "format rules text",
                "image_requirements": "image req text",
                "transcript": "hello",
            },
        )

    def test_raises_business_error_on_not_found(self) -> None:
        manager, mock_client = _create_manager_with_mock()
        mock_client.prompts.get_by_slug.side_effect = NotFoundError(
            code=40400, message="Not found"
        )

        from app.core.exceptions import BusinessError

        with pytest.raises(BusinessError) as exc_info:
            manager.get_prompt("summary", "overview", content_style="meeting")

        assert "Prompt not found" in exc_info.value.kwargs.get("reason", "")

    def test_raises_business_error_on_sdk_error(self) -> None:
        manager, mock_client = _create_manager_with_mock()
        mock_client.prompts.get_by_slug.side_effect = PromptHubError(
            code=50000, message="Internal error"
        )

        from app.core.exceptions import BusinessError

        with pytest.raises(BusinessError) as exc_info:
            manager.get_prompt("summary", "overview", content_style="meeting")

        assert "PromptHub error" in exc_info.value.kwargs.get("reason", "")

    def test_default_content_style_from_variables(self) -> None:
        manager, mock_client = _create_manager_with_mock()

        prompt_obj = _make_prompt("summary-overview-lecture-zh")
        mock_client.prompts.get_by_slug.return_value = prompt_obj
        mock_client.prompts.render.return_value = _make_render_result("output")

        manager.get_prompt(
            category="summary",
            prompt_type="overview",
            locale="zh-CN",
            variables={"content_style": "lecture", "transcript": "text"},
        )

        # Should have used "lecture" from variables
        mock_client.prompts.get_by_slug.assert_any_call(
            "summary-overview-lecture-zh"
        )


# ---------------------------------------------------------------------------
# Tests: _resolve_shared_vars
# ---------------------------------------------------------------------------


class TestResolveSharedVars:
    def test_fetches_format_rules_and_image_req(self) -> None:
        manager, mock_client = _create_manager_with_mock()

        fmt_prompt = _make_prompt("shared-format-rules-zh", "fmt rules")
        img_prompt = _make_prompt("shared-image-req-zh", "img requirements")

        def get_by_slug_side_effect(slug: str, **kwargs: Any) -> Prompt:
            if slug == "shared-format-rules-zh":
                return fmt_prompt
            if slug == "shared-image-req-zh":
                return img_prompt
            raise NotFoundError(code=40400, message="Not found")

        mock_client.prompts.get_by_slug.side_effect = get_by_slug_side_effect

        shared = manager._resolve_shared_vars("zh-CN")

        assert shared["format_rules"] == "fmt rules"
        assert shared["image_requirements"] == "img requirements"

    def test_returns_empty_when_not_found(self) -> None:
        manager, mock_client = _create_manager_with_mock()
        mock_client.prompts.get_by_slug.side_effect = NotFoundError(
            code=40400, message="Not found"
        )

        shared = manager._resolve_shared_vars("zh-CN")
        assert shared == {}

    def test_returns_empty_when_no_client(self) -> None:
        from app.prompts.manager import PromptManager

        PromptManager._instance = None

        with patch("app.config.settings") as mock_settings:
            mock_settings.PROMPTHUB_BASE_URL = None
            mock_settings.PROMPTHUB_API_KEY = None
            mock_settings.PROMPTHUB_CACHE_TTL = 300

            manager = PromptManager.__new__(PromptManager)
            if hasattr(manager, "_initialized"):
                delattr(manager, "_initialized")
            manager.__init__()

        assert manager._resolve_shared_vars("zh-CN") == {}


# ---------------------------------------------------------------------------
# Tests: _get_system_from_hub
# ---------------------------------------------------------------------------


class TestGetSystemFromHub:
    def test_fetches_and_renders_system_role(self) -> None:
        manager, mock_client = _create_manager_with_mock()

        system_prompt = _make_prompt("shared-system-role-zh", "system template")
        mock_client.prompts.get_by_slug.return_value = system_prompt
        mock_client.prompts.render.return_value = _make_render_result(
            "You are a meeting assistant"
        )

        result = manager._get_system_from_hub("summary", "zh-CN", "meeting")

        assert result == "You are a meeting assistant"
        mock_client.prompts.get_by_slug.assert_called_once_with(
            "shared-system-role-zh"
        )
        mock_client.prompts.render.assert_called_once_with(
            system_prompt.id, variables={"content_style": "meeting"}
        )

    def test_returns_none_on_error(self) -> None:
        manager, mock_client = _create_manager_with_mock()
        mock_client.prompts.get_by_slug.side_effect = PromptHubError(
            code=50000, message="error"
        )

        result = manager._get_system_from_hub("summary", "zh-CN", "meeting")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: get_image_prompt
# ---------------------------------------------------------------------------


class TestGetImagePrompt:
    def test_raises_when_no_client(self) -> None:
        from app.prompts.manager import PromptManager

        PromptManager._instance = None

        with patch("app.config.settings") as mock_settings:
            mock_settings.PROMPTHUB_BASE_URL = None
            mock_settings.PROMPTHUB_API_KEY = None
            mock_settings.PROMPTHUB_CACHE_TTL = 300

            manager = PromptManager.__new__(PromptManager)
            if hasattr(manager, "_initialized"):
                delattr(manager, "_initialized")
            manager.__init__()

        from app.core.exceptions import BusinessError

        with pytest.raises(BusinessError):
            manager.get_image_prompt("meeting", "cover", "desc", ["key1"])

    def test_calls_sdk_for_image_prompt(self) -> None:
        manager, mock_client = _create_manager_with_mock()

        img_prompt = _make_prompt("images-baseprompt-zh", "image template")
        mock_client.prompts.get_by_slug.return_value = img_prompt
        mock_client.prompts.render.return_value = _make_render_result(
            "Generate a cover image..."
        )

        result = manager.get_image_prompt(
            content_style="meeting",
            image_type="cover",
            description="Team meeting about Q4",
            key_texts=["Q4 Review", "Budget"],
            locale="zh-CN",
        )

        assert result == "Generate a cover image..."
        mock_client.prompts.get_by_slug.assert_called_once_with(
            "images-baseprompt-zh"
        )
        # Verify render was called with template vars
        render_call = mock_client.prompts.render.call_args
        assert render_call[0][0] == img_prompt.id
        vars_used = render_call[1]["variables"]
        assert "image_type" in vars_used
        assert "description" in vars_used
        assert vars_used["description"] == "Team meeting about Q4"

    def test_raises_on_sdk_error(self) -> None:
        manager, mock_client = _create_manager_with_mock()
        mock_client.prompts.get_by_slug.side_effect = PromptHubError(
            code=50000, message="fail"
        )

        from app.core.exceptions import BusinessError

        with pytest.raises(BusinessError):
            manager.get_image_prompt("meeting", "cover", "desc", ["key1"])


# ---------------------------------------------------------------------------
# Tests: clear_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_clears_config_cache(self) -> None:
        manager, _ = _create_manager_with_mock()
        manager._config_cache["test"] = {"data": True}
        manager.clear_cache()
        assert manager._config_cache == {}
