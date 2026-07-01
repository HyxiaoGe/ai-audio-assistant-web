from __future__ import annotations

from app.models.youtube_recommended_video import YouTubeRecommendedVideo
from app.services.youtube import recommendation_service
from app.services.youtube.search_service import VideoHit


def _hit(vid: str, views: int | None = None) -> VideoHit:
    return VideoHit(video_id=vid, title=f"T {vid}", url=f"https://youtu.be/{vid}", view_count=views)


class _ScalarsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarsResult:
        return self

    def all(self) -> list[object]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[object] | None = None) -> None:
        self.rows = rows or []
        self.added: list[YouTubeRecommendedVideo] = []
        self.executed: list[object] = []
        self.committed = False

    async def execute(self, stmt: object) -> _ScalarsResult:
        self.executed.append(stmt)
        return _ScalarsResult(self.rows)

    def add(self, obj: YouTubeRecommendedVideo) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


async def test_replace_recommendations_clears_then_inserts_ranked() -> None:
    sess = _FakeSession()
    await recommendation_service.replace_recommendations(sess, [_hit("a", 100), _hit("b", 50)])
    assert sess.committed is True
    assert [(o.rank, o.video_id) for o in sess.added] == [(0, "a"), (1, "b")]
    assert sess.added[0].view_count == 100
    assert "DELETE" in str(sess.executed[0]).upper()  # 先清空


async def test_get_recommendations_returns_videohits_in_row_order() -> None:
    rows = [
        YouTubeRecommendedVideo(rank=0, video_id="a", title="A", url="ua", view_count=100, duration=60),
        YouTubeRecommendedVideo(rank=1, video_id="b", title="B", url="ub", view_count=50, duration=None),
    ]
    sess = _FakeSession(rows=rows)
    hits = await recommendation_service.get_recommendations(sess, 12)
    assert [h.video_id for h in hits] == ["a", "b"]
    assert hits[0].view_count == 100 and hits[0].duration == 60


async def test_get_recommendations_failsafe_returns_empty_on_error() -> None:
    class _Boom:
        async def execute(self, _stmt: object) -> object:
            raise RuntimeError("db down")

    assert await recommendation_service.get_recommendations(_Boom(), 12) == []
