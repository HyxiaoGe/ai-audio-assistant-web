from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.youtube_subscription import YouTubeSubscription
from app.models.youtube_summary_style_recommendation import YouTubeSummaryStyleRecommendation
from app.models.youtube_video import YouTubeVideo
from app.services.youtube.summary_style_recommendation import (
    ALGORITHM_VERSION,
    build_video_metadata_hash,
    prewarm_summary_styles_for_videos,
    recommend_summary_style_for_video,
)


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    def __init__(self, *results: Any, commit_error: Exception | None = None) -> None:
        self._results = list(results)
        self._commit_error = commit_error
        self.added: list[Any] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, query: object) -> _ScalarResult:
        if not self._results:
            raise AssertionError(f"Unexpected query: {query}")
        return _ScalarResult(self._results.pop(0))

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        if self._commit_error:
            raise self._commit_error
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeLLM:
    provider = "proxy"
    model_name = "test-model"

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append((messages, kwargs))
        if not self._responses:
            raise AssertionError("Unexpected LLM call")
        return self._responses.pop(0)


def _video(**overrides: Any) -> YouTubeVideo:
    now = datetime(2026, 5, 23, tzinfo=UTC)
    return YouTubeVideo(
        id=overrides.pop("id", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        subscription_id=overrides.pop("subscription_id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        user_id=overrides.pop("user_id", "cccccccc-cccc-cccc-cccc-cccccccccccc"),
        video_id=overrides.pop("video_id", "yt-123"),
        channel_id=overrides.pop("channel_id", "channel-1"),
        title=overrides.pop("title", "How to build a reliable FastAPI upload workflow"),
        description=overrides.pop("description", "A step-by-step tutorial with setup tips and examples."),
        thumbnail_url=overrides.pop("thumbnail_url", None),
        published_at=overrides.pop("published_at", now),
        duration_seconds=overrides.pop("duration_seconds", 900),
        view_count=overrides.pop("view_count", None),
        like_count=overrides.pop("like_count", None),
        comment_count=overrides.pop("comment_count", None),
        last_synced_at=overrides.pop("last_synced_at", now),
        created_at=overrides.pop("created_at", now),
        updated_at=overrides.pop("updated_at", now),
        **overrides,
    )


def _subscription(**overrides: Any) -> YouTubeSubscription:
    now = datetime(2026, 5, 23, tzinfo=UTC)
    return YouTubeSubscription(
        id=overrides.pop("id", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        user_id=overrides.pop("user_id", "cccccccc-cccc-cccc-cccc-cccccccccccc"),
        channel_id=overrides.pop("channel_id", "channel-1"),
        channel_title=overrides.pop("channel_title", "Engineering Tutorials"),
        channel_thumbnail=overrides.pop("channel_thumbnail", None),
        channel_description=overrides.pop("channel_description", "Practical software tutorials."),
        subscribed_at=overrides.pop("subscribed_at", now),
        last_synced_at=overrides.pop("last_synced_at", now),
        **overrides,
    )


def test_metadata_hash_changes_when_relevant_video_metadata_changes() -> None:
    original = build_video_metadata_hash(
        title="FastAPI upload tutorial",
        description="Step-by-step guide",
        channel_title="Engineering Tutorials",
        duration_seconds=900,
    )
    changed = build_video_metadata_hash(
        title="FastAPI upload tutorial updated",
        description="Step-by-step guide",
        channel_title="Engineering Tutorials",
        duration_seconds=900,
    )

    assert original != changed


@pytest.mark.asyncio
async def test_recommend_summary_style_for_video_returns_cached_result() -> None:
    video = _video()
    subscription = _subscription()
    metadata_hash = build_video_metadata_hash(
        title=video.title,
        description=video.description,
        channel_title=subscription.channel_title,
        duration_seconds=video.duration_seconds,
    )
    cached = YouTubeSummaryStyleRecommendation(
        user_id=video.user_id,
        video_id=video.video_id,
        metadata_hash=metadata_hash,
        algorithm_version=ALGORITHM_VERSION,
        style="tutorial",
        confidence=0.88,
        reason="Cached recommendation",
    )
    session = _FakeSession(video, subscription, cached)

    result = await recommend_summary_style_for_video(session, video.user_id, video.video_id, locale="en")

    assert result.style == "tutorial"
    assert result.confidence == 0.88
    assert result.reason == "Cached recommendation"
    assert result.cached is True
    assert session.added == []
    assert session.committed is False


@pytest.mark.asyncio
async def test_recommend_summary_style_for_video_uses_llm_and_creates_cache_record_on_miss() -> None:
    video = _video()
    subscription = _subscription()
    session = _FakeSession(video, subscription, None)
    llm = _FakeLLM('{"style":"tutorial","confidence":0.84,"reason":"The metadata is clearly instructional."}')

    result = await recommend_summary_style_for_video(
        session,
        video.user_id,
        video.video_id,
        locale="en",
        llm_service=llm,
    )

    assert result.style == "tutorial"
    assert result.confidence == 0.84
    assert result.reason == "The metadata is clearly instructional."
    assert result.cached is False
    assert len(llm.calls) == 1
    messages, kwargs = llm.calls[0]
    # 见 summary_style_recommendation 注释：放大到 2048 给 deepseek-chat reasoning_content 留余量，
    # 避免推理链挤掉正文导致空返回误兜底。
    assert kwargs == {"max_tokens": 2048, "temperature": 0.2}
    assert "How to build a reliable FastAPI upload workflow" in messages[-1]["content"]
    assert "tutorial" in messages[-1]["content"]
    assert session.committed is True
    assert len(session.added) == 1
    added = session.added[0]
    assert isinstance(added, YouTubeSummaryStyleRecommendation)
    assert added.user_id == video.user_id
    assert added.video_id == video.video_id
    assert added.style == "tutorial"
    assert added.algorithm_version == ALGORITHM_VERSION


@pytest.mark.asyncio
async def test_recommend_summary_style_for_video_returns_concurrent_cached_result() -> None:
    video = _video()
    subscription = _subscription()
    metadata_hash = build_video_metadata_hash(
        title=video.title,
        description=video.description,
        channel_title=subscription.channel_title,
        duration_seconds=video.duration_seconds,
    )
    cached = YouTubeSummaryStyleRecommendation(
        user_id=video.user_id,
        video_id=video.video_id,
        metadata_hash=metadata_hash,
        algorithm_version=ALGORITHM_VERSION,
        style="tutorial",
        confidence=0.88,
        reason="Concurrent cached recommendation",
    )
    session = _FakeSession(
        video,
        subscription,
        None,
        cached,
        commit_error=IntegrityError("insert", {}, Exception("duplicate key")),
    )
    llm = _FakeLLM('{"style":"tutorial","confidence":0.84,"reason":"The metadata is clearly instructional."}')

    result = await recommend_summary_style_for_video(
        session,
        video.user_id,
        video.video_id,
        locale="en",
        llm_service=llm,
    )

    assert result.style == "tutorial"
    assert result.reason == "Concurrent cached recommendation"
    assert result.cached is True
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_prewarm_summary_styles_for_videos_dedupes_and_limits_work() -> None:
    first = _video(video_id="yt-1")
    second = _video(video_id="yt-2", title="Product review: Camera X")
    subscription = _subscription()
    session = _FakeSession(first, subscription, None, second, subscription, None)
    llm = _FakeLLM(
        '{"style":"tutorial","confidence":0.81,"reason":"Instructional metadata."}',
        '{"style":"review","confidence":0.76,"reason":"Review metadata."}',
    )

    result = await prewarm_summary_styles_for_videos(
        session,
        first.user_id,
        ["yt-1", "yt-1", "yt-2", "yt-3"],
        locale="en",
        limit=2,
        llm_service=llm,
    )

    assert result == {
        "requested_count": 4,
        "queued_count": 2,
        "generated_count": 2,
        "cached_count": 0,
        "failed_count": 0,
    }
    assert len(llm.calls) == 2
    assert [record.video_id for record in session.added] == ["yt-1", "yt-2"]


@pytest.mark.asyncio
async def test_recommend_summary_style_for_video_raises_when_video_not_found() -> None:
    session = _FakeSession(None)

    with pytest.raises(BusinessError) as exc_info:
        await recommend_summary_style_for_video(
            session,
            "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "missing-video",
            locale="en",
        )

    assert exc_info.value.code == ErrorCode.YOUTUBE_VIDEO_NOT_FOUND


@pytest.mark.asyncio
async def test_recommend_summary_style_normalizes_deprecated_podcast_to_conversation() -> None:
    video = _video()
    subscription = _subscription()
    session = _FakeSession(video, subscription, None)
    llm = _FakeLLM('{"style":"podcast","confidence":0.8,"reason":"长对话节目"}')

    result = await recommend_summary_style_for_video(
        session,
        video.user_id,
        video.video_id,
        locale="zh",
        llm_service=llm,
    )

    assert result.style == "conversation"
    assert session.committed is True
    assert session.added[0].style == "conversation"


@pytest.mark.asyncio
async def test_recommend_summary_style_unknown_falls_back_to_general() -> None:
    video = _video()
    subscription = _subscription()
    session = _FakeSession(video, subscription, None)
    llm = _FakeLLM('{"style":"totally-made-up","confidence":0.3,"reason":"x"}')

    result = await recommend_summary_style_for_video(
        session,
        video.user_id,
        video.video_id,
        locale="en",
        llm_service=llm,
    )

    assert result.style == "general"


def test_allowed_styles_is_seven_canonical() -> None:
    from app.services.youtube.summary_style_recommendation import ALLOWED_STYLES

    assert set(ALLOWED_STYLES) == {
        "meeting",
        "conversation",
        "lecture",
        "tutorial",
        "review",
        "news",
        "general",
    }
