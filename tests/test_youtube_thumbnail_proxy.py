"""YouTube 缩略图同源代理(绕开国内直连 i.ytimg.com 慢/被墙)。

与头像代理同思路,但 URL 由服务端从「校验过的 11 位 video_id」拼出,不接收任意外部 URL,
天然无 SSRF 面(白名单 host 由服务端固定,外部只能影响路径段且受正则约束)。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core import youtube_thumbnail


def _make_response(content: bytes = b"img", content_type: str = "image/jpeg") -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.headers = {"content-type": content_type}
    resp.raise_for_status = MagicMock()
    return resp


class TestIsValidVideoId:
    def test_accepts_standard_11_char_id(self) -> None:
        assert youtube_thumbnail.is_valid_video_id("dQw4w9WgXcQ")

    def test_accepts_dash_and_underscore(self) -> None:
        assert youtube_thumbnail.is_valid_video_id("a_b-c_d-e_f")

    def test_rejects_wrong_length(self) -> None:
        assert not youtube_thumbnail.is_valid_video_id("short")
        assert not youtube_thumbnail.is_valid_video_id("waytoolongvideoid")

    def test_rejects_path_traversal_chars(self) -> None:
        # 防注入:斜杠/点/问号一律不合法,杜绝拼出越权 URL。
        assert not youtube_thumbnail.is_valid_video_id("../../etc/pw")
        assert not youtube_thumbnail.is_valid_video_id("aaaa/bbbb/cc")

    def test_rejects_empty(self) -> None:
        assert not youtube_thumbnail.is_valid_video_id("")


class TestFetchThumbnail:
    def setup_method(self) -> None:
        youtube_thumbnail._cache.clear()

    def test_invalid_id_raises_400(self) -> None:
        with pytest.raises(youtube_thumbnail.YouTubeThumbnailError) as exc:
            youtube_thumbnail.fetch_thumbnail("../evil")
        assert exc.value.status_code == 400

    @patch("app.core.youtube_thumbnail.httpx.get")
    def test_fetches_and_returns_bytes(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _make_response(content=b"JPGDATA", content_type="image/jpeg")
        body, ctype = youtube_thumbnail.fetch_thumbnail("dQw4w9WgXcQ")
        assert body == b"JPGDATA"
        assert ctype == "image/jpeg"
        # URL 由服务端从 video_id 拼出,且打到 i.ytimg.com
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"

    @patch("app.core.youtube_thumbnail.httpx.get")
    def test_second_call_hits_cache(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _make_response(content=b"X", content_type="image/jpeg")
        youtube_thumbnail.fetch_thumbnail("dQw4w9WgXcQ")
        youtube_thumbnail.fetch_thumbnail("dQw4w9WgXcQ")
        mock_get.assert_called_once()

    @patch("app.core.youtube_thumbnail.httpx.get")
    def test_non_image_content_type_raises_502(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _make_response(content=b"<html>", content_type="text/html")
        with pytest.raises(youtube_thumbnail.YouTubeThumbnailError) as exc:
            youtube_thumbnail.fetch_thumbnail("dQw4w9WgXcQ")
        assert exc.value.status_code == 502

    @patch("app.core.youtube_thumbnail.httpx.get", side_effect=httpx.HTTPError("boom"))
    def test_upstream_error_raises_502(self, _mock_get: MagicMock) -> None:
        with pytest.raises(youtube_thumbnail.YouTubeThumbnailError) as exc:
            youtube_thumbnail.fetch_thumbnail("dQw4w9WgXcQ")
        assert exc.value.status_code == 502


class TestThumbnailRoute:
    def setup_method(self) -> None:
        youtube_thumbnail._cache.clear()

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import create_app

        return TestClient(create_app(), raise_server_exceptions=False)

    @patch("app.core.youtube_thumbnail.httpx.get")
    def test_route_serves_image_with_cache_header(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _make_response(content=b"IMG", content_type="image/jpeg")
        r = self._client().get("/api/v1/public/youtube-thumbnail/dQw4w9WgXcQ")
        assert r.status_code == 200
        assert r.content == b"IMG"
        assert r.headers["content-type"] == "image/jpeg"
        assert "max-age" in r.headers.get("cache-control", "")

    def test_route_rejects_invalid_video_id(self) -> None:
        # 非法 id:不应返回图片(走错误路径)。
        r = self._client().get("/api/v1/public/youtube-thumbnail/short")
        assert not r.headers.get("content-type", "").startswith("image/")
