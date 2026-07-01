import asyncio
import json
import pathlib

import pytest

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.youtube import search_service as ss


def test_youtube_search_settings_defaults() -> None:
    assert settings.YOUTUBE_SEARCH_CACHE_TTL_SECONDS == 21600
    assert settings.YOUTUBE_SEARCH_RESULT_LIMIT == 20
    assert settings.YOUTUBE_SEARCH_RATE_PER_USER_MIN == 20
    assert settings.YOUTUBE_SEARCH_RATE_PER_IP_MIN == 10
    assert settings.YOUTUBE_TRENDING_WINDOW_DAYS == 7
    assert settings.YOUTUBE_TRENDING_MIN_VOLUME == 20
    assert settings.YOUTUBE_TRENDING_TOP_N == 10
    assert settings.YOUTUBE_SEARCH_DENYLIST == []
    # 交互式搜索独立超时/重试(与 worker 下载路径解耦);整体上限须 < 前端 30s fetch 超时。
    assert settings.YOUTUBE_SEARCH_SOCKET_TIMEOUT == 12
    assert settings.YOUTUBE_SEARCH_RETRIES == 1
    assert settings.YOUTUBE_SEARCH_TOTAL_TIMEOUT_SECONDS == 20
    assert settings.YOUTUBE_SEARCH_TOTAL_TIMEOUT_SECONDS < 30  # 前端 REQUEST_TIMEOUT_MS,须留隧道回程余量


def test_search_ydl_opts_are_bounded() -> None:
    # ytsearch 用搜索专属紧超时 + 有限重试,不再取共享的 worker socket 超时。
    assert ss._SEARCH_YDL_OPTS["socket_timeout"] == settings.YOUTUBE_SEARCH_SOCKET_TIMEOUT
    assert ss._SEARCH_YDL_OPTS["retries"] == settings.YOUTUBE_SEARCH_RETRIES


async def test_search_overall_timeout_raises_localized_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """底层 ytsearch 卡住时,整体超时闸在前端超时前返回 51907(而非泄漏成前端网络错误)。"""

    async def _hang(*_args: object, **_kwargs: object) -> object:
        await asyncio.sleep(10)  # 模拟 ytsearch 挂死;wait_for 会取消它(纯协程,无泄漏线程)
        return []

    monkeypatch.setattr(ss.asyncio, "to_thread", _hang)
    monkeypatch.setattr(settings, "YOUTUBE_SEARCH_TOTAL_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(BusinessError) as ei:
        await ss.YouTubeSearchService().search("anything", 12)
    assert ei.value.code == ErrorCode.YOUTUBE_SEARCH_UNAVAILABLE


def test_youtube_search_error_codes() -> None:
    assert ErrorCode.YOUTUBE_SEARCH_QUERY_BLOCKED == 40016
    assert ErrorCode.YOUTUBE_SEARCH_UNAVAILABLE == 51907


def test_youtube_search_i18n_messages_present() -> None:
    base = pathlib.Path("app/i18n")
    for name in ("zh", "en"):
        data = json.loads((base / f"{name}.json").read_text(encoding="utf-8"))
        assert data.get("40016")
        assert data.get("51907")
