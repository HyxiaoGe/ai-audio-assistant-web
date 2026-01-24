from __future__ import annotations

from typing import Optional

import pytest

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.summary import Summary
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.asr.base import TranscriptSegment
from worker.tasks import process_audio


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
    def __init__(self, task: Optional[Task]) -> None:
        self._task = task

    def scalar_one_or_none(self) -> Optional[Task]:
        if self._task is None:
            return None
        if self._task.deleted_at is not None:
            return None
        return self._task


class _FakeSession:
    def __init__(self, task: Optional[Task]) -> None:
        self.task = task
        self.transcripts: list[Transcript] = []
        self.summaries: list[Summary] = []

    async def execute(self, query: object) -> _FakeResult:
        return _FakeResult(self.task)

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
    def __init__(
        self, segments: list[TranscriptSegment] | None = None, error: Exception | None = None
    ) -> None:
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


def _build_task(source_type: str, source_url: Optional[str], source_key: Optional[str]) -> Task:
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
    task = _build_task("upload", None, "audio/test.wav")
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

    monkeypatch.setattr(
        process_audio, "async_session_factory", lambda: _FakeSessionContext(session)
    )
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
    assert storage.calls == [("audio/test.wav", 60)]


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

    monkeypatch.setattr(
        process_audio, "async_session_factory", lambda: _FakeSessionContext(session)
    )
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
    task = _build_task("upload", None, "audio/test.wav")
    session = _FakeSession(task)
    error = BusinessError(ErrorCode.ASR_SERVICE_FAILED)
    asr = _FakeASRService(error=error)
    llm = _FakeLLMService()
    storage = _FakeStorageService()
    settings.UPLOAD_PRESIGN_EXPIRES = 60

    monkeypatch.setattr(
        process_audio, "async_session_factory", lambda: _FakeSessionContext(session)
    )
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
    task = _build_task("upload", None, "audio/test.wav")
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

    monkeypatch.setattr(
        process_audio, "async_session_factory", lambda: _FakeSessionContext(session)
    )
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

    monkeypatch.setattr(
        process_audio, "async_session_factory", lambda: _FakeSessionContext(session)
    )
    monkeypatch.setattr(process_audio, "publish_message", _noop_publish_message)

    await process_audio._process_task("missing-task", None)
