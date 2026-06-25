import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.youtube import search_service as ss
from app.services.youtube.search_service import YouTubeSearchService


class _FakeYDL:
    def __init__(self, opts: dict) -> None:
        self.opts = opts

    def __enter__(self) -> "_FakeYDL":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict:
        assert url == "ytsearch3:python tutorial"
        assert download is False
        return {
            "entries": [
                {"id": "abc123", "title": "Py 1", "channel": "Chan A", "channel_id": "ucx"},
                {"id": "def456", "title": "Py 2", "uploader": "Chan B"},
                {"title": "no id -> skipped"},
                None,
            ]
        }


class _BoomYDL:
    def __init__(self, opts: dict) -> None:
        pass

    def __enter__(self) -> "_BoomYDL":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict:
        raise RuntimeError("HTTP 429 blocked")


async def test_search_maps_entries_to_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "YoutubeDL", _FakeYDL)
    hits = await YouTubeSearchService().search("python tutorial", 3)
    assert [h.video_id for h in hits] == ["abc123", "def456"]
    assert hits[0].url == "https://www.youtube.com/watch?v=abc123"
    assert hits[0].thumbnail == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"
    assert hits[0].channel == "Chan A"
    assert hits[0].channel_id == "ucx"
    assert hits[1].channel == "Chan B"


async def test_search_raises_unavailable_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "YoutubeDL", _BoomYDL)
    with pytest.raises(BusinessError) as ei:
        await YouTubeSearchService().search("q", 3)
    assert ei.value.code == ErrorCode.YOUTUBE_SEARCH_UNAVAILABLE
