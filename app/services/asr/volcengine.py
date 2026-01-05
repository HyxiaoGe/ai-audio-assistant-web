from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.i18n.codes import ErrorCode
from app.services.asr.base import ASRService, TranscriptSegment

logger = logging.getLogger("app.services.asr.volcengine")

_SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
_QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
_SUPPORTED_FORMATS = {"raw", "wav", "mp3", "ogg"}


@register_service(
    "asr",
    "volcengine",
    metadata=ServiceMetadata(
        name="volcengine",
        service_type="asr",
        priority=20,
        description="火山引擎 ASR 服务（豆包语音）",
        display_name="火山引擎语音识别",
        cost_per_million_tokens=1.0,  # 估算值，后续可按官方定价调整
        rate_limit=100,
    ),
)
class VolcengineASRService(ASRService):
    @property
    def provider(self) -> str:
        return "volcengine"

    def __init__(self) -> None:
        app_id = settings.VOLC_ASR_APP_ID
        access_token = settings.VOLC_ASR_ACCESS_TOKEN
        resource_id = settings.VOLC_ASR_RESOURCE_ID
        if not app_id or not access_token or not resource_id:
            raise RuntimeError(
                "VOLC_ASR_APP_ID/VOLC_ASR_ACCESS_TOKEN/VOLC_ASR_RESOURCE_ID is not set"
            )

        self._app_id = app_id
        self._access_token = access_token
        self._resource_id = resource_id
        self._model_name = settings.VOLC_ASR_MODEL_NAME or "bigmodel"
        self._model_version = settings.VOLC_ASR_MODEL_VERSION
        self._language = settings.VOLC_ASR_LANGUAGE
        self._enable_itn = (
            settings.VOLC_ASR_ENABLE_ITN if settings.VOLC_ASR_ENABLE_ITN is not None else True
        )
        self._show_utterances = (
            settings.VOLC_ASR_SHOW_UTTERANCES
            if settings.VOLC_ASR_SHOW_UTTERANCES is not None
            else True
        )
        self._poll_interval = settings.VOLC_ASR_POLL_INTERVAL or 3
        self._max_wait = settings.VOLC_ASR_MAX_WAIT_SECONDS or 600

    @monitor("asr", "volcengine")
    async def transcribe(
        self,
        audio_url: str,
        status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> list[TranscriptSegment]:
        if not audio_url:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="audio_url")

        if status_callback:
            await status_callback("asr_submitting")

        request_id = await self._submit_task(audio_url)

        if status_callback:
            await status_callback("asr_polling")

        result = await self._poll_task(request_id)
        return self._parse_result(result)

    async def _submit_task(self, audio_url: str) -> str:
        request_id = str(uuid4())
        headers = self._build_headers(request_id, include_sequence=True)
        payload = self._build_payload(audio_url)

        logger.info("Volcengine ASR submitting task %s", request_id)

        async with httpx.AsyncClient(timeout=self._poll_interval * 2) as client:
            response = await client.post(_SUBMIT_URL, headers=headers, json=payload)
            response.raise_for_status()
            status_code = response.headers.get("X-Api-Status-Code")
            status_message = response.headers.get("X-Api-Message")

        if status_code != "20000000":
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED,
                reason=f"Submit failed: {status_code} {status_message}",
            )

        return request_id

    async def _poll_task(self, request_id: str) -> dict[str, object]:
        headers = self._build_headers(request_id, include_sequence=False)
        deadline = time.time() + self._max_wait
        poll_count = 0

        logger.info(
            "Volcengine ASR polling task %s: interval=%ss, max_wait=%ss",
            request_id,
            self._poll_interval,
            self._max_wait,
        )

        async with httpx.AsyncClient(timeout=self._poll_interval * 2) as client:
            while time.time() < deadline:
                poll_count += 1
                response = await client.post(_QUERY_URL, headers=headers, json={})
                response.raise_for_status()
                status_code = response.headers.get("X-Api-Status-Code")
                status_message = response.headers.get("X-Api-Message")

                logger.info(
                    "Volcengine ASR poll #%s for task %s: status=%s",
                    poll_count,
                    request_id,
                    status_code,
                )

                if status_code == "20000000":
                    return response.json()
                if status_code in {"20000001", "20000002"}:
                    await asyncio.sleep(self._poll_interval)
                    continue

                raise BusinessError(
                    ErrorCode.ASR_SERVICE_FAILED,
                    reason=f"Query failed: {status_code} {status_message}",
                )

        raise BusinessError(ErrorCode.ASR_SERVICE_TIMEOUT)

    def _build_headers(self, request_id: str, *, include_sequence: bool) -> dict[str, str]:
        headers = {
            "X-Api-App-Key": self._app_id,
            "X-Api-Access-Key": self._access_token,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Request-Id": request_id,
            "Content-Type": "application/json",
        }
        if include_sequence:
            headers["X-Api-Sequence"] = "-1"
        return headers

    def _build_payload(self, audio_url: str) -> dict[str, object]:
        audio_format = self._guess_audio_format(audio_url)
        audio = {
            "url": audio_url,
            "format": audio_format,
        }
        if self._language:
            audio["language"] = self._language

        request = {
            "model_name": self._model_name,
            "enable_itn": self._enable_itn,
            "show_utterances": self._show_utterances,
        }
        if self._model_version:
            if self._resource_id == "volc.bigasr.auc":
                request["model_version"] = self._model_version
            else:
                logger.warning(
                    "VOLC_ASR_MODEL_VERSION ignored for resource_id=%s", self._resource_id
                )

        return {
            "audio": audio,
            "request": request,
        }

    def _guess_audio_format(self, audio_url: str) -> str:
        parsed = urlparse(audio_url)
        suffix = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
        if suffix in _SUPPORTED_FORMATS:
            return suffix
        raise BusinessError(
            ErrorCode.UNSUPPORTED_FILE_FORMAT, allowed=", ".join(_SUPPORTED_FORMATS)
        )

    def _parse_result(self, payload: dict[str, object]) -> list[TranscriptSegment]:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="missing result")

        segments: list[TranscriptSegment] = []
        utterances = result.get("utterances")
        if isinstance(utterances, list) and utterances:
            for item in utterances:
                if not isinstance(item, dict):
                    continue
                text_value = item.get("text") or ""
                start_ms = item.get("start_time") or 0
                end_ms = item.get("end_time") or 0
                segments.append(
                    TranscriptSegment(
                        speaker_id=None,
                        start_time=float(start_ms) / 1000.0,
                        end_time=float(end_ms) / 1000.0,
                        content=str(text_value),
                        confidence=None,
                    )
                )
            return segments

        text_value = result.get("text") or ""
        if text_value:
            segments.append(
                TranscriptSegment(
                    speaker_id=None,
                    start_time=0.0,
                    end_time=0.0,
                    content=str(text_value),
                    confidence=None,
                )
            )
        return segments

    async def get_task_status(self, task_id: str) -> str:
        if not task_id:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="task_id")

        headers = self._build_headers(task_id, include_sequence=False)
        async with httpx.AsyncClient(timeout=self._poll_interval * 2) as client:
            response = await client.post(_QUERY_URL, headers=headers, json={})
            response.raise_for_status()
            status_code = response.headers.get("X-Api-Status-Code")

        status_map = {
            "20000001": "processing",
            "20000002": "pending",
            "20000000": "success",
        }
        if status_code in status_map:
            return status_map[status_code]
        return "failed"

    async def cancel_task(self, task_id: str) -> bool:
        logger.warning("Cancel task not supported for Volcengine ASR: task_id=%s", task_id)
        return False

    @monitor("asr", "volcengine")
    async def batch_transcribe(
        self,
        audio_urls: list[str],
        status_callback: Optional[Callable[[str, int, int], Awaitable[None]]] = None,
    ) -> list[list[TranscriptSegment]]:
        if not audio_urls:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="audio_urls")

        results = []
        total = len(audio_urls)
        for idx, audio_url in enumerate(audio_urls, 1):
            if status_callback:
                await status_callback(f"batch_transcribing_{idx}_{total}", idx, total)
            try:
                segments = await self.transcribe(audio_url)
            except BusinessError as exc:
                logger.error("Volcengine ASR failed for %s: %s", audio_url, exc)
                segments = []
            results.append(segments)
        return results

    async def health_check(self) -> bool:
        try:
            return bool(self._app_id and self._access_token and self._resource_id)
        except Exception:
            return False

    def estimate_cost(self, duration_seconds: int) -> float:
        # 估算为 1 元/小时，后续可按官方定价调整
        price_per_hour = 1.0
        return (duration_seconds / 3600.0) * price_per_hour
