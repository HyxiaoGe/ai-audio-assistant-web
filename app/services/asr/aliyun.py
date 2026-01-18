"""阿里云语音识别服务实现（智能语音交互 NLS）"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable, Optional

import httpx
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.i18n.codes import ErrorCode
from app.services.asr.base import ASRService, TranscriptSegment
from app.services.config_utils import get_config_value

logger = logging.getLogger("app.services.asr.aliyun")

_TOKEN_DOMAIN = "nls-meta.cn-shanghai.aliyuncs.com"  # nosec B105
_TOKEN_VERSION = "2019-02-28"  # nosec B105
_GATEWAY_URL = "https://nls-gateway.cn-shanghai.aliyuncs.com/stream/v1/FlashRecognizer"
_SUPPORTED_FORMATS = {"wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "amr"}
_TIMESTAMP_PATTERN = re.compile(r"\[(\d+):(\d+(?:\.\d+)?),(\d+):(\d+(?:\.\d+)?),(\d+)\]\s*(.*)")


@register_service(
    "asr",
    "aliyun",
    metadata=ServiceMetadata(
        name="aliyun",
        service_type="asr",
        priority=15,
        description="阿里云 ASR 服务（智能语音交互 NLS 录音文件识别）",
        display_name="阿里云语音识别",
        cost_per_million_tokens=1.0,  # 约 1.0 元/小时
        rate_limit=100,
    ),
)
class AliyunASRService(ASRService):
    """阿里云语音识别服务实现（智能语音交互 NLS）

    官方文档：
    - 录音文件识别：https://help.aliyun.com/zh/isi/developer-reference/recording-file-recognition
    - Token 鉴权：https://help.aliyun.com/document_detail/72153.html
    """

    @property
    def provider(self) -> str:
        return "aliyun"

    def __init__(self, config: Optional[object] = None) -> None:
        access_key_id = get_config_value(config, "access_key_id", settings.ALIYUN_ACCESS_KEY_ID)
        access_key_secret = get_config_value(
            config, "access_key_secret", settings.ALIYUN_ACCESS_KEY_SECRET
        )
        app_key = get_config_value(config, "app_key", settings.ALIYUN_NLS_APP_KEY)
        region = get_config_value(config, "region", "cn-shanghai")

        if not access_key_id or not access_key_secret:
            raise RuntimeError("ALIYUN_ACCESS_KEY_ID or ALIYUN_ACCESS_KEY_SECRET is not set")

        if not app_key:
            raise RuntimeError("ALIYUN_NLS_APP_KEY (NLS AppKey) is not set")

        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._app_key = app_key
        self._region = region
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

        self._poll_interval = int(get_config_value(config, "poll_interval", 5))
        self._max_wait = int(get_config_value(config, "max_wait", 600))
        self._timeout = float(get_config_value(config, "timeout", 30))

    @monitor("asr", "aliyun")
    async def transcribe(
        self,
        audio_url: str,
        status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> list[TranscriptSegment]:
        """转写音频文件"""
        if not audio_url:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="audio_url")

        if status_callback:
            await status_callback("asr_submitting")

        token = await self._get_token()
        response = await self._submit_task(audio_url, token)

        if status_callback:
            await status_callback("asr_polling")

        if response.get("task_id") and not response.get("result"):
            response = await self._poll_task(response["task_id"], token)

        return await self._parse_result(response)

    async def _get_token(self) -> str:
        now = time.time()
        if self._token and now < (self._token_expires_at - 60):
            return self._token

        async with self._token_lock:
            now = time.time()
            if self._token and now < (self._token_expires_at - 60):
                return self._token

            token, expires_at = await asyncio.to_thread(self._create_token)
            self._token = token
            self._token_expires_at = expires_at
            return token

    def _create_token(self) -> tuple[str, float]:
        client = AcsClient(self._access_key_id, self._access_key_secret, self._region)
        request = CommonRequest()
        request.set_method("POST")
        request.set_domain(_TOKEN_DOMAIN)
        request.set_version(_TOKEN_VERSION)
        request.set_action_name("CreateToken")

        try:
            response = client.do_action_with_exception(request)
            payload = json.loads(response)
        except Exception as exc:  # pragma: no cover - sdk wrapper
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Create token failed: {exc}"
            ) from exc

        token_info = payload.get("Token") if isinstance(payload, dict) else None
        if not isinstance(token_info, dict):
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="Invalid token response")

        token = token_info.get("Id")
        expire_time = token_info.get("ExpireTime")

        if not token or expire_time is None:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="Missing token info")

        try:
            expires_at = float(expire_time)
            if expires_at > 1e12:
                expires_at = expires_at / 1000.0
        except (TypeError, ValueError) as exc:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Invalid token expiry: {expire_time}"
            ) from exc

        return token, expires_at

    async def _submit_task(self, audio_url: str, token: str) -> dict:
        # Parameters should be in query string, not JSON body
        params = {
            "appkey": self._app_key,
            "format": self._guess_format(audio_url),
            "sample_rate": 16000,
            "enable_punctuation_prediction": True,
            "enable_inverse_text_normalization": True,
            "audio_address": audio_url,  # Use audio_address instead of speech_file_url
        }

        logger.info("Aliyun NLS ASR submitting: url=%s", audio_url)

        try:
            headers = {
                "X-NLS-Token": token,
            }
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(_GATEWAY_URL, params=params, headers=headers)
            if response.status_code >= 400:
                reason = self._extract_http_error(response)
                raise BusinessError(
                    ErrorCode.ASR_SERVICE_FAILED,
                    reason=f"Submit task failed (HTTP {response.status_code}): {reason}",
                )
            data = response.json()
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Submit task failed: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Invalid response JSON: {exc}"
            ) from exc

        status = data.get("status")
        if status not in (None, 20000000):
            reason = data.get("message") or data.get("msg") or data
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(reason))

        return data

    @staticmethod
    def _extract_http_error(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                payload = response.json()
            except json.JSONDecodeError:
                return response.text or "Invalid JSON response"
            if isinstance(payload, dict):
                return str(
                    payload.get("message") or payload.get("msg") or payload.get("error") or payload
                )
            return str(payload)
        return response.text or "Empty response body"

    async def _poll_task(self, task_id: str, token: str) -> dict:
        poll_interval = max(1, self._poll_interval)
        max_wait = max(60, self._max_wait)
        deadline = time.time() + max_wait
        poll_count = 0

        while time.time() < deadline:
            poll_count += 1
            result = await self._query_task(task_id, token)
            status = result.get("status")

            if status == 20000000 and result.get("result"):
                logger.info("Aliyun NLS task %s completed after %s polls", task_id, poll_count)
                return result

            if status not in (None, 20000000):
                reason = result.get("message") or result.get("msg") or result
                raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(reason))

            await asyncio.sleep(poll_interval)

        raise BusinessError(ErrorCode.ASR_SERVICE_TIMEOUT)

    async def _query_task(self, task_id: str, token: str) -> dict:
        params = {
            "appkey": self._app_key,
            "token": token,
            "task_id": task_id,
        }

        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "X-NLS-Token": token,
            }
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(_GATEWAY_URL, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Query task failed: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Invalid response JSON: {exc}"
            ) from exc

        return data

    async def _parse_result(self, result: dict) -> list[TranscriptSegment]:
        text = result.get("result") if isinstance(result, dict) else None
        if isinstance(text, dict):
            text = text.get("text") or text.get("result")

        if isinstance(text, str) and text.strip():
            segments = self._parse_timestamped_text(text)
            if segments:
                return segments
            return [
                TranscriptSegment(
                    speaker_id=None,
                    start_time=0.0,
                    end_time=0.0,
                    content=text.strip(),
                    confidence=None,
                )
            ]

        raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="Empty ASR result")

    def _parse_timestamped_text(self, text: str) -> list[TranscriptSegment]:
        segments: list[TranscriptSegment] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            match = _TIMESTAMP_PATTERN.match(line)
            if not match:
                continue
            start_min, start_sec, end_min, end_sec, speaker, content = match.groups()
            start_time = int(start_min) * 60 + float(start_sec)
            end_time = int(end_min) * 60 + float(end_sec)
            segments.append(
                TranscriptSegment(
                    speaker_id=speaker,
                    start_time=start_time,
                    end_time=end_time,
                    content=content.strip(),
                    confidence=None,
                )
            )
        return segments

    def _guess_format(self, audio_url: str) -> str:
        suffix = ""
        if "." in audio_url:
            suffix = audio_url.rsplit(".", 1)[-1].split("?")[0].lower()
        return suffix if suffix in _SUPPORTED_FORMATS else "wav"

    async def get_task_status(self, task_id: str) -> str:
        if not task_id:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="task_id")

        token = await self._get_token()
        result = await self._query_task(task_id, token)
        status = result.get("status")

        if status == 20000000:
            return "success" if result.get("result") else "processing"
        if status is None:
            return "unknown"
        return "failed"

    async def cancel_task(self, task_id: str) -> bool:
        logger.warning("Cancel task not supported for Aliyun NLS: task_id=%s", task_id)
        return False

    @monitor("asr", "aliyun")
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
                results.append(segments)
            except BusinessError as exc:
                logger.error("Batch transcribe failed for %s: %s", audio_url, exc)
                results.append([])

        return results

    async def health_check(self) -> bool:
        try:
            await self._get_token()
            return True
        except Exception:
            return False

    def estimate_cost(self, duration_seconds: int) -> float:
        price_per_hour = 1.0
        duration_hours = duration_seconds / 3600.0
        return duration_hours * price_per_hour
