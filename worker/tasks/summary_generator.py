"""摘要生成辅助模块

提供质量感知的摘要生成功能，包括：
- 转写质量评估
- 文本预处理
- 章节划分
- 多类型摘要生成
"""

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.smart_factory import SmartFactory
from app.models.summary import Summary
from app.prompts.manager import get_prompt_manager
from app.services.asr.base import TranscriptSegment
from app.services.llm.base import LLMService
from app.utils.transcript_processor import TranscriptProcessor

logger = logging.getLogger(__name__)


async def generate_summaries_with_quality_awareness(
    task_id: str,
    segments: list[TranscriptSegment],
    content_style: str,
    session: AsyncSession,
    user_id: str,
    provider: str | None = None,
    model_id: str | None = None,
) -> tuple[list[Summary], dict[str, Any]]:
    """质量感知的摘要生成

    Args:
        task_id: 任务ID
        segments: 转写片段列表
        content_style: 内容风格 (meeting/lecture/podcast/video/general)
        session: 数据库会话
        user_id: 用户ID
        provider: LLM provider（可选）
        model_id: LLM model ID（可选）

    Returns:
        tuple: (生成的Summary列表, 元数据字典)
    """
    # ===== Step 1: 评估转写质量 =====
    quality = TranscriptProcessor.assess_quality(segments)

    logger.info(
        f"Task {task_id}: Transcript quality assessment - "
        f"score: {quality.quality_score}, "
        f"avg_confidence: {quality.avg_confidence:.2f}"
    )

    # ===== Step 2: 预处理转写文本 =====
    preprocessed_text = TranscriptProcessor.preprocess(
        segments,
        filter_filler_words=True,
        merge_same_speaker=True,
        merge_threshold_seconds=2.0,
    )

    logger.info(
        f"Task {task_id}: Text preprocessed - "
        f"original_length: {sum(len(s.content) for s in segments)}, "
        f"preprocessed_length: {len(preprocessed_text)}"
    )

    # 生成质量提示
    quality_notice = TranscriptProcessor.get_quality_notice(quality)

    # ===== Step 3: 根据质量选择LLM服务 =====
    if quality.quality_score == "low":
        # 低质量：使用更强的模型
        logger.warning(
            f"Task {task_id}: Low quality transcript detected "
            f"(confidence: {quality.avg_confidence:.2f}), using premium model"
        )
        # 尝试使用 OpenRouter 的 Claude，如果不可用则fallback到指定的provider
        try:
            llm_service: LLMService = await SmartFactory.get_service(
                "llm", provider="openrouter", model_id="anthropic/claude-3.5-sonnet"
            )
        except Exception as e:
            logger.warning(
                f"Task {task_id}: Failed to get premium model, " f"fallback to standard: {e}"
            )
            llm_service = await SmartFactory.get_service(
                "llm", provider=provider, model_id=model_id
            )
    else:
        # 正常质量：使用用户指定或自动选择的服务
        llm_service = await SmartFactory.get_service("llm", provider=provider, model_id=model_id)

    logger.info(
        f"Task {task_id}: Using LLM service - "
        f"provider: {llm_service.provider}, model: {llm_service.model_name}"
    )

    # ===== Step 4: 章节划分（可选，仅对长内容） =====
    chapters_data = None
    chapters_summary = None

    if len(preprocessed_text) > 2000:
        try:
            chapters_data = await _generate_chapters(
                task_id=task_id,
                text=preprocessed_text,
                content_style=content_style,
                quality_notice=quality_notice,
                llm_service=llm_service,
            )

            # 存储章节信息
            chapters_summary = Summary(
                task_id=task_id,
                summary_type="chapters",
                version=1,
                is_active=True,
                content=json.dumps(chapters_data, ensure_ascii=False),
                model_used=llm_service.model_name,
                prompt_version="v1.2.0",
                token_count=len(json.dumps(chapters_data)),
            )
            session.add(chapters_summary)

            logger.info(
                f"Task {task_id}: Chapter segmentation completed - "
                f"{chapters_data['total_chapters']} chapters identified"
            )

        except Exception as e:
            logger.warning(f"Task {task_id}: Chapter segmentation failed: {e}", exc_info=True)
            # 章节划分失败不影响后续摘要生成
            chapters_data = None
    else:
        logger.info(
            f"Task {task_id}: Content too short ({len(preprocessed_text)} chars), "
            "skipping chapter segmentation"
        )

    # ===== Step 5: 生成各类摘要 =====
    summaries = []

    for summary_type in ("overview", "key_points", "action_items"):
        logger.info(f"Task {task_id}: Generating {summary_type} summary (style: {content_style})")

        try:
            content = await _generate_single_summary(
                text=preprocessed_text,
                summary_type=summary_type,
                content_style=content_style,
                quality_notice=quality_notice,
                llm_service=llm_service,
            )

            summary = Summary(
                task_id=task_id,
                summary_type=summary_type,
                version=1,
                is_active=True,
                content=content,
                model_used=llm_service.model_name,
                prompt_version="v1.2.0",
                token_count=len(content),
            )
            summaries.append(summary)

            logger.info(
                f"Task {task_id}: Generated {summary_type} summary " f"({len(content)} characters)"
            )

        except Exception as e:
            logger.error(
                f"Task {task_id}: Failed to generate {summary_type} summary: {e}",
                exc_info=True,
            )
            # 单个摘要失败不影响其他摘要的生成
            continue

    # 如果有章节摘要，添加到列表
    if chapters_summary:
        summaries.append(chapters_summary)

    # 准备元数据
    metadata = {
        "quality_score": quality.quality_score,
        "avg_confidence": quality.avg_confidence,
        "llm_provider": llm_service.provider,
        "llm_model": llm_service.model_name,
        "chapters_count": chapters_data["total_chapters"] if chapters_data else 0,
        "summaries_generated": len(summaries),
    }

    return summaries, metadata


async def _generate_chapters(
    task_id: str,
    text: str,
    content_style: str,
    quality_notice: str,
    llm_service: LLMService,
) -> dict[str, Any]:
    """生成章节划分

    Args:
        task_id: 任务ID
        text: 预处理后的文本
        content_style: 内容风格
        quality_notice: 质量提示
        llm_service: LLM服务实例

    Returns:
        章节数据（JSON格式）
    """
    # 获取章节划分提示词
    prompt_config = get_prompt_manager().get_prompt(
        category="segmentation",
        prompt_type="segment",
        locale="zh-CN",
        variables={"transcript": text, "quality_notice": quality_notice},
        content_style=content_style,
    )

    # 调用LLM进行章节划分
    segmentation_result = await llm_service.generate(
        prompt=prompt_config["user_prompt"],
        system_message=prompt_config["system"],
        temperature=prompt_config["model_params"].get("temperature", 0.3),
        max_tokens=prompt_config["model_params"].get("max_tokens", 1500),
    )

    # 解析JSON结果
    try:
        # 尝试直接解析
        chapters_data = json.loads(segmentation_result)
    except json.JSONDecodeError:
        # 如果解析失败，尝试提取JSON部分
        import re

        json_match = re.search(r"\{.*\}", segmentation_result, re.DOTALL)
        if json_match:
            chapters_data = json.loads(json_match.group())
        else:
            raise ValueError(f"Failed to parse chapter segmentation result for task {task_id}")

    # 验证格式
    if "total_chapters" not in chapters_data or "chapters" not in chapters_data:
        raise ValueError(f"Invalid chapter segmentation format for task {task_id}")

    return chapters_data


async def _generate_single_summary(
    text: str,
    summary_type: str,
    content_style: str,
    quality_notice: str,
    llm_service: LLMService,
) -> str:
    """生成单个摘要

    Args:
        text: 预处理后的文本
        summary_type: 摘要类型
        content_style: 内容风格
        quality_notice: 质量提示
        llm_service: LLM服务实例

    Returns:
        生成的摘要内容
    """
    # 获取提示词
    prompt_config = get_prompt_manager().get_prompt(
        category="summary",
        prompt_type=summary_type,
        locale="zh-CN",
        variables={"transcript": text, "quality_notice": quality_notice},
        content_style=content_style,
    )

    # 调用LLM生成摘要
    content = await llm_service.generate(
        prompt=prompt_config["user_prompt"],
        system_message=prompt_config["system"],
        temperature=prompt_config["model_params"].get("temperature", 0.7),
        max_tokens=prompt_config["model_params"].get("max_tokens", 1500),
    )

    return content
