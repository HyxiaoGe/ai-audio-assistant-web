"""Tests for the unified user identity refactoring.

Covers:
- CurrentUser dataclass
- is_admin_user (scope-based)
- UserProfile model structure
- user_preferences layered logic (app-level only)
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

# Mock the 'auth' package before importing anything from app
if "auth" not in sys.modules:
    _auth_mock = ModuleType("auth")
    _auth_mock.AuthenticatedUser = MagicMock  # type: ignore[attr-defined]
    _auth_mock.JWTValidator = MagicMock  # type: ignore[attr-defined]
    sys.modules["auth"] = _auth_mock

from app.api.deps import CurrentUser, is_admin_user  # noqa: E402
from app.models.user import UserProfile  # noqa: E402
from app.services.user_preferences import (  # noqa: E402
    DEFAULT_NOTIFICATIONS,
    DEFAULT_TASK_DEFAULTS,
    get_app_preferences,
)

# ── CurrentUser dataclass ──


class TestCurrentUser:
    def test_basic_creation(self):
        user = CurrentUser(id="abc-123", email="test@example.com")
        assert user.id == "abc-123"
        assert user.email == "test@example.com"
        assert user.scopes == []

    def test_with_scopes(self):
        user = CurrentUser(id="abc", email="a@b.com", scopes=["admin"])
        assert user.scopes == ["admin"]

    def test_id_is_string(self):
        user = CurrentUser(id="f6d3827e-3827-4c4c-8e5e-6880a1c05f22", email="x@y.com")
        assert isinstance(user.id, str)


# ── Admin check ──


class TestIsAdminUser:
    def test_admin_scope(self):
        user = CurrentUser(id="1", email="admin@test.com", scopes=["admin"])
        assert is_admin_user(user) is True

    def test_user_scope_not_admin(self):
        user = CurrentUser(id="2", email="user@test.com", scopes=["user"])
        assert is_admin_user(user) is False

    def test_empty_scopes_not_admin(self):
        user = CurrentUser(id="3", email="user@test.com", scopes=[])
        assert is_admin_user(user) is False

    def test_multiple_scopes(self):
        user = CurrentUser(id="4", email="a@b.com", scopes=["user", "admin", "special"])
        assert is_admin_user(user) is True


# ── UserProfile model ──


class TestUserProfileModel:
    def test_tablename(self):
        assert UserProfile.__tablename__ == "user_profiles"

    def test_has_app_settings(self):
        cols = {c.name for c in UserProfile.__table__.columns}
        assert "app_settings" in cols
        assert "status" in cols

    def test_no_identity_columns(self):
        cols = {c.name for c in UserProfile.__table__.columns}
        assert "email" not in cols
        assert "name" not in cols
        assert "avatar_url" not in cols
        assert "phone" not in cols
        assert "locale" not in cols
        assert "timezone" not in cols
        assert "settings" not in cols


# ── App preferences (local layer only) ──


class TestAppPreferences:
    def _make_profile(self, app_settings):
        """Create a duck-typed profile with app_settings attribute."""
        from types import SimpleNamespace

        return SimpleNamespace(app_settings=app_settings)

    def test_defaults_when_empty(self):
        profile = self._make_profile({})
        prefs = get_app_preferences(profile)
        assert prefs["task_defaults"] == DEFAULT_TASK_DEFAULTS
        assert prefs["notifications"] == DEFAULT_NOTIFICATIONS

    def test_merge_with_stored(self):
        profile = self._make_profile(
            {
                "preferences": {
                    "task_defaults": {"language": "en", "summary_style": "brief"},
                    "notifications": {"task_completed": False},
                }
            }
        )
        prefs = get_app_preferences(profile)
        assert prefs["task_defaults"]["language"] == "en"
        assert prefs["task_defaults"]["summary_style"] == "brief"
        # Default fields still present
        assert prefs["task_defaults"]["enable_speaker_diarization"] is True
        assert prefs["notifications"]["task_completed"] is False
        assert prefs["notifications"]["task_failed"] is True

    def test_no_ui_in_app_prefs(self):
        """App preferences should not contain UI preferences (locale/timezone)."""
        profile = self._make_profile({})
        prefs = get_app_preferences(profile)
        assert "ui" not in prefs

    def test_handles_none_app_settings(self):
        profile = self._make_profile(None)
        prefs = get_app_preferences(profile)
        assert prefs["task_defaults"] == DEFAULT_TASK_DEFAULTS
