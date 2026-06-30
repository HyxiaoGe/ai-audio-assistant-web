"""YouTube 缩略图同源代理(绕开国内直连 i.ytimg.com 慢/被墙)。

与头像代理同思路,但 URL 由服务端从「校验过的 11 位 video_id」拼出,不接收任意外部 URL,
天然无 SSRF 面(白名单 host 由服务端固定,外部只能影响路径段且受正则约束)。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core import youtube_thumbnail
from app.i18n.codes import ErrorCode


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
        youtube_thumbnail._negative_cache.clear()

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

    @patch("app.core.youtube_thumbnail.httpx.get", side_effect=httpx.HTTPError("boom"))
    def test_failed_fetch_is_negatively_cached(self, mock_get: MagicMock) -> None:
        # 负缓存:对不存在/抓取失败的 id,短时间内重复请求不应反复打 i.ytimg.com
        # (否则枚举不存在的 11 位 id = 无上限的同步出网抓取 = 线程池饿死)。
        with pytest.raises(youtube_thumbnail.YouTubeThumbnailError):
            youtube_thumbnail.fetch_thumbnail("dQw4w9WgXcQ")
        with pytest.raises(youtube_thumbnail.YouTubeThumbnailError):
            youtube_thumbnail.fetch_thumbnail("dQw4w9WgXcQ")
        mock_get.assert_called_once()  # 第二次走负缓存,未再出网

    @patch("app.core.youtube_thumbnail.httpx.get")
    def test_negative_cache_expires_and_allows_retry(self, mock_get: MagicMock) -> None:
        # TTL 过后允许重试:失败不是永久判决(上游抖动恢复后应能再次成功)。
        mock_get.side_effect = [
            httpx.HTTPError("boom"),
            _make_response(content=b"OK", content_type="image/jpeg"),
        ]
        with pytest.raises(youtube_thumbnail.YouTubeThumbnailError):
            youtube_thumbnail.fetch_thumbnail("dQw4w9WgXcQ", now=1000.0)
        body, _ = youtube_thumbnail.fetch_thumbnail(
            "dQw4w9WgXcQ", now=1000.0 + youtube_thumbnail.NEGATIVE_TTL_SECONDS + 1
        )
        assert body == b"OK"
        assert mock_get.call_count == 2

    @patch("app.core.youtube_thumbnail.httpx.get")
    def test_success_clears_prior_negative_entry(self, mock_get: MagicMock) -> None:
        # 一旦成功,负缓存条目应被清除,后续成功立即走正缓存,不被旧失败拖住。
        mock_get.side_effect = [
            httpx.HTTPError("boom"),
            _make_response(content=b"OK", content_type="image/jpeg"),
        ]
        with pytest.raises(youtube_thumbnail.YouTubeThumbnailError):
            youtube_thumbnail.fetch_thumbnail("dQw4w9WgXcQ", now=1000.0)
        body2, _ = youtube_thumbnail.fetch_thumbnail(
            "dQw4w9WgXcQ", now=1000.0 + youtube_thumbnail.NEGATIVE_TTL_SECONDS + 1
        )
        assert body2 == b"OK"
        assert "dQw4w9WgXcQ" not in youtube_thumbnail._negative_cache
        # 第三次必须走正缓存,不再出网(side_effect 只有 2 项,真出网会 StopIteration)
        body3, _ = youtube_thumbnail.fetch_thumbnail(
            "dQw4w9WgXcQ", now=1000.0 + youtube_thumbnail.NEGATIVE_TTL_SECONDS + 2
        )
        assert body3 == b"OK"
        assert mock_get.call_count == 2

    def test_negative_cache_eviction_is_bounded_and_amortized(self) -> None:
        # 负缓存是为「枚举不存在 id」设计的——正是它被填满的场景。淘汰必须有界且摊还,
        # 不能每加一个就 O(n log n) 全量排序掉一个。
        n = youtube_thumbnail.NEGATIVE_MAX_ENTRIES
        for i in range(n + 64):
            youtube_thumbnail._remember_failure(f"id{i:08d}", float(i))
        assert len(youtube_thumbnail._negative_cache) <= n  # 永不超上限
        # 触发过一次摊还淘汰 → 降到低水位附近(远小于上限),而非贴着上限每次掉一个
        assert len(youtube_thumbnail._negative_cache) <= youtube_thumbnail.NEGATIVE_LOW_WATER + 64
        # 保留最新的(最近失败的更可能被很快重复请求),淘汰最旧的
        assert f"id{n + 63:08d}" in youtube_thumbnail._negative_cache
        assert "id00000000" not in youtube_thumbnail._negative_cache


class TestThumbnailRoute:
    def setup_method(self) -> None:
        youtube_thumbnail._cache.clear()
        youtube_thumbnail._negative_cache.clear()

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

    @patch("app.core.rate_limit.get_redis_client")
    @patch("app.core.youtube_thumbnail.httpx.get")
    def test_route_is_rate_limited(self, mock_get: MagicMock, mock_redis: MagicMock) -> None:
        # 缩略图代理是匿名同步出网端点:必须按 IP 限流,否则可被枚举式刷成出网放大器。
        mock_get.return_value = _make_response(content=b"IMG", content_type="image/jpeg")

        class _Saturated:
            async def incr(self, _key: str) -> int:
                return 10**9  # 直接越过任何阈值

            async def expire(self, _key: str, _ttl: int) -> None:
                return None

        mock_redis.return_value = _Saturated()
        r = self._client().get("/api/v1/public/youtube-thumbnail/dQw4w9WgXcQ")
        assert r.status_code == 429  # 限流改真 429(信封 code 仍 40920)
        assert r.json()["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)
        mock_get.assert_not_called()  # 限流在抓取前短路,绝不出网
