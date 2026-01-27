"""内容大纲图生成模块

使用 AI 图像生成模型（如 Gemini 3 Pro Image）直接生成大纲图片。
"""

import logging
import tempfile
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.smart_factory import SmartFactory
from app.models.summary import Summary
from app.prompts.manager import get_prompt_manager
from app.services.asr.base import TranscriptSegment
from app.utils.transcript_processor import TranscriptProcessor

logger = logging.getLogger(__name__)

# 默认的图像生成模型
DEFAULT_IMAGE_MODEL = "google/gemini-3-pro-image-preview"
DEFAULT_PROVIDER = "openrouter"


async def upload_outline_image(
    user_id: str,
    task_id: str,
    image_data: bytes,
    image_format: str = "png",
) -> str:
    """上传大纲图片到存储服务

    Args:
        user_id: 用户 ID
        task_id: 任务 ID
        image_data: 图片二进制数据
        image_format: 图片格式 (png/jpg/webp)

    Returns:
        str: 存储对象 key
    """
    # 生成唯一文件名
    outline_id = str(uuid.uuid4())[:8]
    object_key = f"visuals/{user_id}/{task_id}/outline_{outline_id}.{image_format}"

    # 获取存储服务
    storage = await SmartFactory.get_service("storage")

    # 写入临时文件
    with tempfile.NamedTemporaryFile(suffix=f".{image_format}", delete=False) as tmp_file:
        tmp_file.write(image_data)
        tmp_path = tmp_file.name

    try:
        # 上传到存储（同步方法）
        content_type = f"image/{image_format}"
        storage.upload_file(object_name=object_key, file_path=tmp_path, content_type=content_type)
        logger.info(f"Uploaded outline image to storage: {object_key}")
        return object_key

    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def generate_outline_summary(
    task_id: str,
    segments: list[TranscriptSegment],
    content_style: str,
    session: AsyncSession,
    user_id: str,
    provider: str | None = None,
    model_id: str | None = None,
    image_format: str = "png",
) -> Summary:
    """生成内容大纲图（AI 图像生成）

    Args:
        task_id: 任务 ID
        segments: 转写片段列表
        content_style: 内容风格 (meeting/lecture/podcast/video/general)
        session: 数据库会话
        user_id: 用户 ID
        provider: LLM provider（默认 openrouter）
        model_id: 图像生成模型 ID（默认 google/gemini-3-pro-image-preview）
        image_format: 图片格式 (png)

    Returns:
        Summary: 生成的大纲摘要对象
    """
    logger.info(
        f"Task {task_id}: Generating outline image - "
        f"style: {content_style}, provider: {provider}, model: {model_id}"
    )

    # ===== Step 1: 评估转写质量 =====
    quality = TranscriptProcessor.assess_quality(segments)
    quality_notice = TranscriptProcessor.get_quality_notice(quality)

    logger.info(
        f"Task {task_id}: Quality assessment - score: {quality.quality_score}, "
        f"confidence: {quality.avg_confidence:.2f}"
    )

    # ===== Step 2: 预处理转写文本 =====
    # 为了让 AI 更好地理解内容，先生成摘要再生成图片
    preprocessed_text = TranscriptProcessor.preprocess(
        segments, filter_filler_words=True, merge_same_speaker=True
    )

    # 限制文本长度（AI 图像生成的 prompt 不宜过长）
    max_chars = 3000
    if len(preprocessed_text) > max_chars:
        preprocessed_text = preprocessed_text[:max_chars] + "..."

    logger.info(f"Task {task_id}: Preprocessed text length: {len(preprocessed_text)} chars")

    # ===== Step 3: 获取 prompt 配置 =====
    prompt_manager = get_prompt_manager()
    prompt_config = prompt_manager.get_prompt(
        category="visual",
        prompt_type="outline",
        locale="zh-CN",
        variables={"transcript": preprocessed_text, "quality_notice": quality_notice},
        content_style=content_style,
    )

    # ===== Step 4: 获取图像生成服务 =====
    # 使用指定的 provider/model 或默认配置
    actual_provider = provider or DEFAULT_PROVIDER
    actual_model = model_id or DEFAULT_IMAGE_MODEL

    logger.info(
        f"Task {task_id}: Using image model - provider: {actual_provider}, model: {actual_model}"
    )

    # 获取 OpenRouter 服务（支持图像生成）
    llm_service = await SmartFactory.get_service(
        "llm", provider=actual_provider, model_id=actual_model
    )

    # ===== Step 5: 生成大纲图片 =====
    logger.info(f"Task {task_id}: Calling AI to generate outline image")

    # 获取图像配置
    visual_config = prompt_manager.get_visual_config("outline")
    image_config = visual_config.get("image_config", {})
    aspect_ratio = image_config.get("aspect_ratio", "3:4")
    image_size = image_config.get("image_size", "2K")

    # 调用图像生成 API
    image_data = await llm_service.generate_image(
        prompt=prompt_config["user_prompt"],
        system_message=prompt_config.get("system"),
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        temperature=prompt_config.get("model_params", {}).get("temperature", 0.7),
        max_tokens=prompt_config.get("model_params", {}).get("max_tokens", 4096),
    )

    logger.info(f"Task {task_id}: Image generated, size: {len(image_data)} bytes")

    # ===== Step 6: 上传图片到存储 =====
    image_key = await upload_outline_image(
        user_id=user_id,
        task_id=task_id,
        image_data=image_data,
        image_format=image_format,
    )
    logger.info(f"Task {task_id}: Outline image uploaded to {image_key}")

    # ===== Step 7: 创建 Summary 记录 =====
    summary = Summary(
        task_id=task_id,
        summary_type="visual_outline",
        version=1,
        is_active=True,
        content="[AI 生成的内容大纲图]",  # outline 类型主要是图片，此处为占位描述
        model_used=actual_model,
        prompt_version="v1.5.0",
        token_count=0,
        visual_format="image",  # 区别于 mermaid
        visual_content=None,
        image_key=image_key,
        image_format=image_format,
    )

    session.add(summary)

    logger.info(
        f"Task {task_id}: Outline summary generated successfully - " f"image_key: {image_key}"
    )

    return summary
