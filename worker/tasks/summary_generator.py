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

from app.config import settings
from app.core.smart_factory import SmartFactory
from app.models.summary import Summary
from app.prompts.manager import get_prompt_manager
from app.services.asr.base import TranscriptSegment
from app.services.llm.base import LLMService
from app.services.summary.markdown_fence import strip_markdown_fence
from app.services.summary.preamble import strip_summary_preamble
from app.utils.transcript_processor import TranscriptProcessor

logger = logging.getLogger(__name__)


def _merge_prompt_and_usage(
    prompt_meta: dict[str, Any] | None,
    usage: dict[str, int | None] | None,
) -> dict[str, Any]:
    """把 prompt 溯源元数据(slug/version)与 LLM 真实 token 用量合并为单个落库 metadata。

    usage 为 None(provider 未透出用量)时不写 token 键 → Summary 的 token 列留 NULL,
    不落伪造值;调用方仍可从同一 dict 取 slug/version。
    """
    meta: dict[str, Any] = dict(prompt_meta or {})
    if usage:
        meta["input_tokens"] = usage.get("input_tokens")
        meta["output_tokens"] = usage.get("output_tokens")
    return meta


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
        content_style: 内容风格 (meeting/conversation/lecture/tutorial/review/news/general)
        session: 数据库会话
        user_id: 用户ID
        provider: LLM provider（可选）
        model_id: LLM model ID（可选）

    Returns:
        tuple: (生成的Summary列表, 元数据字典)
    """
    # ===== Step 0: 归一 provider/model_id，杜绝把 None 传给 SmartFactory =====
    # 本协程是公开入口，provider/model_id 默认 None；标准路径与 premium 失败回退路径
    # 都会把它们透传给 SmartFactory.get_service("llm", ...)，而后者在 model_id 为空时会抛
    # ValueError("model_id is required for llm services")。生产调用方（process_audio）已用
    # _resolve_llm_selection 传入具体值，但此处再兜底一层，保证直接调用 / 测试 / 未来调用方
    # 在 premium 回退分支也不会因 None 而崩（与 _default_model_id_for_provider 的默认保持一致）。
    provider = provider or "proxy"
    model_id = model_id or settings.LITELLM_MODEL

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
        # 低质量：升级到 premium 模型（LiteLLM 别名，具体后端由代理决定）
        logger.warning(
            f"Task {task_id}: Low quality transcript detected "
            f"(confidence: {quality.avg_confidence:.2f}), using premium model"
        )
        try:
            llm_service: LLMService = await SmartFactory.get_service(
                "llm", provider="proxy", model_id="chat-premium", user_id=user_id
            )
        except Exception as e:
            logger.warning(f"Task {task_id}: Failed to get premium model, fallback to standard: {e}")
            llm_service = await SmartFactory.get_service("llm", provider=provider, model_id=model_id, user_id=user_id)
    else:
        # 正常质量：使用用户指定或自动选择的服务
        # 成本归因:带上 user_id,让 ProxyLLMService 给 LiteLLM 请求体打 end-user 标签(GET
        # /customer/info 据此把这条 audio 主摘要的花费拆到具体用户);与 youtube/regenerate 对齐。
        llm_service = await SmartFactory.get_service("llm", provider=provider, model_id=model_id, user_id=user_id)

    logger.info(
        f"Task {task_id}: Using LLM service - provider: {llm_service.provider}, model: {llm_service.model_name}"
    )

    # ===== Step 4: 章节划分（可选，仅对长内容） =====
    chapters_data = None
    chapters_summary = None

    if len(preprocessed_text) > 2000:
        try:
            chapters_data, chapters_meta = await _generate_chapters(
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
                prompt_slug=chapters_meta.get("slug"),
                prompt_version=chapters_meta.get("version"),
                input_tokens=chapters_meta.get("input_tokens"),
                output_tokens=chapters_meta.get("output_tokens"),
                # 真实 output token 优先；provider 未透出用量时回落字符数近似(向后兼容)。
                token_count=chapters_meta.get("output_tokens") or len(json.dumps(chapters_data)),
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
            f"Task {task_id}: Content too short ({len(preprocessed_text)} chars), skipping chapter segmentation"
        )

    # ===== Step 5: 生成各类摘要 =====
    summaries = []

    for summary_type in ("overview", "key_points", "action_items"):
        logger.info(f"Task {task_id}: Generating {summary_type} summary (style: {content_style})")

        try:
            content, prompt_meta = await _generate_single_summary(
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
                prompt_slug=prompt_meta.get("slug"),
                prompt_version=prompt_meta.get("version"),
                input_tokens=prompt_meta.get("input_tokens"),
                output_tokens=prompt_meta.get("output_tokens"),
                # 真实 output token 优先；provider 未透出用量时回落字符数近似(向后兼容)。
                token_count=prompt_meta.get("output_tokens") or len(content),
            )
            summaries.append(summary)

            logger.info(f"Task {task_id}: Generated {summary_type} summary ({len(content)} characters)")

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
) -> tuple[dict[str, Any], dict[str, Any]]:
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

    # 调用LLM进行章节划分，并取回真实 token 用量
    segmentation_result, usage = await llm_service.generate_with_usage(
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

    # 同摘要路径:连同 prompt 溯源元数据(slug + 真版本)与真实 token 用量返回,供章节 Summary 落库。
    return chapters_data, _merge_prompt_and_usage(prompt_config.get("metadata"), usage)


async def _generate_single_summary(
    text: str,
    summary_type: str,
    content_style: str,
    quality_notice: str,
    llm_service: LLMService,
) -> tuple[str, dict[str, Any]]:
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

    # 调用LLM生成摘要，并取回真实 token 用量
    content, usage = await llm_service.generate_with_usage(
        prompt=prompt_config["user_prompt"],
        system_message=prompt_config["system"],
        temperature=prompt_config["model_params"].get("temperature", 0.7),
        max_tokens=prompt_config["model_params"].get("max_tokens", 1500),
    )

    # LLM 偶发把整段散文包进 ```markdown 围栏，落库前在源头剥掉（与前端渲染防御同语义）
    # 再剥掉偶发逸出的客套/元描述开场白（先剥围栏再剥开场白）
    cleaned = strip_summary_preamble(strip_markdown_fence(content))
    # 一并返回 prompt 溯源元数据(命中的 PromptHub slug + 真实版本)与真实 token 用量,
    # 供调用方落库,取代此前硬编码的 prompt_version="v1.2.0" 与字符数近似的 token_count。
    return cleaned, _merge_prompt_and_usage(prompt_config.get("metadata"), usage)
