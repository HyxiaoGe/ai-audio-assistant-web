"""Tests for first_ready_image_url(图集中首张 ready 配图的原始代理 URL)。"""

from app.services.summary.public_image import first_ready_image_url

_READY = "/api/v1/summaries/images/u/t/a.webp"


def test_skips_leading_pending_returns_first_ready():
    images = [
        {"status": "pending", "url": None},
        {"status": "ready", "url": _READY},
        {"status": "ready", "url": "/api/v1/summaries/images/u/t/b.webp"},
    ]
    assert first_ready_image_url(images) == _READY


def test_none_when_no_ready():
    assert first_ready_image_url([{"status": "pending", "url": None}, {"status": "failed", "url": None}]) is None


def test_none_for_empty_or_none():
    assert first_ready_image_url(None) is None
    assert first_ready_image_url([]) is None


def test_ignores_ready_without_url():
    assert first_ready_image_url([{"status": "ready", "url": None}]) is None
    assert first_ready_image_url([{"status": "ready"}]) is None


def test_tolerates_non_dict_items():
    assert first_ready_image_url(["junk", {"status": "ready", "url": _READY}]) == _READY
