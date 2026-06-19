"""YouTube 缩略图同源代理。

国内直连 ``i.ytimg.com`` 常常很慢甚至被墙——探索广场的 YouTube 封面卡因此裂图。这里由后端
按 video_id 抓取标准缩略图并内存缓存,前端改走同源 URL,把「国内访问 YouTube 图床」的慢/失败
从每个浏览器收敛到一次服务端抓取 + 浏览器强缓存(与 ``avatar_proxy`` 同思路)。

安全:不接收任意外部 URL——只接收 11 位 video_id(严格正则),URL 由服务端固定拼到 i.ytimg.com,
故无 SSRF 面(外部无法把请求导向内网/云元数据地址)。不跟随重定向;限制响应体大小与 image/* 类型。

实现为同步函数,由同步路由处理器调用——FastAPI 会把同步处理器丢到线程池执行,抓取的阻塞 I/O
不会卡住事件循环。
"""

from __future__ import annotations

import re
import time

import httpx

# YouTube video_id 恒为 11 位 [A-Za-z0-9_-];锚定全串杜绝路径穿越/注入。
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 缩略图基本不变,缓存一周
MAX_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 1024
_FETCH_TIMEOUT = 10.0

# video_id -> (body, content_type, fetched_at)
_cache: dict[str, tuple[bytes, str, float]] = {}


class YouTubeThumbnailError(Exception):
    """携带 (status_code, detail),由路由层翻译成 HTTP 响应。"""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def is_valid_video_id(video_id: str) -> bool:
    """仅 11 位 [A-Za-z0-9_-] 通过;其余(空/超长/含斜杠点问号)一律拒绝。"""
    return bool(_VIDEO_ID_RE.match(video_id))


def _thumbnail_url(video_id: str) -> str:
    """由校验过的 video_id 拼出 i.ytimg.com 标准缩略图 URL(服务端固定 host)。"""
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def public_thumbnail_path(video_id: str) -> str:
    """前端用的同源代理相对路径(相对 URL 避免与 nginx 反代撞 CORS)。"""
    return f"/api/v1/public/youtube-thumbnail/{video_id}"


def _evict_if_needed() -> None:
    if len(_cache) <= MAX_ENTRIES:
        return
    overflow = len(_cache) - MAX_ENTRIES
    for key, _ in sorted(_cache.items(), key=lambda kv: kv[1][2])[:overflow]:
        _cache.pop(key, None)


def fetch_thumbnail(video_id: str, *, now: float | None = None) -> tuple[bytes, str]:
    """返回 ``(image_bytes, content_type)``;非法 id 或上游失败抛 ``YouTubeThumbnailError``。"""
    moment = time.time() if now is None else now
    if not is_valid_video_id(video_id):
        raise YouTubeThumbnailError(400, "Invalid video id")

    cached = _cache.get(video_id)
    if cached is not None and moment - cached[2] < CACHE_TTL_SECONDS:
        return cached[0], cached[1]

    try:
        response = httpx.get(_thumbnail_url(video_id), timeout=_FETCH_TIMEOUT, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise YouTubeThumbnailError(502, "Thumbnail fetch failed") from exc

    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise YouTubeThumbnailError(502, "Upstream is not an image")

    body = response.content
    if len(body) > MAX_BYTES:
        raise YouTubeThumbnailError(502, "Thumbnail too large")

    _cache[video_id] = (body, content_type, moment)
    _evict_if_needed()
    return body, content_type
