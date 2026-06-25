import json
import pathlib

from app.config import settings
from app.i18n.codes import ErrorCode


def test_youtube_search_settings_defaults() -> None:
    assert settings.YOUTUBE_SEARCH_CACHE_TTL_SECONDS == 21600
    assert settings.YOUTUBE_SEARCH_RESULT_LIMIT == 20
    assert settings.YOUTUBE_SEARCH_RATE_PER_USER_MIN == 20
    assert settings.YOUTUBE_SEARCH_RATE_PER_IP_MIN == 10
    assert settings.YOUTUBE_TRENDING_WINDOW_DAYS == 7
    assert settings.YOUTUBE_TRENDING_MIN_VOLUME == 20
    assert settings.YOUTUBE_TRENDING_TOP_N == 10
    assert settings.YOUTUBE_SEARCH_DENYLIST == []


def test_youtube_search_error_codes() -> None:
    assert ErrorCode.YOUTUBE_SEARCH_QUERY_BLOCKED == 40016
    assert ErrorCode.YOUTUBE_SEARCH_UNAVAILABLE == 51907


def test_youtube_search_i18n_messages_present() -> None:
    base = pathlib.Path("app/i18n")
    for name in ("zh", "en"):
        data = json.loads((base / f"{name}.json").read_text(encoding="utf-8"))
        assert data.get("40016")
        assert data.get("51907")
