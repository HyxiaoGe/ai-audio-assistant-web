from __future__ import annotations

import asyncio
import base64
import json
import time
import urllib.request
from typing import Optional

from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.asr.v20190614 import asr_client, models

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.asr.base import ASRService, TranscriptSegment


class TencentASRService(ASRService):
    _BASE64_MAX_BYTES = 9 * 1024 * 1024

    def __init__(self) -> None:
        secret_id = settings.TENCENT_SECRET_ID
        secret_key = settings.TENCENT_SECRET_KEY
        region = settings.TENCENT_REGION
        if not secret_id or not secret_key or not region:
            raise RuntimeError(
                "TENCENT_SECRET_ID/TENCENT_SECRET_KEY/TENCENT_REGION is not set"
            )
        self._secret_id = secret_id
        self._secret_key = secret_key
        self._region = region

    async def transcribe(
        self, audio_url: str, status_callback=None
    ) -> list[TranscriptSegment]:
        if not audio_url:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="audio_url")
        if status_callback:
            await status_callback("asr_submitting")
        task_id = await asyncio.to_thread(self._create_task, audio_url)
        if status_callback:
            await status_callback("asr_polling")
        result = await self._poll_task(task_id)
        return self._parse_result(result)

    def _create_client(self) -> asr_client.AsrClient:
        cred = credential.Credential(self._secret_id, self._secret_key)
        http_profile = HttpProfile()
        client_profile = ClientProfile(httpProfile=http_profile)
        return asr_client.AsrClient(cred, self._region, client_profile)

    def _create_task(self, audio_url: str) -> str:
        engine_model_type = settings.TENCENT_ASR_ENGINE_MODEL_TYPE
        channel_num = settings.TENCENT_ASR_CHANNEL_NUM
        source_type = settings.TENCENT_ASR_SOURCE_TYPE
        res_text_format = settings.TENCENT_ASR_RES_TEXT_FORMAT
        speaker_dia = settings.TENCENT_ASR_SPEAKER_DIA
        speaker_number = settings.TENCENT_ASR_SPEAKER_NUMBER
        if (
            not engine_model_type
            or channel_num is None
            or source_type is None
            or res_text_format is None
            or speaker_dia is None
            or speaker_number is None
        ):
            raise RuntimeError("Tencent ASR settings are not set")

        request = models.CreateRecTaskRequest()
        request.EngineModelType = engine_model_type
        request.ChannelNum = channel_num
        request.SourceType = source_type
        if source_type == 1:
            if self._should_use_url(audio_url):
                request.SourceType = 0
                request.Url = self._ensure_http_url(audio_url)
            else:
                try:
                    with urllib.request.urlopen(audio_url, timeout=30) as response:
                        payload = response.read()
                except Exception as exc:
                    raise BusinessError(
                        ErrorCode.ASR_SERVICE_FAILED, reason=f"fetch audio failed: {exc}"
                    ) from exc
                if len(payload) > self._BASE64_MAX_BYTES:
                    request.SourceType = 0
                    request.Url = self._ensure_http_url(audio_url)
                else:
                    request.Data = base64.b64encode(payload).decode("ascii")
        else:
            request.Url = audio_url
        request.ResTextFormat = res_text_format
        request.SpeakerDiarization = speaker_dia
        request.SpeakerNumber = speaker_number
        try:
            client = self._create_client()
            response = client.CreateRecTask(request)
        except TencentCloudSDKException as exc:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(exc)) from exc
        task_id = response.Data.TaskId if response and response.Data else None
        if not task_id:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="missing task id")
        return str(task_id)

    def _should_use_url(self, audio_url: str) -> bool:
        try:
            request = urllib.request.Request(audio_url, method="HEAD")
            with urllib.request.urlopen(request, timeout=10) as response:
                content_length = response.headers.get("Content-Length")
            if not content_length:
                return False
            return int(content_length) > self._BASE64_MAX_BYTES
        except Exception:
            return False

    def _ensure_http_url(self, audio_url: str) -> str:
        if audio_url.startswith("https://") and ".cos." in audio_url:
            return "http://" + audio_url[len("https://") :]
        return audio_url

    async def _poll_task(self, task_id: str) -> dict[str, object]:
        poll_interval = settings.TENCENT_ASR_POLL_INTERVAL
        max_wait = settings.TENCENT_ASR_MAX_WAIT_SECONDS
        if not poll_interval or not max_wait:
            raise RuntimeError("Tencent ASR polling settings are not set")

        deadline = time.time() + max_wait
        while time.time() < deadline:
            result = await asyncio.to_thread(self._describe_task, task_id)
            status = result.get("Status")
            if status == 2:
                return result
            if status == 3:
                reason = result.get("ErrorMsg") or "ASR failed"
                raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(reason))
            await asyncio.sleep(poll_interval)
        raise BusinessError(ErrorCode.ASR_SERVICE_TIMEOUT)

    def _describe_task(self, task_id: str) -> dict[str, object]:
        request = models.DescribeTaskStatusRequest()
        request.TaskId = int(task_id)
        try:
            client = self._create_client()
            response = client.DescribeTaskStatus(request)
        except TencentCloudSDKException as exc:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(exc)) from exc
        raw_data = response.Data
        if raw_data is None:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="missing data")
        payload = raw_data.to_json_string()
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="invalid data")
        return data

    def _parse_result(self, payload: dict[str, object]) -> list[TranscriptSegment]:
        result = payload.get("Result")
        if result is None:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="missing result")
        if isinstance(result, str):
            try:
                result_data = json.loads(result)
            except json.JSONDecodeError as exc:
                raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(exc)) from exc
        elif isinstance(result, list):
            result_data = result
        else:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="invalid result")

        segments: list[TranscriptSegment] = []
        for item in result_data:
            if not isinstance(item, dict):
                continue
            speaker_id = item.get("SpeakerId")
            start_time = item.get("StartTime")
            end_time = item.get("EndTime")
            text_value = item.get("Text")
            confidence = item.get("Confidence")
            segments.append(
                TranscriptSegment(
                    speaker_id=str(speaker_id) if speaker_id is not None else None,
                    start_time=float(start_time) if start_time is not None else 0.0,
                    end_time=float(end_time) if end_time is not None else 0.0,
                    content=str(text_value) if text_value is not None else "",
                    confidence=float(confidence) if confidence is not None else None,
                )
            )
        return segments
