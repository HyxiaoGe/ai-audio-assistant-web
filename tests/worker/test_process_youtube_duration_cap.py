from __future__ import annotations

from typing import Any

import pytest

from worker.tasks import process_youtube


def test_duration_over_cap_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_youtube.settings, "INGEST_MAX_DURATION_SECONDS", 14400)
    assert process_youtube._duration_over_cap(14401) is True
    assert process_youtube._duration_over_cap(14400) is False
    assert process_youtube._duration_over_cap(10) is False
    assert process_youtube._duration_over_cap(None) is False  # 站点未报时长 → 不拦


def test_duration_over_cap_disabled_when_non_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_youtube.settings, "INGEST_MAX_DURATION_SECONDS", 0)
    assert process_youtube._duration_over_cap(999999) is False


class _FakeYDL:
    _side_effects: list[Any] = []
    _calls: dict[str, int] = {"n": 0}

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts

    def __enter__(self) -> _FakeYDL:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> Any:
        i = _FakeYDL._calls["n"]
        _FakeYDL._calls["n"] += 1
        effect = _FakeYDL._side_effects[i]
        if isinstance(effect, Exception):
            raise effect
        return effect


def test_extract_youtube_info_returns_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_youtube.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(process_youtube.settings, "YOUTUBE_RESOLVE_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(process_youtube, "_youtube_ydl_opts", lambda: {})
    _FakeYDL._calls["n"] = 0
    _FakeYDL._side_effects = [{"title": "X", "url": "u", "duration": 321}]
    monkeypatch.setattr(process_youtube, "YoutubeDL", _FakeYDL)

    direct_url, title, duration, channel_id, channel_name = process_youtube._extract_youtube_info("https://x/y")
    assert (direct_url, title, duration) == ("u", "X", 321)


def test_extract_youtube_info_duration_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_youtube.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(process_youtube.settings, "YOUTUBE_RESOLVE_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(process_youtube, "_youtube_ydl_opts", lambda: {})
    _FakeYDL._calls["n"] = 0
    _FakeYDL._side_effects = [{"title": "X", "url": "u"}]
    monkeypatch.setattr(process_youtube, "YoutubeDL", _FakeYDL)

    _, _, duration, _, _ = process_youtube._extract_youtube_info("https://x/y")
    assert duration is None


def test_extract_youtube_info_returns_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_youtube.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(process_youtube.settings, "YOUTUBE_RESOLVE_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(process_youtube, "_youtube_ydl_opts", lambda: {})
    _FakeYDL._calls["n"] = 0
    _FakeYDL._side_effects = [{"title": "X", "url": "u", "duration": 10, "channel_id": "UCabc", "channel": "Lex"}]
    monkeypatch.setattr(process_youtube, "YoutubeDL", _FakeYDL)

    _, _, _, channel_id, channel_name = process_youtube._extract_youtube_info("https://x/y")
    assert (channel_id, channel_name) == ("UCabc", "Lex")
