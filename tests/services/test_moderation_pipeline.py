from __future__ import annotations

import pytest

from app.services.moderation.gate import DisplayModerationOutcome
from app.services.youtube import moderation_pipeline as mp
from app.services.youtube.search_service import VideoHit


def _hit(vid: str) -> VideoHit:
    return VideoHit(video_id=vid, title=f"T {vid}", url=f"https://youtu.be/{vid}")


async def test_moderate_hits_blocklist_allowlist_cms_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    a, b, c, d = _hit("a"), _hit("b"), _hit("c"), _hit("d")

    # 黑名单剔除 d;放行表放行 b(绕过 CMS);CMS 判 c block、保留 a。
    monkeypatch.setattr(mp.blocklist_service, "filter_hits", lambda hits, bl: [h for h in hits if h.video_id != "d"])

    async def _get_allowlist(_db: object) -> str:
        return "AL"

    monkeypatch.setattr(mp.allowlist_service, "get_allowlist", _get_allowlist)
    monkeypatch.setattr(mp.allowlist_service, "is_channel_allowed", lambda h, al: h.video_id == "b")

    async def _filter_display(hits: list[VideoHit], *, request_id: str | None) -> DisplayModerationOutcome:
        # 只收到「非放行」项(a、c);判 c block
        return DisplayModerationOutcome(
            kept=[h for h in hits if h.video_id != "c"],
            blocked=[h for h in hits if h.video_id == "c"],
        )

    monkeypatch.setattr(mp.moderation_gate, "filter_display", _filter_display)

    recorded: list[VideoHit] = []

    async def _record(blocked: list[VideoHit]) -> None:
        recorded.extend(blocked)

    monkeypatch.setattr(mp.channel_flag_service, "record_flags", _record)

    kept, sensitive = await mp.moderate_hits(object(), [a, b, c, d], bl="BL")

    assert [h.video_id for h in kept] == ["a", "b"]  # d 剔黑、c 被 CMS block、a 保留、b 放行
    assert sensitive is True
    assert [h.video_id for h in recorded] == ["c"]  # 只有 CMS block 项进复核队列


async def test_moderate_hits_clean_batch_not_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = _hit("a"), _hit("b")
    monkeypatch.setattr(mp.blocklist_service, "filter_hits", lambda hits, bl: hits)

    async def _get_allowlist(_db: object) -> str:
        return "AL"

    monkeypatch.setattr(mp.allowlist_service, "get_allowlist", _get_allowlist)
    monkeypatch.setattr(mp.allowlist_service, "is_channel_allowed", lambda h, al: False)

    async def _filter_display(hits: list[VideoHit], *, request_id: str | None) -> DisplayModerationOutcome:
        return DisplayModerationOutcome(kept=list(hits), blocked=[])

    monkeypatch.setattr(mp.moderation_gate, "filter_display", _filter_display)

    async def _record(blocked: list[VideoHit]) -> None:
        return None

    monkeypatch.setattr(mp.channel_flag_service, "record_flags", _record)

    kept, sensitive = await mp.moderate_hits(object(), [a, b], bl="BL")
    assert [h.video_id for h in kept] == ["a", "b"]
    assert sensitive is False
