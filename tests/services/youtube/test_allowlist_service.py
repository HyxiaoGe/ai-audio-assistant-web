import app.services.youtube.allowlist_service as als
from app.models.youtube_allowlist import YouTubeAllowlistEntry
from app.services.youtube.allowlist_service import Allowlist
from app.services.youtube.search_service import VideoHit


def _hit(vid, channel=None, channel_id=None, handle=None) -> VideoHit:
    return VideoHit(
        video_id=vid,
        title=f"T {vid}",
        channel=channel,
        channel_id=channel_id,
        handle=handle,
        thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={vid}",
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.execute_calls = 0

    async def execute(self, _stmt):
        self.execute_calls += 1
        return _Result(self._rows)


def test_is_channel_allowed_by_id() -> None:
    al = Allowlist(channel_ids=frozenset({"UCabc"}), channel_handles=frozenset(), channel_names=frozenset())
    assert als.is_channel_allowed(_hit("v1", channel_id="UCabc"), al) is True
    assert als.is_channel_allowed(_hit("v2", channel_id="UCxyz"), al) is False


def test_is_channel_allowed_by_handle() -> None:
    al = Allowlist(channel_ids=frozenset(), channel_handles=frozenset({"globalnewstw"}), channel_names=frozenset())
    assert als.is_channel_allowed(_hit("v1", handle="@globalnewstw"), al) is True
    assert als.is_channel_allowed(_hit("v2", handle="@Other"), al) is False
    assert als.is_channel_allowed(_hit("v3", handle=None), al) is False


def test_is_channel_allowed_by_normalized_name() -> None:
    al = Allowlist(channel_ids=frozenset(), channel_handles=frozenset(), channel_names=frozenset({"lex fridman"}))
    assert als.is_channel_allowed(_hit("v1", channel="Lex  Fridman"), al) is True
    assert als.is_channel_allowed(_hit("v2", channel="Other"), al) is False


async def test_get_allowlist_partitions_rows() -> None:
    als.invalidate_cache()
    rows = [
        ("channel_id", "UCabc"),
        ("channel_handle", "globalnewstw"),
        ("channel_name", "lex fridman"),
    ]
    al = await als.get_allowlist(_FakeSession(rows))
    assert al.channel_ids == frozenset({"UCabc"})
    assert al.channel_handles == frozenset({"globalnewstw"})
    assert al.channel_names == frozenset({"lex fridman"})
    als.invalidate_cache()


async def test_get_allowlist_caches_within_ttl_and_invalidates() -> None:
    als.invalidate_cache()
    session = _FakeSession([("channel_id", "UCabc")])
    await als.get_allowlist(session)
    await als.get_allowlist(session)
    assert session.execute_calls == 1  # 第二次命中缓存
    als.invalidate_cache()
    await als.get_allowlist(session)
    assert session.execute_calls == 2  # 失效后重载
    als.invalidate_cache()


async def test_list_entries_returns_rows() -> None:
    rows = [
        YouTubeAllowlistEntry(
            match_field="channel_id", raw_value="UCabc", normalized_value="UCabc", note=None, created_by=None
        )
    ]
    out = await als.list_entries(_FakeSession(rows))
    assert out == rows
