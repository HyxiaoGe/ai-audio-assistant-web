"""Tests for first_ready_image_url(图集中首张 ready 配图的原始代理 URL)。"""

import pytest

from app.services.summary import public_image
from app.services.summary.public_image import first_ready_image_url, public_summary_image_url

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


# ---------------------------------------------------------------------------
# public_summary_image_url
# ---------------------------------------------------------------------------

_PROXY = "/api/v1/summaries/images/u/t/a.webp"


async def test_public_url_none_for_non_str_or_empty():
    assert await public_summary_image_url(None, "ready") is None
    assert await public_summary_image_url(123, "ready") is None
    assert await public_summary_image_url("", "ready") is None


async def test_public_url_passthrough_when_not_ready():
    # 非 ready 不换发,原样返回代理 URL
    assert await public_summary_image_url(_PROXY, "pending") == _PROXY


async def test_public_url_passthrough_when_not_proxy_prefix():
    other = "https://cdn.example.com/x.webp"
    assert await public_summary_image_url(other, "ready") == other


async def test_public_url_presigns_ready_proxy_url(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, int]] = []

    async def _fake(object_key: str, expires: int) -> str:
        calls.append((object_key, expires))
        return f"https://oss/{object_key}?Signature=sig"

    monkeypatch.setattr(public_image, "build_presigned_media_url", _fake)
    out = await public_summary_image_url(_PROXY, "ready")
    assert out == "https://oss/summary_images/u/t/a.webp?Signature=sig"
    assert calls == [("summary_images/u/t/a.webp", 600)]


async def test_public_url_falls_back_to_proxy_on_presign_failure(monkeypatch: pytest.MonkeyPatch):
    async def _fail(object_key: str, expires: int) -> None:
        return None

    monkeypatch.setattr(public_image, "build_presigned_media_url", _fail)
    assert await public_summary_image_url(_PROXY, "ready") == _PROXY
