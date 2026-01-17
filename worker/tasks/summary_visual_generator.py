"""可视化摘要生成模块

提供基于 Mermaid 的可视化摘要生成功能，包括：
- 思维导图 (mindmap)
- 时间轴 (timeline)
- 流程图 (flowchart)

支持后端渲染为 PNG/SVG 图片
"""

import json
import logging
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.smart_factory import SmartFactory
from app.models.summary import Summary
from app.prompts.manager import get_prompt_manager
from app.services.asr.base import TranscriptSegment
from app.services.llm.base import LLMService
from app.utils.transcript_processor import TranscriptProcessor

logger = logging.getLogger(__name__)


def validate_mermaid(content: str) -> str:
    """从 LLM 输出中提取并验证 Mermaid 语法

    Args:
        content: LLM 返回的原始内容

    Returns:
        str: 提取的纯 Mermaid 代码

    Raises:
        ValueError: 如果没有找到有效的 Mermaid 语法
    """
    # 匹配 ```mermaid ... ``` 代码块
    pattern = r"```mermaid\s*\n(.*?)\n```"
    match = re.search(pattern, content, re.DOTALL)

    if match:
        mermaid_code = match.group(1).strip()
    else:
        # 尝试不带代码块的情况
        mermaid_code = content.strip()

    # 基本验证：检查是否以合法的 Mermaid 图表类型开头
    valid_types = ["mindmap", "timeline", "flowchart", "graph", "sequenceDiagram"]
    if not any(mermaid_code.startswith(diagram_type) for diagram_type in valid_types):
        raise ValueError(
            f"Invalid Mermaid syntax: must start with one of {valid_types}, "
            f"got: {mermaid_code[:50]}..."
        )

    logger.info(f"Validated Mermaid code, length: {len(mermaid_code)} characters")
    return mermaid_code


async def render_mermaid_to_image(
    mermaid_code: str,
    output_format: Literal["png", "svg"] = "png",
    background_color: str = "white",
    width: int = 1200,
    height: int = 800,
) -> bytes:
    """使用 mmdc CLI 将 Mermaid 图表渲染为图片

    Args:
        mermaid_code: Mermaid 语法代码
        output_format: 输出格式 (png 或 svg)
        background_color: 背景颜色
        width: 图片宽度（仅 PNG）
        height: 图片高度（仅 PNG）

    Returns:
        bytes: 图片二进制数据

    Raises:
        RuntimeError: 如果渲染失败
    """
    # 创建临时文件
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".mmd", delete=False, encoding="utf-8"
    ) as mmd_file:
        mmd_file.write(mermaid_code)
        mmd_path = mmd_file.name

    output_path = mmd_path.replace(".mmd", f".{output_format}")

    try:
        # 构建 mmdc 命令
        cmd = [
            "mmdc",
            "-i",
            mmd_path,
            "-o",
            output_path,
            "-b",
            background_color,
            "-t",
            "neutral",  # 主题: default, forest, dark, neutral
        ]

        # PNG 特定参数
        if output_format == "png":
            cmd.extend(["-w", str(width), "-H", str(height)])

        logger.info(f"Running mmdc command: {' '.join(cmd)}")

        # 执行渲染命令
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )

        if result.returncode != 0:
            logger.error(f"mmdc failed with code {result.returncode}: {result.stderr}")
            raise RuntimeError(f"Mermaid rendering failed: {result.stderr}")

        # 读取生成的图片
        with open(output_path, "rb") as f:
            image_data = f.read()

        logger.info(
            f"Successfully rendered {output_format.upper()} image, size: {len(image_data)} bytes"
        )
        return image_data

    finally:
        # 清理临时文件
        Path(mmd_path).unlink(missing_ok=True)
        Path(output_path).unlink(missing_ok=True)


async def upload_visual_image(
    user_id: str,
    task_id: str,
    visual_type: str,
    image_data: bytes,
    image_format: str,
) -> str:
    """上传可视化图片到存储服务

    Args:
        user_id: 用户 ID
        task_id: 任务 ID
        visual_type: 可视化类型 (mindmap/timeline/flowchart)
        image_data: 图片二进制数据
        image_format: 图片格式 (png/svg)

    Returns:
        str: 存储对象 key
    """
    # 生成唯一文件名
    visual_id = str(uuid.uuid4())[:8]
    object_key = f"visuals/{user_id}/{task_id}/{visual_type}_{visual_id}.{image_format}"

    # 获取存储服务
    storage = await SmartFactory.get_service("storage")

    # 写入临时文件
    with tempfile.NamedTemporaryFile(
        suffix=f".{image_format}", delete=False
    ) as tmp_file:
        tmp_file.write(image_data)
        tmp_path = tmp_file.name

    try:
        # 上传到存储
        content_type = "image/png" if image_format == "png" else "image/svg+xml"
        await storage.upload_file(
            object_name=object_key, file_path=tmp_path, content_type=content_type
        )
        logger.info(f"Uploaded visual image to storage: {object_key}")
        return object_key

    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def generate_visual_summary(
    task_id: str,
    segments: list[TranscriptSegment],
    visual_type: Literal["mindmap", "timeline", "flowchart"],
    content_style: str,
    session: AsyncSession,
    user_id: str,
    provider: str | None = None,
    model_id: str | None = None,
    generate_image: bool = True,
    image_format: Literal["png", "svg"] = "png",
) -> Summary:
    """生成可视化摘要（带可选图片渲染）

    Args:
        task_id: 任务 ID
        segments: 转写片段列表
        visual_type: 可视化类型
        content_style: 内容风格 (meeting/lecture/podcast/video/general)
        session: 数据库会话
        user_id: 用户 ID
        provider: LLM provider（可选）
        model_id: LLM model ID（可选）
        generate_image: 是否生成图片
        image_format: 图片格式

    Returns:
        Summary: 生成的可视化摘要对象
    """
    logger.info(
        f"Task {task_id}: Generating visual summary - "
        f"type: {visual_type}, style: {content_style}, generate_image: {generate_image}"
    )

    # ===== Step 1: 评估转写质量 =====
    quality = TranscriptProcessor.assess_quality(segments)
    quality_notice = TranscriptProcessor.get_quality_notice(quality)

    logger.info(
        f"Task {task_id}: Quality assessment - score: {quality.quality_score}, "
        f"confidence: {quality.avg_confidence:.2f}"
    )

    # ===== Step 2: 预处理转写文本 =====
    preprocessed_text = TranscriptProcessor.preprocess(
        segments, filter_filler_words=True, merge_same_speaker=True
    )

    logger.info(
        f"Task {task_id}: Preprocessed text length: {len(preprocessed_text)} chars"
    )

    # ===== Step 3: 获取 LLM 服务 =====
    llm_service: LLMService = await SmartFactory.get_service(
        "llm", provider=provider, model_id=model_id
    )

    logger.info(
        f"Task {task_id}: Using LLM - provider: {llm_service.provider}, "
        f"model: {llm_service.model_name}"
    )

    # ===== Step 4: 获取可视化提示词 =====
    prompt_config = get_prompt_manager().get_prompt(
        category="visual",
        prompt_type=visual_type,
        locale="zh-CN",
        variables={"transcript": preprocessed_text, "quality_notice": quality_notice},
        content_style=content_style,
    )

    # ===== Step 5: 调用 LLM 生成 Mermaid 语法 =====
    logger.info(f"Task {task_id}: Calling LLM to generate {visual_type} diagram")

    raw_output = await llm_service.generate(
        prompt=prompt_config["user_prompt"],
        system_message=prompt_config["system"],
        temperature=prompt_config["model_params"].get("temperature", 0.4),
        max_tokens=prompt_config["model_params"].get("max_tokens", 2000),
    )

    # ===== Step 6: 验证并提取 Mermaid 代码 =====
    try:
        mermaid_code = validate_mermaid(raw_output)
    except ValueError as e:
        logger.error(
            f"Task {task_id}: Mermaid validation failed: {e}\n"
            f"Raw output: {raw_output[:500]}..."
        )
        raise

    # ===== Step 7: 渲染图片（可选）=====
    image_key = None
    if generate_image:
        try:
            logger.info(
                f"Task {task_id}: Rendering Mermaid to {image_format.upper()} image"
            )
            image_data = await render_mermaid_to_image(
                mermaid_code, output_format=image_format
            )

            # 上传到存储
            image_key = await upload_visual_image(
                user_id=user_id,
                task_id=task_id,
                visual_type=visual_type,
                image_data=image_data,
                image_format=image_format,
            )
            logger.info(f"Task {task_id}: Image uploaded to {image_key}")

        except Exception as e:
            logger.warning(
                f"Task {task_id}: Failed to generate/upload image: {e}. "
                "Continuing with Mermaid syntax only.",
                exc_info=True,
            )
            # 图片生成失败不影响 Mermaid 语法的保存
            image_key = None

    # ===== Step 8: 创建 Summary 记录 =====
    summary = Summary(
        task_id=task_id,
        summary_type=f"visual_{visual_type}",
        version=1,
        is_active=True,
        content=mermaid_code,  # 主要内容存储 Mermaid 代码
        model_used=llm_service.model_name,
        prompt_version="v1.3.0",
        token_count=len(mermaid_code),
        visual_format="mermaid",
        visual_content=mermaid_code,  # 冗余存储，便于查询
        image_key=image_key,
        image_format=image_format if image_key else None,
    )

    session.add(summary)

    logger.info(
        f"Task {task_id}: Visual summary generated successfully - "
        f"type: {visual_type}, has_image: {image_key is not None}"
    )

    return summary
