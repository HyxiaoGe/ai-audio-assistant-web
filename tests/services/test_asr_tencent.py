from __future__ import annotations

import json

import pytest

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.asr.tencent import TencentASRService


class _FakeData:
    def __init__(self, task_id: int | None = None, payload: dict[str, object] | None = None) -> None:
        self.TaskId = task_id
        self._payload = payload or {}

    def to_json_string(self) -> str:
        return json.dumps(self._payload)


class _FakeResponse:
    def __init__(self, task_id: int | None = None, payload: dict[str, object] | None = None) -> None:
        if task_id is not None:
            self.Data = _FakeData(task_id=task_id)
        else:
            self.Data = _FakeData(payload=payload)


class _FakeClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def CreateRecTask(self, request: object) -> _FakeResponse:
        return _FakeResponse(task_id=123)

    def DescribeTaskStatus(self, request: object) -> _FakeResponse:
        return _FakeResponse(payload=self._payload)


def _set_tencent_settings() -> None:
    settings.TENCENT_SECRET_ID = "test-secret-id"
    settings.TENCENT_SECRET_KEY = "test-secret-key"
    settings.TENCENT_REGION = "ap-shanghai"
    settings.TENCENT_ASR_ENGINE_MODEL_TYPE = "16k_zh"
    settings.TENCENT_ASR_CHANNEL_NUM = 1
    settings.TENCENT_ASR_SOURCE_TYPE = 0
    settings.TENCENT_ASR_RES_TEXT_FORMAT = 0
    settings.TENCENT_ASR_SPEAKER_DIA = 1
    settings.TENCENT_ASR_SPEAKER_NUMBER = 2
    settings.TENCENT_ASR_POLL_INTERVAL = 1
    settings.TENCENT_ASR_MAX_WAIT_SECONDS = 5


@pytest.mark.asyncio
async def test_transcribe_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tencent_settings()
    payload = {
        "Status": 2,
        "Result": [
            {
                "SpeakerId": 1,
                "StartTime": 0.0,
                "EndTime": 1.2,
                "Text": "hello",
                "Confidence": 0.9,
            }
        ],
    }
    client = _FakeClient(payload)

    def _fake_create_client(self: TencentASRService) -> _FakeClient:
        return client

    monkeypatch.setattr(TencentASRService, "_create_client", _fake_create_client)

    service = TencentASRService()
    segments = await service.transcribe("http://example.com/audio.wav")

    assert len(segments) == 1
    assert segments[0].speaker_id == "1"
    assert segments[0].content == "hello"


@pytest.mark.asyncio
async def test_transcribe_failed_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tencent_settings()
    payload = {"Status": 3, "ErrorMsg": "failed"}
    client = _FakeClient(payload)

    def _fake_create_client(self: TencentASRService) -> _FakeClient:
        return client

    monkeypatch.setattr(TencentASRService, "_create_client", _fake_create_client)

    service = TencentASRService()
    with pytest.raises(BusinessError) as exc_info:
        await service.transcribe("http://example.com/audio.wav")

    assert exc_info.value.code == ErrorCode.ASR_SERVICE_FAILED
