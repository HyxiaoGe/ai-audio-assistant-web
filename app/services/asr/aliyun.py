"""阿里云语音识别服务实现（DashScope SDK）"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from http import HTTPStatus
from typing import Awaitable, Callable, Optional

import httpx
from dashscope.audio.asr import Transcription

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.i18n.codes import ErrorCode
from app.services.asr.base import ASRService, TranscriptSegment
from app.services.config_utils import get_config_value

logger = logging.getLogger("app.services.asr.aliyun")


@register_service(
    "asr",
    "aliyun",
    metadata=ServiceMetadata(
        name="aliyun",
        service_type="asr",
        priority=15,
        description="阿里云 ASR 服务（Paraformer-v2）",
        display_name="阿里云语音识别",
        cost_per_million_tokens=1.0,  # 约 1.0 元/小时
        rate_limit=100,
    ),
)
class AliyunASRService(ASRService):
    """阿里云语音识别服务实现（基于 DashScope SDK）

    官方文档：https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-python-sdk
    """

    @property
    def provider(self) -> str:
        return "aliyun"

    def __init__(self, config: Optional[object] = None) -> None:
        access_key_id = get_config_value(config, "access_key_id", settings.ALIYUN_ACCESS_KEY_ID)
        access_key_secret = get_config_value(
            config, "access_key_secret", settings.ALIYUN_ACCESS_KEY_SECRET
        )

        if not access_key_id or not access_key_secret:
            raise RuntimeError("ALIYUN_ACCESS_KEY_ID or ALIYUN_ACCESS_KEY_SECRET is not set")

        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret

        # 设置 DashScope API Key（使用 access_key_id）
        import dashscope

        dashscope.api_key = access_key_id

    @monitor("asr", "aliyun")
    async def transcribe(
        self,
        audio_url: str,
        status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> list[TranscriptSegment]:
        """转写音频文件

        Args:
            audio_url: 音频文件 URL
            status_callback: 状态回调函数

        Returns:
            转写结果列表
        """
        if not audio_url:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="audio_url")

        if status_callback:
            await status_callback("asr_submitting")

        task_id = await asyncio.to_thread(self._create_task, audio_url)

        if status_callback:
            await status_callback("asr_polling")

        result = await self._poll_task(task_id)
        return await self._parse_result(result)

    def _create_task(self, audio_url: str) -> str:
        """创建语音识别任务

        Args:
            audio_url: 音频文件 URL

        Returns:
            任务 ID
        """
        logger.info(f"Aliyun ASR submitting with audio_url: {audio_url}")

        try:
            task_response = Transcription.async_call(
                model="paraformer-v2",
                file_urls=[audio_url],
                language_hints=["zh", "en"],  # 支持中英文
            )

            if task_response.status_code != HTTPStatus.OK:
                raise BusinessError(
                    ErrorCode.ASR_SERVICE_FAILED,
                    reason=f"Failed to create task: {task_response.message}",
                )

            task_id = task_response.output.task_id
            if not task_id:
                raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="missing task id")

            logger.info(f"Aliyun ASR task created successfully: TaskId={task_id}")
            return task_id

        except Exception as exc:
            if isinstance(exc, BusinessError):
                raise
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Create task failed: {exc}"
            ) from exc

    async def _poll_task(self, task_id: str) -> dict:
        """轮询任务状态直到完成

        Args:
            task_id: 任务 ID

        Returns:
            任务结果
        """
        poll_interval = 3  # 3秒轮询一次
        max_wait = 600  # 最长等待10分钟

        logger.info(
            f"Aliyun ASR polling task {task_id}: interval={poll_interval}s, max_wait={max_wait}s"
        )

        deadline = time.time() + max_wait
        poll_count = 0

        while time.time() < deadline:
            poll_count += 1
            result = await asyncio.to_thread(self._fetch_task, task_id)

            task_status = result.get("task_status")
            logger.info(f"Aliyun ASR poll #{poll_count} for task {task_id}: status={task_status}")

            if task_status == "SUCCEEDED":
                logger.info(
                    f"Aliyun ASR task {task_id} completed successfully after {poll_count} polls"
                )
                return result

            if task_status == "FAILED":
                error_msg = result.get("message", "ASR task failed")
                logger.warning(f"Aliyun ASR task {task_id} failed: {error_msg}")
                raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(error_msg))

            await asyncio.sleep(poll_interval)

        logger.error(f"Aliyun ASR task {task_id} timeout after {poll_count} polls ({max_wait}s)")
        raise BusinessError(ErrorCode.ASR_SERVICE_TIMEOUT)

    def _fetch_task(self, task_id: str) -> dict:
        """获取任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态字典
        """
        try:
            response = Transcription.fetch(task=task_id)

            if response.status_code != HTTPStatus.OK:
                raise BusinessError(
                    ErrorCode.ASR_SERVICE_FAILED, reason=f"Fetch task failed: {response.message}"
                )

            return {
                "task_id": response.output.task_id,
                "task_status": response.output.task_status,
                "submit_time": response.output.submit_time,
                "run_time": response.output.run_time,
                "results": response.output.results if hasattr(response.output, "results") else [],
                "message": response.message,
            }

        except Exception as exc:
            if isinstance(exc, BusinessError):
                raise
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Fetch task failed: {exc}"
            ) from exc

    async def _parse_result(self, result: dict) -> list[TranscriptSegment]:
        """解析识别结果

        Args:
            result: 任务结果

        Returns:
            转写片段列表
        """
        results = result.get("results", [])
        if not results:
            raise BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="No results in response")

        # 获取第一个文件的结果（因为我们只提交了一个文件）
        file_result = results[0]
        transcription_url = file_result.get("transcription_url")

        if not transcription_url:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason="Missing transcription_url in result"
            )

        # 下载转写结果 JSON
        transcription_data = await self._download_transcription(transcription_url)

        # 解析转写结果
        segments: list[TranscriptSegment] = []

        # Paraformer 结果格式：包含 transcription 和可选的 sentences
        sentences = transcription_data.get("sentences", [])

        if not sentences:
            # 如果没有句子级别的结果，尝试使用整体文本
            full_text = transcription_data.get("transcription", "")
            if full_text:
                segments.append(
                    TranscriptSegment(
                        speaker_id=None,
                        start_time=0.0,
                        end_time=0.0,
                        content=full_text,
                        confidence=None,
                    )
                )
        else:
            # 解析句子级别的结果
            for sentence in sentences:
                speaker_id = sentence.get("speaker_id")
                begin_time = sentence.get("begin_time", 0)  # 毫秒
                end_time = sentence.get("end_time", 0)  # 毫秒
                text = sentence.get("text", "")

                segments.append(
                    TranscriptSegment(
                        speaker_id=str(speaker_id) if speaker_id is not None else None,
                        start_time=float(begin_time) / 1000.0,  # 转换为秒
                        end_time=float(end_time) / 1000.0,  # 转换为秒
                        content=text,
                        confidence=None,  # Paraformer 没有置信度字段
                    )
                )

        return segments

    async def _download_transcription(self, url: str) -> dict:
        """下载转写结果 JSON

        Args:
            url: 转写结果 URL

        Returns:
            转写结果字典
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Download transcription failed: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise BusinessError(
                ErrorCode.ASR_SERVICE_FAILED, reason=f"Invalid transcription JSON: {exc}"
            ) from exc

    async def get_task_status(self, task_id: str) -> str:
        """查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态字符串：'pending', 'processing', 'success', 'failed'
        """
        if not task_id:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="task_id")

        result = await asyncio.to_thread(self._fetch_task, task_id)
        task_status = result.get("task_status")

        # 阿里云 DashScope 状态映射
        status_map = {
            "PENDING": "pending",
            "RUNNING": "processing",
            "SUCCEEDED": "success",
            "FAILED": "failed",
        }

        return status_map.get(task_status, "unknown")

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务

        阿里云 DashScope 暂不支持取消任务功能

        Args:
            task_id: 任务 ID

        Returns:
            False（暂不支持）
        """
        logger.warning(f"Cancel task not supported for Aliyun ASR: task_id={task_id}")
        return False

    @monitor("asr", "aliyun")
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
        """健康检查：验证阿里云配置是否正确

        Returns:
            True 如果服务健康，否则 False
        """
        try:
            # 检查必要的配置是否存在
            if not self._access_key_id or not self._access_key_secret:
                return False
            return True
        except Exception:
            return False

    def estimate_cost(self, duration_seconds: int) -> float:
        """估算成本（人民币元）

        阿里云语音识别定价（2024 年参考）：
        - 免费额度: 每月 3 小时
        - 收费标准: ¥1.0/小时

        Args:
            duration_seconds: 音频时长（秒）

        Returns:
            估算成本（人民币元）
        """
        # 价格（元/小时）
        price_per_hour = 1.0

        # 转换为小时
        duration_hours = duration_seconds / 3600.0

        # 简化估算，不考虑免费额度
        cost = duration_hours * price_per_hour

        return cost
