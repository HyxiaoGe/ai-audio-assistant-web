from __future__ import annotations

from app.services.youtube.search_service import _entry_to_hit


def test_entry_to_hit_captures_view_count_and_duration() -> None:
    hit = _entry_to_hit({"id": "abc", "title": "T", "view_count": 12345, "duration": 600})
    assert hit is not None
    assert hit.view_count == 12345
    assert hit.duration == 600


def test_entry_to_hit_non_int_view_count_and_duration_become_none() -> None:
    hit = _entry_to_hit({"id": "abc", "title": "T", "view_count": "NaN", "duration": None})
    assert hit is not None
    assert hit.view_count is None
    assert hit.duration is None


def test_entry_to_hit_missing_view_count_defaults_none() -> None:
    hit = _entry_to_hit({"id": "abc", "title": "T"})
    assert hit is not None
    assert hit.view_count is None
    assert hit.duration is None
