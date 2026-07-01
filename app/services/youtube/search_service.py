from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel
from yt_dlp import YoutubeDL

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode

logger = logging.getLogger(__name__)

_SEARCH_YDL_OPTS: dict[str, Any] = {
    "extract_flat": True,  # 只取搜索结果列表元数据,不进每条视频详情(快 + 零额外请求)
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    # 交互式搜索用独立紧超时 + 有限重试(与 worker 下载路径的 YOUTUBE_SOCKET_TIMEOUT 解耦):
    # 卡死快速失败换新连接重试,让常见情况不至于单连接挂住数十秒;真正的封顶由 search() 的 wait_for 负责。
    "socket_timeout": settings.YOUTUBE_SEARCH_SOCKET_TIMEOUT,
    "retries": settings.YOUTUBE_SEARCH_RETRIES,
}


class VideoHit(BaseModel):
    video_id: str
    title: str
    channel: str | None = None
    channel_id: str | None = None
    handle: str | None = None  # @handle(取自 yt-dlp uploader_id),供频道黑名单按 handle 兜底匹配
    thumbnail: str | None = None
    url: str
    view_count: int | None = None  # flat ytsearch 免费携带;供推荐排序 + 展示
    duration: int | None = None  # 秒


def _entry_to_hit(entry: dict[str, Any]) -> VideoHit | None:
    video_id = entry.get("id")
    if not isinstance(video_id, str) or not video_id:
        return None
    title = entry.get("title")
    channel = entry.get("channel") or entry.get("uploader")
    uploader_id = entry.get("uploader_id")
    raw_views = entry.get("view_count")
    raw_dur = entry.get("duration")
    return VideoHit(
        video_id=video_id,
        title=str(title) if title else video_id,
        channel=channel if isinstance(channel, str) else None,
        channel_id=entry.get("channel_id") if isinstance(entry.get("channel_id"), str) else None,
        handle=uploader_id if isinstance(uploader_id, str) else None,
        # flat 模式缩略图字段不稳定,直接由 video_id 拼 i.ytimg 直链(前端已识别该域,无需代理)
        thumbnail=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={video_id}",
        view_count=raw_views if isinstance(raw_views, int) and not isinstance(raw_views, bool) else None,
        duration=raw_dur if isinstance(raw_dur, int) and not isinstance(raw_dur, bool) else None,
    )


class YouTubeSearchService:
    """唯一的「搜索抓取」单元:yt-dlp ytsearchN(网页搜索,零 Data API 配额)。"""

    async def search(self, query: str, limit: int) -> list[VideoHit]:
        # 整体超时钳制(真正的硬封顶):CN→YouTube 抖动大,单次 ytsearch 偶尔卡到数十秒。用 wait_for 在
        # 前端 fetch 超时(30s)之前先返回本地化 51907「搜索暂时不可用」,而非让前端等到自己 abort 报
        # 「请检查网络后重试」。注:wait_for 取消不强杀底层线程,超时后线程仍会跑到 yt-dlp 自己放弃
        # (受 socket_timeout+retries 约束,秒级~数十秒)才结束;搜索有限流,泄漏窗口可接受。
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._search_sync, query, limit),
                timeout=settings.YOUTUBE_SEARCH_TOTAL_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            logger.warning(
                "youtube ytsearch overall timeout query=%r after %ss",
                query,
                settings.YOUTUBE_SEARCH_TOTAL_TIMEOUT_SECONDS,
            )
            raise BusinessError(
                ErrorCode.YOUTUBE_SEARCH_UNAVAILABLE, reason="ytsearch overall timeout"
            ) from exc

    def _search_sync(self, query: str, limit: int) -> list[VideoHit]:
        search_url = f"ytsearch{limit}:{query}"
        try:
            with YoutubeDL(_SEARCH_YDL_OPTS) as ydl:
                info = ydl.extract_info(search_url, download=False)
        except Exception as exc:  # 网络/反爬/解析失败一律归一为「搜索不可用」,端点不 500
            logger.warning("youtube ytsearch failed query=%r: %s", query, exc)
            raise BusinessError(ErrorCode.YOUTUBE_SEARCH_UNAVAILABLE, reason=str(exc)) from exc

        entries = (info or {}).get("entries") or []
        hits: list[VideoHit] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            hit = _entry_to_hit(entry)
            if hit is not None:
                hits.append(hit)
        return hits
