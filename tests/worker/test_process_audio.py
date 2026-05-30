from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
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

    async def transcribe(self, audio_url: str) -> list[TranscriptSegment]:
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
async def test_enforce_size_limit_disabled_skips_head(
    monkeypatch: pytest.MonkeyPatch, limit: int | None
) -> None:
    monkeypatch.setattr(process_audio.settings, "UPLOAD_MAX_SIZE_BYTES", limit, raising=False)
    fake = _FakeSizeStorage(size=10**9)
    await process_audio._enforce_object_size_limit(fake, "k")
    assert fake.info_calls == []  # early-return before any HEAD
    assert fake.deleted == []


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

    await process_audio._process_task(task.id, "req-1")

    assert task.status == "failed"
    assert task.error_code == ErrorCode.FILE_TOO_LARGE.value
    assert storage.deleted == [_UPLOAD_KEY]
    assert session.transcripts == []  # aborted before transcribing
