"""Tests for auth-service UI-preferences fetching: LAN internal-URL routing (优化①)
and the short-TTL per-token cache with invalidation-on-update (优化②).

Mocks httpx via ``httpx.MockTransport`` + patching ``AsyncClient.__init__``, mirroring
``tests/test_media_proxy.py`` / ``tests/test_llm_catalog.py``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

# Mock the 'auth' package before importing anything from app (same prelude as test_user_identity).
if "auth" not in sys.modules:
    _auth_mock = ModuleType("auth")
    _auth_mock.AuthenticatedUser = MagicMock  # type: ignore[attr-defined]
    _auth_mock.JWTValidator = MagicMock  # type: ignore[attr-defined]
    sys.modules["auth"] = _auth_mock

from app.config import Settings  # noqa: E402
from app.services import user_preferences as up  # noqa: E402


def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Route every httpx.AsyncClient created by the code under test through ``handler``."""
    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("transport", transport)  # 不覆盖显式传入的 transport（对齐 test_media_proxy）
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Each test starts with an empty preferences cache."""
    up._auth_prefs_cache.clear()
    yield
    up._auth_prefs_cache.clear()


# ── 优化①: resolved internal URL ──


class TestResolvedInternalUrl:
    def test_prefers_internal_when_set(self) -> None:
        s = Settings(
            _env_file=None,
            AUTH_SERVICE_URL="https://auth.seanfield.org",
            AUTH_SERVICE_INTERNAL_URL="http://192.168.1.11:8100",
        )
        assert s.resolved_auth_service_internal_url == "http://192.168.1.11:8100"

    def test_falls_back_to_auth_service_url_when_unset(self) -> None:
        s = Settings(
            _env_file=None,
            AUTH_SERVICE_URL="https://auth.seanfield.org",
            AUTH_SERVICE_INTERNAL_URL=None,
        )
        assert s.resolved_auth_service_internal_url == "https://auth.seanfield.org"

    def test_strips_trailing_slash(self) -> None:
        s = Settings(
            _env_file=None,
            AUTH_SERVICE_URL="https://auth.seanfield.org",
            AUTH_SERVICE_INTERNAL_URL="http://192.168.1.11:8100/",
        )
        assert s.resolved_auth_service_internal_url == "http://192.168.1.11:8100"


# ── 优化①: userinfo/profile go to the resolved internal URL ──


@pytest.mark.asyncio
async def test_get_uses_internal_url_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up.settings, "AUTH_SERVICE_INTERNAL_URL", "http://lan.test:9999")
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"preferences": {"locale": "en", "timezone": "Europe/Paris"}})

    _install_mock_transport(monkeypatch, handler)

    result = await up._get_auth_preferences("tok-internal")

    assert result == {"locale": "en", "timezone": "Europe/Paris"}
    assert len(seen) == 1
    assert seen[0].host == "lan.test"
    assert seen[0].port == 9999
    assert seen[0].path == "/auth/userinfo"


@pytest.mark.asyncio
async def test_get_falls_back_to_auth_service_url_when_internal_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(up.settings, "AUTH_SERVICE_INTERNAL_URL", None)
    monkeypatch.setattr(up.settings, "AUTH_SERVICE_URL", "http://pub.test:8100")
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"preferences": {}})

    _install_mock_transport(monkeypatch, handler)

    await up._get_auth_preferences("tok-fallback")

    assert seen[0].host == "pub.test"


# ── 优化②: short-TTL cache ──


@pytest.mark.asyncio
async def test_caches_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up.settings, "AUTH_SERVICE_INTERNAL_URL", "http://lan.test:8100")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"preferences": {"locale": "en", "timezone": "UTC"}})

    _install_mock_transport(monkeypatch, handler)

    first = await up._get_auth_preferences("tok-cache")
    second = await up._get_auth_preferences("tok-cache")

    assert calls["n"] == 1  # 第二次命中缓存，未回源
    assert first == second == {"locale": "en", "timezone": "UTC"}


@pytest.mark.asyncio
async def test_does_not_cache_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up.settings, "AUTH_SERVICE_INTERNAL_URL", "http://lan.test:8100")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    _install_mock_transport(monkeypatch, handler)

    first = await up._get_auth_preferences("tok-500")
    second = await up._get_auth_preferences("tok-500")

    assert calls["n"] == 2  # 失败不缓存，每次都重试
    assert first == second == {"locale": "zh", "timezone": "Asia/Shanghai"}


@pytest.mark.asyncio
async def test_does_not_cache_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up.settings, "AUTH_SERVICE_INTERNAL_URL", "http://lan.test:8100")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("boom")

    _install_mock_transport(monkeypatch, handler)

    result = await up._get_auth_preferences("tok-exc")
    await up._get_auth_preferences("tok-exc")

    assert calls["n"] == 2
    assert result == {"locale": "zh", "timezone": "Asia/Shanghai"}


@pytest.mark.asyncio
async def test_ttl_expiry_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up.settings, "AUTH_SERVICE_INTERNAL_URL", "http://lan.test:8100")
    clock = {"t": 1000.0}
    monkeypatch.setattr(up, "_now", lambda: clock["t"])
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"preferences": {"locale": "en", "timezone": "UTC"}})

    _install_mock_transport(monkeypatch, handler)

    await up._get_auth_preferences("tok-ttl")
    clock["t"] += up._AUTH_PREFS_CACHE_TTL + 1  # 越过 TTL
    await up._get_auth_preferences("tok-ttl")

    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_update_invalidates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up.settings, "AUTH_SERVICE_INTERNAL_URL", "http://lan.test:8100")
    get_calls = {"n": 0}
    patch_urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            patch_urls.append(request.url)
            return httpx.Response(200, json={})
        get_calls["n"] += 1
        return httpx.Response(200, json={"preferences": {"locale": "en", "timezone": "UTC"}})

    _install_mock_transport(monkeypatch, handler)

    await up._get_auth_preferences("tok-upd")  # prime cache
    assert get_calls["n"] == 1

    await up._update_auth_preferences("tok-upd", locale="zh", timezone=None)
    # 优化①：profile 的 PATCH 也走内部 LAN URL
    assert len(patch_urls) == 1
    assert patch_urls[0].host == "lan.test"
    assert patch_urls[0].path == "/auth/profile"

    await up._get_auth_preferences("tok-upd")  # 缓存已失效 → 重新回源
    assert get_calls["n"] == 2
