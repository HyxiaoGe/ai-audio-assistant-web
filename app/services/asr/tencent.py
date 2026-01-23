from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import re
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import urlencode, urlparse

import httpx
from tencentcloud.asr.v20190614 import asr_client, models
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.i18n.codes import ErrorCode
from app.services.asr.base import ASRService, TranscriptSegment, WordTimestamp
from app.services.config_utils import get_config_value

logger = logging.getLogger("app.services.asr.tencent")


@register_service(
    "asr",
    "tencent",
    metadata=ServiceMetadata(
        name="tencent",
        service_type="asr",
        priority=10,
        description="腾讯云 ASR 服务",
        display_name="腾讯云语音识别",
        cost_per_million_tokens=1.25,  # 标准版 ¥1.25/小时，极速版 ¥3.10/小时
        rate_limit=100,
    ),
)
class TencentASRService(ASRService):
    # 腾讯云ASR base64方式限制为5MB，URL方式限制为1GB
    _BASE64_MAX_BYTES = 5 * 1024 * 1024
    _FLASH_MAX_BYTES = 100 * 1024 * 1024
    _FLASH_HOST = "asr.cloud.tencent.com"

    @property
    def provider(self) -> str:
        return "tencent"

    def __init__(self, config: Optional[object] = None) -> None:
        app_id = get_config_value(config, "app_id", settings.TENCENT_ASR_APP_ID)
        secret_id = get_config_value(config, "secret_id", settings.TENCENT_SECRET_ID)
        secret_key = get_config_value(config, "secret_key", settings.TENCENT_SECRET_KEY)
        region = get_config_value(config, "region", settings.TENCENT_REGION)
        engine_model_type = get_config_value(
            config, "engine_model_type", settings.TENCENT_ASR_ENGINE_MODEL_TYPE
        )
        engine_model_type_file_fast = get_config_value(
            config,
            "engine_model_type_file_fast",
            settings.TENCENT_ASR_ENGINE_MODEL_TYPE_FILE_FAST,
        )
        channel_num = get_config_value(config, "channel_num", settings.TENCENT_ASR_CHANNEL_NUM)
        res_text_format = get_config_value(
            config, "res_text_format", settings.TENCENT_ASR_RES_TEXT_FORMAT
        )
        speaker_dia = get_config_value(config, "speaker_dia", settings.TENCENT_ASR_SPEAKER_DIA)
        speaker_number = get_config_value(
            config, "speaker_number", settings.TENCENT_ASR_SPEAKER_NUMBER
        )
        poll_interval = get_config_value(
            config, "poll_interval", settings.TENCENT_ASR_POLL_INTERVAL
        )
        max_wait = get_config_value(config, "max_wait", settings.TENCENT_ASR_MAX_WAIT_SECONDS)
        source_type = get_config_value(config, "source_type", settings.TENCENT_ASR_SOURCE_TYPE)
        if not secret_id or not secret_key or not region:
            raise RuntimeError("TENCENT_SECRET_ID/TENCENT_SECRET_KEY/TENCENT_REGION is not set")
        if (
            not engine_model_type
            or channel_num is None
            or res_text_format is None
            or speaker_dia is None
            or speaker_number is None
            or poll_interval is None
            or max_wait is None
            or source_type is None
        ):
            raise RuntimeError("Tencent ASR settings are not set")
        self._secret_id = secret_id
        self._secret_key = secret_key
        self._region = region
        self._app_id = self._normalize_app_id(app_id)
        self._engine_model_type = engine_model_type
        self._engine_model_type_file_fast = engine_model_type_file_fast
        self._channel_num = channel_num
        self._res_text_format = res_text_format
        self._speaker_dia = speaker_dia
        self._speaker_number = speaker_number
        self._poll_interval = poll_interval
        self._max_wait = max_wait
        self._source_type = source_type

    def _resolve_speaker_settings(
        self,
        enable_speaker_diarization: Optional[bool],
    ) -> tuple[int, int]:
        speaker_dia = self._speaker_dia
        speaker_number = self._speaker_number
        if enable_speaker_diarization is False:
            return 0, 0
        if enable_speaker_diarization is True and speaker_dia in (0, None):
            return 1, max(speaker_number or 0, 0)
        return speaker_dia, speaker_number

    @monitor("asr", "tencent")
    async def transcribe(
        self,
        audio_url: str,
        status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        *,
        enable_speaker_diarization: Optional[bool] = None,
        asr_variant: Optional[str] = None,
    ) -> list[TranscriptSegment]:
        if not audio_url:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="audio_url")
        if status_callback:
            await status_callback("asr_submitting")
        if asr_variant == "file_fast":
            segments = await self._transcribe_flash(
                audio_url,
                enable_speaker_diarization=enable_speaker_diarization,
            )
            if status_callback:
                await status_callback("asr_completed")
            return segments
        task_id = await asyncio.to_thread(
            self._create_task,
            audio_url,
            enable_speaker_diarization=enable_speaker_diarization,
            asr_variant=asr_variant,
        )
        if status_callback:
            await status_callback("asr_polling")
        result = await self._poll_task(task_id)
        return self._parse_result(result)

    def _normalize_app_id(self, raw: Optional[str]) -> Optional[str]:
        if raw:
            return str(raw).strip()
        bucket = settings.COS_BUCKET
        if isinstance(bucket, str):
            match = re.search(r"-(\d+)$", bucket.strip())
            if match:
                return match.group(1)
        return None

    def _create_client(self) -> asr_client.AsrClient:
        cred = credential.Credential(self._secret_id, self._secret_key)
        http_profile = HttpProfile()
        http_profile.endpoint = "asr.tencentcloudapi.com"
        client_profile = ClientProfile(httpProfile=http_profile)
        client_profile.signMethod = "TC3-HMAC-SHA256"
        return asr_client.AsrClient(cred, self._region, client_profile)

    def _create_task(
        self,
        audio_url: str,
        *,
        enable_speaker_diarization: Optional[bool] = None,
        asr_variant: Optional[str] = None,
    ) -> str:
        if asr_variant == "file_fast" and self._engine_model_type_file_fast:
            engine_model_type = self._engine_model_type_file_fast
        else:
            engine_model_type = self._engine_model_type
        channel_num = self._channel_num
        source_type = self._source_type
        res_text_format = self._res_text_format
        speaker_dia, speaker_number = self._resolve_speaker_settings(enable_speaker_diarization)

        logger.info(f"ASR submitting with audio_url: {audio_url}")

        # 创建请求对象（与测试脚本完全一致的方式）
        request = models.CreateRecTaskRequest()
        request.EngineModelType = engine_model_type
        request.ChannelNum = channel_num
        request.ResTextFormat = res_text_format
        request.SourceType = source_type
        request.Url = audio_url
        request.SpeakerDiarization = speaker_dia
        request.SpeakerNumber = speaker_number

        logger.info(
            "ASR request parameters: EngineModelType=%s, ChannelNum=%s, "
            "ResTextFormat=%s, SourceType=%s, SpeakerDiarization=%s, SpeakerNumber=%s",
            engine_model_type,
            channel_num,
            res_text_format,
            source_type,
            speaker_dia,
            speaker_number,
        )

        try:
            client = self._create_client()
            response = client.CreateRecTask(request)
        except TencentCloudSDKException as exc:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(exc)) from exc
        task_id = response.Data.TaskId if response and response.Data else None
        if not task_id:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="missing task id")
        logger.info(f"ASR task created successfully: TaskId={task_id}")
        return str(task_id)

    async def _transcribe_flash(
        self,
        audio_url: str,
        *,
        enable_speaker_diarization: Optional[bool] = None,
    ) -> list[TranscriptSegment]:
        if not self._app_id:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED,
                reason="missing tencent app id for flash asr",
            )
        audio_bytes = await self._download_flash_audio(audio_url)
        voice_format = self._guess_voice_format(audio_url)
        timestamp = int(time.time())
        speaker_dia, _speaker_number = self._resolve_speaker_settings(enable_speaker_diarization)
        word_info = 0 if self._res_text_format is None else int(self._res_text_format)
        params = {
            "secretid": self._secret_id,
            "timestamp": timestamp,
            "engine_type": self._engine_model_type_file_fast or self._engine_model_type,
            "voice_format": voice_format,
            "word_info": word_info,
            "speaker_diarization": int(speaker_dia),
            "first_channel_only": 1,
        }
        signature = self._sign_flash(params)
        query = urlencode(sorted(params.items()), doseq=True)
        url = f"https://{self._FLASH_HOST}/asr/flash/v1/{self._app_id}?{query}"
        headers = {
            "Authorization": signature,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(audio_bytes)),
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0)) as client:
                response = await client.post(url, content=audio_bytes, headers=headers)
        except httpx.HTTPError as exc:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(exc)) from exc
        if response.status_code >= 400:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED,
                reason=f"flash api http {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason="invalid flash response"
            ) from exc
        code = payload.get("code")
        if code != 0:
            message = payload.get("message") or "flash api error"
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(message))
        return self._parse_flash_result(payload)

    async def _download_flash_audio(self, audio_url: str) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0)) as client:
                async with client.stream("GET", audio_url) as response:
                    response.raise_for_status()
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self._FLASH_MAX_BYTES:
                        raise BusinessError(
                            ErrorCode.ASR_SERVICE_FAILED,
                            reason="flash audio too large",
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self._FLASH_MAX_BYTES:
                            raise BusinessError(
                                ErrorCode.ASR_SERVICE_FAILED,
                                reason="flash audio too large",
                            )
                        chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(exc)) from exc
        return b"".join(chunks)

    def _guess_voice_format(self, audio_url: str) -> str:
        parsed = urlparse(audio_url)
        suffix = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
        if suffix:
            return suffix
        mime_type, _ = mimetypes.guess_type(parsed.path)
        if mime_type:
            if mime_type.endswith("wav"):
                return "wav"
            if mime_type.endswith("mpeg"):
                return "mp3"
            if mime_type.endswith("aac"):
                return "aac"
            if mime_type.endswith("amr"):
                return "amr"
            if mime_type.endswith("ogg"):
                return "ogg-opus"
        raise BusinessError(ErrorCode.UNSUPPORTED_FILE_FORMAT)

    def _sign_flash(self, params: dict[str, object]) -> str:
        query = urlencode(sorted(params.items()), doseq=True)
        sign_source = f"POST{self._FLASH_HOST}/asr/flash/v1/{self._app_id}?{query}"
        digest = hmac.new(
            self._secret_key.encode("utf-8"),
            sign_source.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _parse_flash_result(self, payload: dict[str, object]) -> list[TranscriptSegment]:
        results = payload.get("flash_result")
        if not isinstance(results, list):
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="missing flash_result")
        segments: list[TranscriptSegment] = []
        for channel in results:
            if not isinstance(channel, dict):
                continue
            sentence_list = channel.get("sentence_list")
            if not isinstance(sentence_list, list):
                text = channel.get("text")
                if isinstance(text, str) and text:
                    segments.append(
                        TranscriptSegment(
                            speaker_id=None,
                            start_time=0.0,
                            end_time=0.0,
                            content=text,
                            confidence=None,
                        )
                    )
                continue
            for sentence in sentence_list:
                if not isinstance(sentence, dict):
                    continue
                text = sentence.get("text")
                start_ms = sentence.get("start_time")
                end_ms = sentence.get("end_time")
                speaker_id = sentence.get("speaker_id")
                words = self._parse_flash_words(sentence.get("word_list"))
                start_time = self._ms_to_seconds(start_ms) or 0.0
                end_time = self._ms_to_seconds(end_ms) or 0.0
                segments.append(
                    TranscriptSegment(
                        speaker_id=str(speaker_id) if speaker_id is not None else None,
                        start_time=start_time,
                        end_time=end_time,
                        content=str(text) if text is not None else "",
                        confidence=None,
                        words=words,
                    )
                )
        return segments

    @staticmethod
    def _parse_flash_words(raw: object) -> Optional[list[WordTimestamp]]:
        if not isinstance(raw, list):
            return None
        words: list[WordTimestamp] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            word_value = entry.get("word")
            if not isinstance(word_value, str):
                continue
            start_ms = entry.get("start_time")
            end_ms = entry.get("end_time")
            start_time = TencentASRService._ms_to_seconds(start_ms)
            end_time = TencentASRService._ms_to_seconds(end_ms)
            if start_time is None or end_time is None:
                continue
            words.append(
                WordTimestamp(
                    word=word_value,
                    start_time=start_time,
                    end_time=end_time,
                    confidence=None,
                )
            )
        return words or None

    @staticmethod
    def _ms_to_seconds(value: object) -> Optional[float]:
        numeric = TencentASRService._to_float(value)
        if numeric is None:
            return None
        return numeric / 1000.0

    @staticmethod
    def _to_float(value: object) -> Optional[float]:
        if isinstance(value, (int, float, str)):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return None

    async def _poll_task(self, task_id: str) -> dict[str, object]:
        poll_interval = self._poll_interval
        max_wait = self._max_wait

        logger.info(f"ASR polling task {task_id}: interval={poll_interval}s, max_wait={max_wait}s")
        deadline = time.time() + max_wait
        poll_count = 0
        while time.time() < deadline:
            poll_count += 1
            result = await asyncio.to_thread(self._describe_task, task_id)
            status = result.get("Status")
            status_str = result.get("StatusStr", "unknown")
            logger.info(
                f"ASR poll #{poll_count} for task {task_id}: status={status} ({status_str})"
            )
            if status == 2:
                logger.info(f"ASR task {task_id} completed successfully after {poll_count} polls")
                return result
            if status == 3:
                reason = result.get("ErrorMsg") or "ASR failed"
                logger.warning(f"ASR task {task_id} failed. Full response: {result}")
                raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(reason))
            await asyncio.sleep(poll_interval)
        logger.error(f"ASR task {task_id} timeout after {poll_count} polls ({max_wait}s)")
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
        # 优先使用 ResultDetail（结构化字段），否则回退到 Result
        result_detail = payload.get("ResultDetail")
        use_detail = result_detail is not None
        if result_detail is None:
            result_detail = payload.get("Result")
            if result_detail is None:
                raise BusinessError(
                    ErrorCode.ASR_SERVICE_FAILED, reason="missing ResultDetail/Result"
                )
        if isinstance(result_detail, str):
            try:
                parsed = json.loads(result_detail)
            except json.JSONDecodeError:
                timestamped = self._parse_timestamped_text(result_detail)
                if timestamped:
                    return timestamped
                return [
                    TranscriptSegment(
                        speaker_id=None,
                        start_time=0.0,
                        end_time=0.0,
                        content=result_detail,
                        confidence=None,
                    )
                ]
            if isinstance(parsed, list):
                result_detail = parsed
            elif isinstance(parsed, dict):
                result_detail = [parsed]
            else:
                raise BusinessError(
                    ErrorCode.ASR_SERVICE_FAILED, reason="ResultDetail/Result is not a list"
                )
        elif isinstance(result_detail, dict):
            result_detail = [result_detail]
        if not isinstance(result_detail, list):
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason="ResultDetail/Result is not a list"
            )

        segments: list[TranscriptSegment] = []
        for item in result_detail:
            if not isinstance(item, dict):
                continue
            use_detail = use_detail or any(
                key in item for key in ("FinalSentence", "StartMs", "EndMs")
            )
            words = self._parse_words(item)
            if use_detail:
                speaker_id = item.get("SpeakerId")
                start_ms = item.get("StartMs")
                end_ms = item.get("EndMs")
                text_value = item.get("FinalSentence")
                confidence = None
                start_time = self._ms_to_seconds(start_ms) or 0.0
                end_time = self._ms_to_seconds(end_ms) or 0.0
            else:
                speaker_id = item.get("SpeakerId")
                start_time = self._to_seconds(item.get("StartTime")) or 0.0
                end_time = self._to_seconds(item.get("EndTime")) or 0.0
                text_value = item.get("Text")
                confidence = item.get("Confidence")

            if isinstance(text_value, str):
                timestamped = self._parse_timestamped_text(text_value)
                if timestamped:
                    segments.extend(timestamped)
                    continue

            segments.append(
                TranscriptSegment(
                    speaker_id=str(speaker_id) if speaker_id is not None else None,
                    start_time=start_time,
                    end_time=end_time,
                    content=str(text_value) if text_value is not None else "",
                    confidence=float(confidence) if confidence is not None else None,
                    words=words,
                )
            )
        if len(segments) == 1:
            content = segments[0].content
            if isinstance(content, str):
                timestamped = self._parse_timestamped_text(content)
                if timestamped:
                    return timestamped
        return segments

    def _parse_words(self, item: dict[str, object]) -> Optional[list[WordTimestamp]]:
        words_raw = (
            item.get("SentenceWords")
            or item.get("Words")
            or item.get("WordList")
            or item.get("WordSet")
        )
        if not isinstance(words_raw, list):
            return None
        words: list[WordTimestamp] = []
        for entry in words_raw:
            if not isinstance(entry, dict):
                continue
            word_value = (
                entry.get("Word") or entry.get("WordStr") or entry.get("Text") or entry.get("Value")
            )
            if not isinstance(word_value, str):
                continue
            base_start_ms = self._to_float(item.get("StartMs"))
            offset_start = self._to_float(
                entry.get("OffsetStartMs") or entry.get("OffsetStartTime")
            )
            offset_end = self._to_float(entry.get("OffsetEndMs") or entry.get("OffsetEndTime"))
            start_val = entry.get("StartTime") or entry.get("StartMs") or entry.get("BeginTime")
            end_val = entry.get("EndTime") or entry.get("EndMs") or entry.get("FinishTime")
            if base_start_ms is not None and offset_start is not None and offset_end is not None:
                start_time = (base_start_ms + offset_start) / 1000.0
                end_time = (base_start_ms + offset_end) / 1000.0
            else:
                start_time = self._to_seconds(start_val)
                end_time = self._to_seconds(end_val)
            if start_time is None or end_time is None:
                continue
            confidence = entry.get("Confidence") or entry.get("WordConfidence")
            words.append(
                WordTimestamp(
                    word=word_value,
                    start_time=start_time,
                    end_time=end_time,
                    confidence=float(confidence) if confidence is not None else None,
                )
            )
        return words or None

    @staticmethod
    def _to_seconds(value: object) -> Optional[float]:
        numeric = TencentASRService._to_float(value)
        if numeric is None:
            return None
        if numeric > 1000:
            return numeric / 1000.0
        return numeric

    def _parse_timestamped_text(self, content: str) -> list[TranscriptSegment]:
        pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?),(\d+):(\d+(?:\.\d+)?),(\d+)\]\s*(.*)")
        segments: list[TranscriptSegment] = []
        for match in pattern.finditer(content):
            start_min, start_sec, end_min, end_sec, speaker_id, text = match.groups()
            start_time = float(start_min) * 60 + float(start_sec)
            end_time = float(end_min) * 60 + float(end_sec)
            segments.append(
                TranscriptSegment(
                    speaker_id=str(speaker_id),
                    start_time=start_time,
                    end_time=end_time,
                    content=text.strip(),
                    confidence=None,
                )
            )
        return segments

    async def get_task_status(self, task_id: str) -> str:
        """查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态字符串：'pending', 'processing', 'success', 'failed'
        """
        if not task_id:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="task_id")

        result = await asyncio.to_thread(self._describe_task, task_id)
        status = result.get("Status")

        # 腾讯云 ASR 状态映射：
        # 0: 任务等待
        # 1: 任务执行中
        # 2: 任务成功
        # 3: 任务失败
        status_map = {
            0: "pending",
            1: "processing",
            2: "success",
            3: "failed",
        }

        status_code = status if isinstance(status, int) else None
        return status_map.get(status_code, "unknown")

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务

        腾讯云 ASR 录音文件识别暂不支持取消任务功能

        Args:
            task_id: 任务 ID

        Returns:
            False（暂不支持）
        """
        logger.warning(f"Cancel task not supported for Tencent ASR: task_id={task_id}")
        return False

    @monitor("asr", "tencent")
    async def batch_transcribe(
        self,
        audio_urls: list[str],
        status_callback: Optional[Callable[[str, int, int], Awaitable[None]]] = None,
    ) -> list[list[TranscriptSegment]]:
        """批量转写音频

        Args:
            audio_urls: 音频 URL 列表
            status_callback: 状态回调函数 (当前文件索引, 总文件数)

        Returns:
            转写结果列表
        """
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
            except BusinessError as e:
                logger.error(f"Batch transcribe failed for {audio_url}: {e}")
                # 失败的文件返回空列表
                results.append([])

        return results

    async def health_check(self) -> bool:
        """健康检查：验证腾讯云配置是否正确

        Returns:
            True 如果服务健康，否则 False
        """
        try:
            # 检查必要的配置是否存在
            if not self._secret_id or not self._secret_key or not self._region:
                return False
            return True
        except Exception:
            return False

    def estimate_cost(self, duration_seconds: int, variant: str = "file") -> float:
        """估算成本（人民币元）

        腾讯云录音文件识别定价（2025 年参考）：
        - 录音文件识别（标准版）: ¥1.25/小时
        - 录音文件识别（极速版）: ¥3.10/小时

        免费资源包（每月自动发放，当月有效）：
        - 录音文件识别极速版: 5 小时/月
        - 实时语音识别: 5 小时/月

        注意：免费额度需通过配额管理系统单独配置，此处仅计算按量付费成本。

        Args:
            duration_seconds: 音频时长（秒）
            variant: 服务变体 (file=标准版, file_fast=极速版)

        Returns:
            估算成本（人民币元）
        """
        # 根据变体选择价格
        if variant == "file_fast":
            price_per_hour = 3.10  # 极速版
        else:
            price_per_hour = 1.25  # 标准版

        duration_hours = duration_seconds / 3600.0
        return duration_hours * price_per_hour
