from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.asr_usage import ASRUsage
from app.models.summary import Summary
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.asr.base import TranscriptSegment
from worker.tasks import process_audio

# Realistic upload object key: matches the `upload/{user_id}/{Y/m/d}/{32-hex}{ext}`
# shape produced by app.api.v1.upload._build_file_key for user_id="user-1".
_UPLOAD_KEY = "upload/user-1/2026/05/30/" + "a" * 32 + ".wav"


@dataclass
class _FakeQuotaConsumptionResult:
    """Fake quota consumption result"""

    free_consumed: float = 0.0
    paid_consumed: float = 0.0
    cost: float = 0.0
    remaining_free: float = 0.0


async def _fake_consume_quota(*args: Any, **kwargs: Any) -> _FakeQuotaConsumptionResult:
    """Fake consume_quota that returns a valid result"""
    return _FakeQuotaConsumptionResult()


async def _fake_ingest_task_chunks(*args: Any, **kwargs: Any) -> None:
    """Fake RAG ingest that does nothing"""
    return None


class _FakeResult:
    def __init__(self, data: Any = None) -> None:
        self._data = data

    def scalar_one_or_none(self) -> Any:
        return self._data

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._data if isinstance(self._data, list) else [])


class _FakeScalars:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _FakeSession:
    def __init__(self, task: Task | None) -> None:
        self.task = task
        self.transcripts: list[Transcript] = []
        self.summaries: list[Summary] = []

    async def execute(self, query: object) -> _FakeResult:
        # Check if query is for Task model (by checking the query string)
        query_str = str(query)
        if "tasks" in query_str.lower():
            if self.task is None or self.task.deleted_at is not None:
                return _FakeResult(None)
            return _FakeResult(self.task)
        # For non-Task queries (like AsrPricingConfig, AsrUserQuota), return None
        return _FakeResult(None)

    def add(self, item: object) -> None:
        if isinstance(item, Transcript):
            self.transcripts.append(item)
        elif isinstance(item, Summary):
            self.summaries.append(item)

    def add_all(self, items: list[object]) -> None:
        for item in items:
            self.add(item)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeASRService:
    def __init__(self, segments: list[TranscriptSegment] | None = None, error: Exception | None = None) -> None:
        self._segments = segments or []
        self._error = error
        self.transcribe_calls = 0

    async def transcribe(self, audio_url: str) -> list[TranscriptSegment]:
        self.transcribe_calls += 1
        if self._error is not None:
            raise self._error
        return self._segments


class _FakeLLMService:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self._model_name = "doubao-test-model"

    @property
    def model_name(self) -> str:
        return self._model_name

    async def summarize(self, text: str, style: str) -> str:
        if self._error is not None:
            raise self._error
        return f"summary-{style}"


class _FakeStorageService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        self.calls.append((object_name, expires_in))
        return f"https://minio.local/{object_name}?token=presigned"


async def _noop_publish_message(channel: str, message: str) -> None:
    return None


def _build_task(source_type: str, source_url: str | None, source_key: str | None) -> Task:
    return Task(
        user_id="user-1",
        content_hash="hash-1",
        title="demo",
        source_type=source_type,
        source_url=source_url,
        source_key=source_key,
    )


async def _fake_generate_summaries(
    task_id: str,
    segments: list[TranscriptSegment],
    content_style: str,
    session: Any,
    user_id: str,
    provider: str | None = None,
    model_id: str | None = None,
) -> tuple[list[Summary], dict[str, Any]]:
    """Fake summary generator"""
    summaries = [
        Summary(
            task_id=task_id,
            summary_type="overview",
            content="overview summary",
            model_used="test-model",
        ),
        Summary(
            task_id=task_id,
            summary_type="keypoints",
            content="keypoints summary",
            model_used="test-model",
        ),
        Summary(
            task_id=task_id,
            summary_type="action_items",
            content="action items summary",
            model_used="test-model",
        ),
    ]
    metadata = {
        "quality_score": "high",
        "avg_confidence": 0.95,
        "llm_provider": "test-provider",
        "llm_model": "test-model",
        "summaries_generated": 3,
    }
    return summaries, metadata


@pytest.mark.asyncio
async def test_process_audio_success_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    settings.UPLOAD_PRESIGN_EXPIRES = 60
    task = _build_task("upload", None, _UPLOAD_KEY)
    session = _FakeSession(task)

    segments = [
        TranscriptSegment(
            speaker_id="1",
            start_time=0.0,
            end_time=1.0,
            content="hello",
            confidence=0.9,
        )
    ]
    asr = _FakeASRService(segments=segments)
    llm = _FakeLLMService()
    storage = _FakeStorageService()

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", lambda: asr)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *args, **kwargs: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *args, **kwargs: storage)
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        process_audio,
        "generate_summaries_with_quality_awareness",
        _fake_generate_summaries,
    )
    monkeypatch.setattr(process_audio, "ingest_task_chunks_async", _fake_ingest_task_chunks)

    # Mock AsrFreeQuotaService.consume_quota
    from app.services import asr_free_quota_service

    monkeypatch.setattr(
        asr_free_quota_service.AsrFreeQuotaService,
        "consume_quota",
        _fake_consume_quota,
    )

    await process_audio._process_task(task.id, "req-1")

    assert task.status == "completed"
    assert len(session.transcripts) == 1
    assert len(session.summaries) == 3
    assert session.summaries[0].model_used == "test-model"
    assert storage.calls == [(_UPLOAD_KEY, 60)]


@pytest.mark.asyncio
async def test_process_audio_success_youtube(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _build_task("youtube", "https://example.com/audio.mp3", None)
    session = _FakeSession(task)
    asr = _FakeASRService(
        segments=[
            TranscriptSegment(
                speaker_id="1",
                start_time=0.0,
                end_time=1.0,
                content="hi",
                confidence=None,
            )
        ]
    )
    llm = _FakeLLMService()
    storage = _FakeStorageService()

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", lambda: asr)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *args, **kwargs: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *args, **kwargs: storage)
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        process_audio,
        "generate_summaries_with_quality_awareness",
        _fake_generate_summaries,
    )
    monkeypatch.setattr(process_audio, "ingest_task_chunks_async", _fake_ingest_task_chunks)

    # Mock AsrFreeQuotaService.consume_quota
    from app.services import asr_free_quota_service

    monkeypatch.setattr(
        asr_free_quota_service.AsrFreeQuotaService,
        "consume_quota",
        _fake_consume_quota,
    )

    await process_audio._process_task(task.id, None)

    assert task.status == "completed"
    assert storage.calls == []


@pytest.mark.asyncio
async def test_process_audio_asr_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _build_task("upload", None, _UPLOAD_KEY)
    session = _FakeSession(task)
    error = BusinessError(ErrorCode.ASR_SERVICE_FAILED)
    asr = _FakeASRService(error=error)
    llm = _FakeLLMService()
    storage = _FakeStorageService()
    settings.UPLOAD_PRESIGN_EXPIRES = 60

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", lambda: asr)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *args, **kwargs: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *args, **kwargs: storage)
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))

    await process_audio._process_task(task.id, None)

    assert task.status == "failed"
    assert task.error_code == ErrorCode.ASR_SERVICE_FAILED.value


async def _fake_generate_summaries_error(*args: Any, **kwargs: Any) -> None:
    """Fake summary generator that raises an error"""
    raise BusinessError(ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE)


@pytest.mark.asyncio
async def test_process_audio_llm_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _build_task("upload", None, _UPLOAD_KEY)
    session = _FakeSession(task)
    asr = _FakeASRService(
        segments=[
            TranscriptSegment(
                speaker_id="1",
                start_time=0.0,
                end_time=1.0,
                content="hi",
                confidence=0.8,
            )
        ]
    )
    llm = _FakeLLMService()
    storage = _FakeStorageService()
    settings.UPLOAD_PRESIGN_EXPIRES = 60

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", lambda: asr)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *args, **kwargs: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *args, **kwargs: storage)
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        process_audio,
        "generate_summaries_with_quality_awareness",
        _fake_generate_summaries_error,
    )
    monkeypatch.setattr(process_audio, "ingest_task_chunks_async", _fake_ingest_task_chunks)

    # Mock AsrFreeQuotaService.consume_quota
    from app.services import asr_free_quota_service

    monkeypatch.setattr(
        asr_free_quota_service.AsrFreeQuotaService,
        "consume_quota",
        _fake_consume_quota,
    )

    await process_audio._process_task(task.id, None)

    assert task.status == "failed"
    # process_audio wraps summary errors as AI_SUMMARY_GENERATION_FAILED
    assert task.error_code == ErrorCode.AI_SUMMARY_GENERATION_FAILED.value
    assert len(session.transcripts) == 1


@pytest.mark.asyncio
async def test_process_audio_task_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(None)

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)

    await process_audio._process_task("missing-task", None)


# --------------------------------------------------------------------------- #
# P10: server-side object size enforcement (presigned-PUT cannot cap size)
# --------------------------------------------------------------------------- #
class _FakeSizeStorage:
    def __init__(self, size: int | None = None, raise_on_info: bool = False) -> None:
        self._size = size
        self._raise = raise_on_info
        self.deleted: list[str] = []
        self.info_calls: list[str] = []

    def get_file_info(self, object_name: str) -> dict[str, Any]:
        self.info_calls.append(object_name)
        if self._raise:
            raise RuntimeError("HEAD failed")
        return {"size": self._size}

    def delete_file(self, object_name: str) -> None:
        self.deleted.append(object_name)

    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        return f"https://minio.local/{object_name}?token=presigned"


@pytest.mark.asyncio
async def test_enforce_size_limit_oversize_deletes_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_audio.settings, "UPLOAD_MAX_SIZE_BYTES", 1000, raising=False)
    fake = _FakeSizeStorage(size=1001)
    with pytest.raises(BusinessError) as ei:
        await process_audio._enforce_object_size_limit(fake, "k")
    assert ei.value.code == ErrorCode.FILE_TOO_LARGE
    assert fake.deleted == ["k"]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1000, 999])
async def test_enforce_size_limit_within_limit_ok(monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    monkeypatch.setattr(process_audio.settings, "UPLOAD_MAX_SIZE_BYTES", 1000, raising=False)
    fake = _FakeSizeStorage(size=size)
    await process_audio._enforce_object_size_limit(fake, "k")
    assert fake.deleted == []


@pytest.mark.asyncio
async def test_enforce_size_limit_head_error_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_audio.settings, "UPLOAD_MAX_SIZE_BYTES", 1000, raising=False)
    fake = _FakeSizeStorage(raise_on_info=True)
    await process_audio._enforce_object_size_limit(fake, "k")  # fail-open, no raise
    assert fake.deleted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [None, 0])
async def test_enforce_size_limit_disabled_skips_head(monkeypatch: pytest.MonkeyPatch, limit: int | None) -> None:
    monkeypatch.setattr(process_audio.settings, "UPLOAD_MAX_SIZE_BYTES", limit, raising=False)
    fake = _FakeSizeStorage(size=10**9)
    await process_audio._enforce_object_size_limit(fake, "k")
    assert fake.info_calls == []  # early-return before any HEAD
    assert fake.deleted == []


# --------------------------------------------------------------------------- #
# D5-retry: ASR money-path idempotency on Celery autoretry
# --------------------------------------------------------------------------- #
class _RetryFakeSession(_FakeSession):
    """FakeSession preloadable with a prior attempt's transcripts + ASRUsage rows.

    Lets a retry of ``_process_task`` observe the exact durable state a crashed
    earlier attempt would have left, so the idempotency branches are exercised
    end-to-end without a database.
    """

    def __init__(
        self,
        task: Task | None,
        *,
        existing_transcripts: list[Transcript] | None = None,
        usage_rows: list[ASRUsage] | None = None,
    ) -> None:
        super().__init__(task)
        self._existing = existing_transcripts or []
        self._usage_rows = usage_rows or []
        self.added_usages: list[ASRUsage] = []

    async def execute(self, query: object) -> _FakeResult:
        q = str(query).lower()
        if "asr_usages" in q:
            return _FakeResult(list(self._usage_rows))
        if "transcripts" in q:
            return _FakeResult(list(self._existing))
        if "tasks" in q:
            if self.task is None or self.task.deleted_at is not None:
                return _FakeResult(None)
            return _FakeResult(self.task)
        return _FakeResult(None)

    def add(self, item: object) -> None:
        if isinstance(item, ASRUsage):
            self.added_usages.append(item)
        super().add(item)


def _prior_transcript(task: Task) -> Transcript:
    return Transcript(
        task_id=task.id,
        speaker_id="1",
        content="hello",
        start_time=0.0,
        end_time=2.0,
        confidence=0.9,
        sequence=1,
    )


@pytest.mark.asyncio
async def test_process_audio_retry_finalizes_cost_without_recharge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Window C: transcripts persisted but cost never finalized.

    A retry must reuse the transcripts (NOT re-call the paid ASR) and finalize
    cost exactly once, flipping the existing ``processing`` claim to ``success``.
    The old transcript-keyed guard skipped all cost recording here -> under-charge.
    """
    task = _build_task("youtube", "https://example.com/audio.mp3", None)
    task.asr_provider = "volcengine"
    claim = ASRUsage(
        user_id=str(task.user_id),
        task_id=str(task.id),
        provider="volcengine",
        variant="file",
        duration_seconds=0.0,
        status="processing",
    )
    session = _RetryFakeSession(task, existing_transcripts=[_prior_transcript(task)], usage_rows=[claim])
    asr = _FakeASRService(segments=[])
    llm = _FakeLLMService()
    storage = _FakeStorageService()

    consume_calls: list[tuple] = []

    async def _tracking_consume_quota(*args: Any, **kwargs: Any) -> _FakeQuotaConsumptionResult:
        consume_calls.append((args, kwargs))
        return _FakeQuotaConsumptionResult(paid_consumed=2.0, cost=0.01)

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", lambda *args, **kwargs: asr)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *args, **kwargs: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *args, **kwargs: storage)
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(process_audio, "generate_summaries_with_quality_awareness", _fake_generate_summaries)
    monkeypatch.setattr(process_audio, "ingest_task_chunks_async", _fake_ingest_task_chunks)

    from app.services import asr_free_quota_service

    monkeypatch.setattr(
        asr_free_quota_service.AsrFreeQuotaService,
        "consume_quota",
        _tracking_consume_quota,
    )

    await process_audio._process_task(task.id, None)

    assert asr.transcribe_calls == 0  # paid ASR NOT re-run
    assert claim.status == "success"  # cost finalized on the existing claim (single record)
    assert session.added_usages == []  # reused the claim, no duplicate usage row
    assert len(consume_calls) == 1  # cost recorded exactly once
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_process_audio_retry_skips_when_already_finalized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal ``success`` ASRUsage means paid call + cost already completed.

    A retry must skip both the paid ASR and all cost recording (no double charge).
    """
    task = _build_task("youtube", "https://example.com/audio.mp3", None)
    success = ASRUsage(
        user_id=str(task.user_id),
        task_id=str(task.id),
        provider="volcengine",
        variant="file",
        duration_seconds=120.0,
        status="success",
        actual_paid_cost=1.23,
    )
    session = _RetryFakeSession(task, existing_transcripts=[_prior_transcript(task)], usage_rows=[success])
    asr = _FakeASRService(segments=[])
    llm = _FakeLLMService()
    storage = _FakeStorageService()

    consume_calls: list[tuple] = []

    async def _tracking_consume_quota(*args: Any, **kwargs: Any) -> _FakeQuotaConsumptionResult:
        consume_calls.append((args, kwargs))
        return _FakeQuotaConsumptionResult()

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", lambda *args, **kwargs: asr)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *args, **kwargs: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *args, **kwargs: storage)
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(process_audio, "generate_summaries_with_quality_awareness", _fake_generate_summaries)
    monkeypatch.setattr(process_audio, "ingest_task_chunks_async", _fake_ingest_task_chunks)

    from app.services import asr_free_quota_service

    monkeypatch.setattr(
        asr_free_quota_service.AsrFreeQuotaService,
        "consume_quota",
        _tracking_consume_quota,
    )

    await process_audio._process_task(task.id, None)

    assert asr.transcribe_calls == 0  # paid ASR NOT re-run
    assert consume_calls == []  # no cost recorded again
    assert success.duration_seconds == 120.0  # terminal record left untouched
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_process_audio_retry_finalizes_cost_when_provider_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FINALIZE_COST must survive an ASR-service construction failure.

    The provider lookup only feeds the optional ``estimate_cost``. If it raises
    on retry (e.g. transient credential/instantiation error, force_new=True),
    cost must still be finalized from the claim and the task must COMPLETE --
    not get marked failed via _mark_failed (which would brick a task that
    already holds valid transcripts).
    """
    task = _build_task("youtube", "https://example.com/audio.mp3", None)
    task.asr_provider = "volcengine"
    claim = ASRUsage(
        user_id=str(task.user_id),
        task_id=str(task.id),
        provider="volcengine",
        variant="file",
        duration_seconds=0.0,
        status="processing",
    )
    session = _RetryFakeSession(task, existing_transcripts=[_prior_transcript(task)], usage_rows=[claim])
    llm = _FakeLLMService()
    storage = _FakeStorageService()

    consume_calls: list[tuple] = []

    async def _tracking_consume_quota(*args: Any, **kwargs: Any) -> _FakeQuotaConsumptionResult:
        consume_calls.append((args, kwargs))
        return _FakeQuotaConsumptionResult(paid_consumed=2.0, cost=0.01)

    def _raise_get_asr_service(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("asr service construction failed")

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", _raise_get_asr_service)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *args, **kwargs: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *args, **kwargs: storage)
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(process_audio, "generate_summaries_with_quality_awareness", _fake_generate_summaries)
    monkeypatch.setattr(process_audio, "ingest_task_chunks_async", _fake_ingest_task_chunks)

    from app.services import asr_free_quota_service

    monkeypatch.setattr(
        asr_free_quota_service.AsrFreeQuotaService,
        "consume_quota",
        _tracking_consume_quota,
    )

    await process_audio._process_task(task.id, None)

    assert claim.status == "success"  # cost finalized despite the lookup failure
    assert session.added_usages == []  # reused the claim, no duplicate usage row
    assert len(consume_calls) == 1  # cost recorded exactly once
    assert task.status == "completed"  # NOT failed via _mark_failed


@pytest.mark.asyncio
async def test_finalize_asr_cost_keeps_claim_nonterminal_when_duration_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """时长为 0（提取静默失败 + provider 未回时间戳）不得记为终态 success。

    否则 D5-retry 的 SKIP_ALL 幂等标记会把这条零成本记录永久锁死 -> 漏计费。
    正确行为：保留 claim 的非终态 status 以便对账/重试补记，且不写任何配额。
    """
    task = _build_task("youtube", "https://example.com/audio.mp3", None)
    claim = ASRUsage(
        user_id=str(task.user_id),
        task_id=str(task.id),
        provider="volcengine",
        variant="file",
        duration_seconds=0.0,
        status="processing",
    )
    session = _RetryFakeSession(task, usage_rows=[claim])

    consume_calls: list[tuple] = []

    async def _tracking_consume_quota(*args: Any, **kwargs: Any) -> _FakeQuotaConsumptionResult:
        consume_calls.append((args, kwargs))
        return _FakeQuotaConsumptionResult()

    from app.services import asr_free_quota_service

    monkeypatch.setattr(
        asr_free_quota_service.AsrFreeQuotaService,
        "consume_quota",
        _tracking_consume_quota,
    )

    await process_audio._finalize_asr_cost(
        session,
        task,
        provider_name="volcengine",
        asr_variant="file",
        duration_seconds=0.0,
        asr_service=None,
        successful_audio_url=None,
        diarization=None,
        processing_time_ms=0,
        claim_row=claim,
    )

    assert claim.status == "processing"  # 非终态：未被锁死为零成本 success
    assert consume_calls == []  # 时长未知 -> 不写任何配额


@pytest.mark.asyncio
async def test_process_audio_oversize_upload_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_audio.settings, "UPLOAD_MAX_SIZE_BYTES", 10, raising=False)
    task = _build_task("upload", None, _UPLOAD_KEY)
    session = _FakeSession(task)
    asr = _FakeASRService(segments=[])
    llm = _FakeLLMService()
    storage = _FakeSizeStorage(size=10**9)

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", lambda: asr)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *args, **kwargs: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *args, **kwargs: storage)
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))

    await process_audio._process_task(task.id, "req-1")

    assert task.status == "failed"
    assert task.error_code == ErrorCode.FILE_TOO_LARGE.value
    assert storage.deleted == [_UPLOAD_KEY]
    assert session.transcripts == []  # aborted before transcribing


# --------------------------------------------------------------------------- #
# Phase 2: kind="task_progress" 信封标签断言
# --------------------------------------------------------------------------- #
class _CapturePublish:
    """捕获 process_audio.publish_message 发出的 (channel, message) 以断言信封 kind。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, channel: str, message: str) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_process_audio_progress_envelope_has_task_progress_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    settings.UPLOAD_PRESIGN_EXPIRES = 60
    task = _build_task("upload", None, _UPLOAD_KEY)
    session = _FakeSession(task)
    asr = _FakeASRService(
        segments=[TranscriptSegment(speaker_id="1", start_time=0.0, end_time=1.0, content="hello", confidence=0.9)]
    )
    llm = _FakeLLMService()
    storage = _FakeStorageService()
    capture = _CapturePublish()

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", lambda: asr)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *a, **k: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *a, **k: storage)
    monkeypatch.setattr(process_audio, "publish_message", capture)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(process_audio, "generate_summaries_with_quality_awareness", _fake_generate_summaries)
    monkeypatch.setattr(process_audio, "ingest_task_chunks_async", _fake_ingest_task_chunks)

    from app.services import asr_free_quota_service

    monkeypatch.setattr(asr_free_quota_service.AsrFreeQuotaService, "consume_quota", _fake_consume_quota)

    await process_audio._process_task(task.id, "req-1")

    assert capture.messages, "expected at least one published progress message"
    for raw in capture.messages:
        assert json.loads(raw)["kind"] == "task_progress"


@pytest.mark.asyncio
async def test_process_audio_failure_envelope_has_task_progress_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    settings.UPLOAD_PRESIGN_EXPIRES = 60
    task = _build_task("upload", None, _UPLOAD_KEY)
    session = _FakeSession(task)
    asr = _FakeASRService(error=BusinessError(ErrorCode.ASR_SERVICE_FAILED))
    llm = _FakeLLMService()
    storage = _FakeStorageService()
    capture = _CapturePublish()

    monkeypatch.setattr(process_audio, "async_session_factory", lambda: _FakeSessionContext(session))
    monkeypatch.setattr(process_audio, "get_asr_service", lambda: asr)
    monkeypatch.setattr(process_audio, "get_llm_service", lambda *a, **k: llm)
    monkeypatch.setattr(process_audio, "get_storage_service", lambda *a, **k: storage)
    monkeypatch.setattr(process_audio, "publish_message", capture)
    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(lambda *a, **k: None))

    await process_audio._process_task(task.id, None)

    assert task.status == "failed"
    assert capture.messages, "expected a failure progress message"
    assert json.loads(capture.messages[-1])["kind"] == "task_progress"


@pytest.mark.asyncio
async def test_process_audio_completed_calls_notify_task_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完成时不再手搓 Notification 行，改调 NotificationService.notify(TASK_COMPLETED)。"""
    from app.services.notifications.types import NotificationType

    task = _build_task("youtube", "https://example.com/a.mp3", None)
    task.duration_seconds = 123
    session = _FakeSession(task)

    calls: list[dict[str, Any]] = []

    def _spy_notify(sess: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(_spy_notify))
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)

    await process_audio._update_task(session, task, "completed", 100, "completed", "req-1")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["type"] == NotificationType.TASK_COMPLETED
    assert kwargs["user_id"] == str(task.user_id)
    assert kwargs["task_id"] == str(task.id)
    assert kwargs["params"]["task_title"] == "demo"
    assert kwargs["params"]["duration"] == 123
    # 不再写库手搓的 Notification 行
    assert not hasattr(session, "notifications") or session.notifications == []


@pytest.mark.asyncio
async def test_process_audio_failed_calls_notify_task_failed_without_raw_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败时改调 notify(TASK_FAILED)，params 只带 error_code，绝不带原始错误文本。"""
    from app.services.notifications.types import NotificationType

    task = _build_task("upload", None, _UPLOAD_KEY)
    session = _FakeSession(task)
    error = BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="boom secret internal trace")

    calls: list[dict[str, Any]] = []

    def _spy_notify(sess: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(process_audio.NotificationService, "notify", staticmethod(_spy_notify))
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)

    await process_audio._mark_failed(session, task, error, "req-1")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["type"] == NotificationType.TASK_FAILED
    assert kwargs["user_id"] == str(task.user_id)
    assert kwargs["task_id"] == str(task.id)
    assert kwargs["params"]["error_code"] == ErrorCode.ASR_SERVICE_FAILED.value
    assert kwargs["params"]["task_title"] == "demo"
    # 原始错误文本不得出现在任何 user-facing params 字段
    for value in kwargs["params"].values():
        assert "boom secret internal trace" not in str(value)
