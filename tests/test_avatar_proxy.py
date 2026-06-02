"""同源头像代理（绕开国内直连 Google/GitHub 图床慢/被墙）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core import avatar_proxy


def _make_response(content: bytes = b"img", content_type: str = "image/jpeg") -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.headers = {"content-type": content_type}
    resp.raise_for_status = MagicMock()
    return resp


class TestIsAllowedAvatarUrl:
    def test_allows_google_https(self) -> None:
        assert avatar_proxy.is_allowed_avatar_url("https://lh3.googleusercontent.com/a/x=s96-c")

    def test_allows_github_https(self) -> None:
        assert avatar_proxy.is_allowed_avatar_url("https://avatars.githubusercontent.com/u/1?v=4")

    def test_rejects_http_scheme(self) -> None:
        assert not avatar_proxy.is_allowed_avatar_url("http://lh3.googleusercontent.com/a/x")

    def test_rejects_other_host(self) -> None:
        assert not avatar_proxy.is_allowed_avatar_url("https://evil.example/internal")

    def test_rejects_ssrf_metadata_host(self) -> None:
        # 防 SSRF：白名单之外（含云元数据地址）一律拒绝。
        assert not avatar_proxy.is_allowed_avatar_url("https://169.254.169.254/latest/meta-data")

    def test_rejects_garbage(self) -> None:
        assert not avatar_proxy.is_allowed_avatar_url("not a url")


class TestFetchAvatar:
    def setup_method(self) -> None:
        avatar_proxy._cache.clear()

    def test_disallowed_url_raises_400(self) -> None:
        with pytest.raises(avatar_proxy.AvatarProxyError) as exc:
            avatar_proxy.fetch_avatar("https://evil.example/x")
        assert exc.value.status_code == 400

    @patch("app.core.avatar_proxy.httpx.get")
    def test_fetches_and_returns_bytes(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _make_response(content=b"PNGDATA", content_type="image/png")
        body, ctype = avatar_proxy.fetch_avatar("https://lh3.googleusercontent.com/a/x=s96-c")
        assert body == b"PNGDATA"
        assert ctype == "image/png"
        mock_get.assert_called_once()

    @patch("app.core.avatar_proxy.httpx.get")
    def test_second_call_hits_cache(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _make_response(content=b"X", content_type="image/png")
        url = "https://lh3.googleusercontent.com/a/y=s96-c"
        avatar_proxy.fetch_avatar(url)
        avatar_proxy.fetch_avatar(url)
        mock_get.assert_called_once()  # 第二次走缓存

    @patch("app.core.avatar_proxy.httpx.get")
    def test_non_image_content_type_raises_502(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _make_response(content=b"<html>", content_type="text/html")
        with pytest.raises(avatar_proxy.AvatarProxyError) as exc:
            avatar_proxy.fetch_avatar("https://lh3.googleusercontent.com/a/z")
        assert exc.value.status_code == 502

    @patch("app.core.avatar_proxy.httpx.get")
    def test_oversize_raises_502(self, mock_get: MagicMock) -> None:
        big = b"x" * (avatar_proxy.MAX_BYTES + 1)
        mock_get.return_value = _make_response(content=big, content_type="image/png")
        with pytest.raises(avatar_proxy.AvatarProxyError) as exc:
            avatar_proxy.fetch_avatar("https://lh3.googleusercontent.com/a/big")
        assert exc.value.status_code == 502

    @patch("app.core.avatar_proxy.httpx.get", side_effect=httpx.HTTPError("boom"))
    def test_upstream_error_raises_502(self, _mock_get: MagicMock) -> None:
        with pytest.raises(avatar_proxy.AvatarProxyError) as exc:
            avatar_proxy.fetch_avatar("https://lh3.googleusercontent.com/a/err")
        assert exc.value.status_code == 502


class TestAvatarRoute:
    def setup_method(self) -> None:
        avatar_proxy._cache.clear()

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import create_app

        return TestClient(create_app(), raise_server_exceptions=False)

    @patch("app.core.avatar_proxy.httpx.get")
    def test_route_serves_image_with_cache_header(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _make_response(content=b"IMG", content_type="image/jpeg")
        r = self._client().get(
            "/api/v1/users/avatar",
            params={"url": "https://lh3.googleusercontent.com/a/x=s96-c"},
        )
        assert r.status_code == 200
        assert r.content == b"IMG"
        assert r.headers["content-type"] == "image/jpeg"
        assert "max-age" in r.headers.get("cache-control", "")

    def test_route_rejects_disallowed_host(self) -> None:
        # 统一响应壳：HTTPException 被包成 200 + code!=0；断言不是成功取图即可。
        r = self._client().get("/api/v1/users/avatar", params={"url": "https://evil.example/x"})
        assert r.headers.get("content-type", "").startswith("application/json")

    def test_route_requires_url_param(self) -> None:
        r = self._client().get("/api/v1/users/avatar")
        assert r.status_code in (200, 422)  # 缺参：FastAPI 422 或被统一壳包成 200+code
