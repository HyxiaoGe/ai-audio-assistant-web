from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable, Optional

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
from app.services.asr.base import ASRService, TranscriptSegment
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
    ),
)
class TencentASRService(ASRService):
    # 腾讯云ASR base64方式限制为5MB，URL方式限制为1GB
    _BASE64_MAX_BYTES = 5 * 1024 * 1024

    @property
    def provider(self) -> str:
        return "tencent"

    def __init__(self, config: Optional[object] = None) -> None:
        secret_id = get_config_value(config, "secret_id", settings.TENCENT_SECRET_ID)
        secret_key = get_config_value(config, "secret_key", settings.TENCENT_SECRET_KEY)
        region = get_config_value(config, "region", settings.TENCENT_REGION)
        engine_model_type = get_config_value(
            config, "engine_model_type", settings.TENCENT_ASR_ENGINE_MODEL_TYPE
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
        self._engine_model_type = engine_model_type
        self._channel_num = channel_num
        self._res_text_format = res_text_format
        self._speaker_dia = speaker_dia
        self._speaker_number = speaker_number
        self._poll_interval = poll_interval
        self._max_wait = max_wait
        self._source_type = source_type

    @monitor("asr", "tencent")
    async def transcribe(
        self, audio_url: str, status_callback: Optional[Callable[[str], Awaitable[None]]] = None
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
        http_profile.endpoint = "asr.tencentcloudapi.com"
        client_profile = ClientProfile(httpProfile=http_profile)
        client_profile.signMethod = "TC3-HMAC-SHA256"
        return asr_client.AsrClient(cred, self._region, client_profile)

    def _create_task(self, audio_url: str) -> str:
        engine_model_type = self._engine_model_type
        channel_num = self._channel_num
        source_type = self._source_type
        res_text_format = self._res_text_format
        speaker_dia = self._speaker_dia
        speaker_number = self._speaker_number

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
            if use_detail:
                speaker_id = item.get("SpeakerId")
                start_ms = item.get("StartMs")
                end_ms = item.get("EndMs")
                text_value = item.get("FinalSentence")
                confidence = None
                start_time = float(start_ms) / 1000.0 if start_ms is not None else 0.0
                end_time = float(end_ms) / 1000.0 if end_ms is not None else 0.0
            else:
                speaker_id = item.get("SpeakerId")
                start_time = float(item.get("StartTime", 0.0))
                end_time = float(item.get("EndTime", 0.0))
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
                )
            )
        if len(segments) == 1:
            content = segments[0].content
            if isinstance(content, str):
                timestamped = self._parse_timestamped_text(content)
                if timestamped:
                    return timestamped
        return segments

    def _parse_timestamped_text(self, content: str) -> list[TranscriptSegment]:
        pattern = re.compile(
            r"\[(\d+):(\d+(?:\.\d+)?),(\d+):(\d+(?:\.\d+)?),(\d+)\]\s*(.*)"
        )
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

    def estimate_cost(self, duration_seconds: int) -> float:
        """估算成本（人民币元）

        腾讯云录音文件识别定价（2024 年参考）：
        - 免费额度: 每月 30 小时
        - 收费标准: ¥1.2/小时

        Args:
            duration_seconds: 音频时长（秒）

        Returns:
            估算成本（人民币元）
        """
        # 价格（元/小时）
        price_per_hour = 1.2

        # 转换为小时
        duration_hours = duration_seconds / 3600.0

        # 简化估算，不考虑免费额度
        cost = duration_hours * price_per_hour

        return cost
