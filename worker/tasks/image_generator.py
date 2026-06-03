"""AI 智能配图生成模块

提供摘要智能配图功能，支持：
- 从摘要文本中提取图片占位符
- 并行生成多张图片
- 上传到存储服务
- 替换占位符为 Markdown 图片
"""

import asyncio
import json
import logging
import re
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from app.core.smart_factory import SmartFactory
from app.models.summary import Summary
from app.models.task import Task
from app.prompts.manager import get_prompt_manager
from app.services.notifications.service import NotificationService
from app.services.notifications.types import NotificationType
from worker.db import get_sync_db_session
from worker.redis_client import publish_user_notification_sync

logger = logging.getLogger(__name__)

# 新格式图片占位符正则：{{IMAGE: 类型 | 描述 | 关键文字}} 或 {IMAGE: 类型 | 描述 | 关键文字}
IMAGE_PLACEHOLDER_PATTERN_NEW_DOUBLE = r"\{\{IMAGE:\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^}]+)\}\}"
IMAGE_PLACEHOLDER_PATTERN_NEW_SINGLE = r"(?<!\{)\{IMAGE:\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^}]+)\}(?!\})"

# 旧格式图片占位符正则（向后兼容）：{{IMAGE: 描述}} 或 {IMAGE: 描述}
IMAGE_PLACEHOLDER_PATTERN_OLD = r"\{?\{IMAGE:\s*([^}|]+)\}?\}"


def extract_image_placeholders(content: str) -> list[dict]:
    """提取所有图片占位符（支持新旧两种格式，单双花括号）

    新格式: {{IMAGE: 类型 | 描述 | 关键文字}} 或 {IMAGE: 类型 | 描述 | 关键文字}
    旧格式: {{IMAGE: 描述}} 或 {IMAGE: 描述}

    Args:
        content: 摘要内容

    Returns:
        [{"placeholder": "原始占位符", "type": "图片类型", "description": "描述", "key_texts": ["文字1", "文字2"]}, ...]
    """
    results = []
    matched_positions = set()

    # 1. 先匹配新格式（双花括号）{{IMAGE: 类型 | 描述 | 关键文字}}
    for match in re.finditer(IMAGE_PLACEHOLDER_PATTERN_NEW_DOUBLE, content):
        image_type = match.group(1).strip().lower()
        description = match.group(2).strip()
        key_texts_str = match.group(3).strip()
        key_texts = [t.strip() for t in key_texts_str.split(",") if t.strip()]

        # 重建原始占位符用于后续替换
        placeholder = f"{{{{IMAGE: {match.group(1).strip()} | {match.group(2).strip()} | {match.group(3).strip()}}}}}"

        results.append(
            {
                "placeholder": placeholder,
                "type": image_type,
                "description": description,
                "key_texts": key_texts,
            }
        )
        matched_positions.add((match.start(), match.end()))

    # 2. 匹配新格式（单花括号）{IMAGE: 类型 | 描述 | 关键文字}
    for match in re.finditer(IMAGE_PLACEHOLDER_PATTERN_NEW_SINGLE, content):
        # 跳过已匹配的位置
        if any(start <= match.start() < end or start < match.end() <= end for start, end in matched_positions):
            continue

        image_type = match.group(1).strip().lower()
        description = match.group(2).strip()
        key_texts_str = match.group(3).strip()
        key_texts = [t.strip() for t in key_texts_str.split(",") if t.strip()]

        # 使用原始单花括号格式
        placeholder = f"{{IMAGE: {match.group(1).strip()} | {match.group(2).strip()} | {match.group(3).strip()}}}"

        results.append(
            {
                "placeholder": placeholder,
                "type": image_type,
                "description": description,
                "key_texts": key_texts,
            }
        )
        matched_positions.add((match.start(), match.end()))

    # 3. 匹配旧格式（双花括号）{{IMAGE: 描述}}
    for match in re.finditer(r"\{\{IMAGE:\s*([^}|]+)\}\}", content):
        # 跳过已匹配的位置
        if any(start <= match.start() < end or start < match.end() <= end for start, end in matched_positions):
            continue

        description = match.group(1).strip()
        placeholder = f"{{{{IMAGE: {description}}}}}"

        results.append(
            {
                "placeholder": placeholder,
                "type": None,  # 旧格式无类型，后续使用默认
                "description": description,
                "key_texts": [],
            }
        )
        matched_positions.add((match.start(), match.end()))

    # 4. 匹配旧格式（单花括号）{IMAGE: 描述}
    for match in re.finditer(r"(?<!\{)\{IMAGE:\s*([^}|]+)\}(?!\})", content):
        # 跳过已匹配的位置
        if any(start <= match.start() < end or start < match.end() <= end for start, end in matched_positions):
            continue

        description = match.group(1).strip()
        placeholder = f"{{IMAGE: {description}}}"

        results.append(
            {
                "placeholder": placeholder,
                "type": None,
                "description": description,
                "key_texts": [],
            }
        )

    return results


def get_auto_images_config() -> dict:
    """获取 auto_images 配置

    Returns:
        配置字典，包含 enabled, max_images, timeout_seconds 等
    """
    try:
        prompt_manager = get_prompt_manager()
        config = prompt_manager._load_config("summary")
        features = config.get("features", {})
        auto_images = features.get("auto_images", {})
        return auto_images
    except Exception as e:
        logger.warning(f"Failed to load auto_images config: {e}")
        return {
            "enabled": False,
            "max_images": 3,
            "timeout_seconds": 60,
            "supported_summary_types": ["overview"],
            "supported_content_styles": ["lecture", "podcast", "video", "documentary", "explainer"],
        }


def is_auto_images_enabled(summary_type: str, content_style: str | None = None) -> bool:
    """检查是否启用自动配图

    Args:
        summary_type: 摘要类型
        content_style: 内容风格

    Returns:
        是否启用
    """
    config = get_auto_images_config()

    if not config.get("enabled", False):
        logger.debug("Auto images disabled: feature not enabled in config")
        return False

    supported_types = config.get("supported_summary_types", [])
    if summary_type not in supported_types:
        logger.debug(f"Auto images disabled: summary_type '{summary_type}' not in supported types {supported_types}")
        return False

    logger.debug(f"Auto images enabled for summary_type={summary_type}, content_style={content_style}")
    return True


def _normalize_image_text(value: str, max_length: int = 48) -> str:
    text = re.sub(r"!\[[^\]]*]\([^)]*\)", "", value)
    text = re.sub(r"\*\*|__|`|>|#+", "", text)
    text = re.sub(r"^\s*[-*+\d.)、]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ：:，,。.;；|")
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _extract_article_image_topic(content: str) -> str:
    for raw_line in content.splitlines():
        line = _normalize_image_text(raw_line)
        if line and "IMAGE:" not in line:
            return line
    return "内容核心概览"


def _extract_article_image_key_texts(content: str, topic: str, max_items: int = 4) -> list[str]:
    key_texts: list[str] = []
    seen: set[str] = set()

    for raw_line in content.splitlines():
        line = _normalize_image_text(raw_line, max_length=18)
        if not line or line == topic or "IMAGE:" in line or line in seen:
            continue
        seen.add(line)
        key_texts.append(line)
        if len(key_texts) >= max_items:
            break

    if not key_texts:
        key_texts.append(_normalize_image_text(topic, max_length=18))

    return key_texts


def _build_default_article_image_placeholder(
    content: str,
    content_style: str | None = None,
) -> dict[str, Any] | None:
    topic = _extract_article_image_topic(content)
    if not topic:
        return None

    style = content_style or "general"
    try:
        image_config = get_prompt_manager().get_image_config(style)
        image_type = image_config.get("default_type", "infographic")
    except Exception as exc:
        logger.warning("Failed to load image config for style %s: %s", style, exc)
        image_type = "infographic"

    key_texts = _extract_article_image_key_texts(content, topic)
    placeholder = f"{{{{IMAGE: {image_type} | {topic} | {', '.join(key_texts)}}}}}"

    return {
        "placeholder": placeholder,
        "type": image_type,
        "description": topic,
        "key_texts": key_texts,
    }


def _insert_article_image_placeholder(content: str, placeholder: str) -> str:
    stripped = content.rstrip()
    if not stripped:
        return placeholder

    blocks = re.split(r"(\n\s*\n)", stripped, maxsplit=1)
    if len(blocks) == 3:
        return f"{blocks[0]}{blocks[1]}{placeholder}\n\n{blocks[2]}"

    return f"{placeholder}\n\n{stripped}"


def _alt_from_placeholder(placeholder: str) -> str:
    """从占位符提取描述作为 alt，与 replace_placeholders 的取描述口径一致。

    新格式 {{IMAGE: 类型 | 描述 | 关键文字}} -> 取「描述」；旧格式 {{IMAGE: 描述}} -> 取「描述」。
    """
    cleaned = placeholder.replace("{{IMAGE:", "").replace("}}", "")
    cleaned = cleaned.replace("{IMAGE:", "").replace("}", "")
    if "|" in cleaned:
        parts = cleaned.split("|")
        return parts[1].strip() if len(parts) >= 2 else parts[0].strip()
    return cleaned.strip()


def build_image_specs(content: str, content_style: str | None) -> tuple[str, list[dict[str, object]]]:
    """规划 overview 配图占位符并返回 pending 状态的图集（不生成图片）。

    复用 extract_image_placeholders / _build_default_article_image_placeholder 的规划逻辑：
    - content 已含 {{IMAGE:…}} 占位符：原样保留 content，按占位符建 specs；
    - content 无占位符：自动规划一张默认文章配图并把占位符插入 content（作为锚点），
      返回插入后的 content（供调用方写回 Summary.content，保留锚点不被覆盖）。

    返回 (content, specs)。specs 每项严格对齐跨仓契约：
      {"placeholder","status":"pending","url":None,"alt","model_id":None,"error":None}
    """
    placeholders = extract_image_placeholders(content)
    if not placeholders:
        default_placeholder = _build_default_article_image_placeholder(content, content_style)
        if default_placeholder is None:
            return content, []
        content = _insert_article_image_placeholder(content, default_placeholder["placeholder"])
        placeholders = [default_placeholder]

    specs: list[dict[str, object]] = []
    for p in placeholders:
        ph = p["placeholder"]
        specs.append(
            {
                "placeholder": ph,
                "status": "pending",
                "url": None,
                "alt": p.get("description") or _alt_from_placeholder(ph),
                "model_id": None,
                "error": None,
            }
        )
    return content, specs


def apply_image_result_to_summary(
    session: Any,
    summary_id: str,
    result: dict[str, Any],
) -> dict[str, object] | None:
    """把单张图片生成结果回写到 Summary.images 对应项（按 placeholder 匹配）。

    result 形如 generate_single_image 的返回：
      {"placeholder","url","status"("success"|"failed"),"model_id","error"?}
    回写后该项 status 归一为 "ready"|"failed"。返回更新后的 images 项；占位符不存在返回 None。
    JSONB 就地改子项后须 flag_modified 才会被 SQLAlchemy 检测并 UPDATE。
    """
    summary = session.query(Summary).filter(Summary.id == summary_id).first()
    if summary is None or not summary.images:
        return None
    placeholder = result.get("placeholder")
    target: dict[str, object] | None = None
    for item in summary.images:
        if item.get("placeholder") == placeholder:
            target = item
            break
    if target is None:
        return None
    if result.get("status") == "success" and result.get("url"):
        target["status"] = "ready"
        target["url"] = result.get("url")
        target["error"] = None
    else:
        target["status"] = "failed"
        target["url"] = None
        target["error"] = result.get("error") or "image generation failed"
    if result.get("model_id"):
        target["model_id"] = result.get("model_id")
    flag_modified(summary, "images")
    session.commit()
    return target


def publish_image_ready_global(
    *,
    user_id: str,
    task_id: str,
    summary_id: str,
    placeholder: str,
    status: str,
    url: str | None,
    model_id: str | None,
    summary_type: str = "overview",
) -> None:
    """发 image_ready 事件到全局 WS user:{uid}:updates（方案 A，跨仓契约信封）。

    best-effort：发布失败只记日志，绝不冒泡（图主流程不受影响）。
    """
    envelope = {
        "kind": "image_ready",
        "task_id": task_id,
        "summary_id": summary_id,
        "summary_type": summary_type,
        "placeholder": placeholder,
        "status": status,
        "url": url,
        "model_id": model_id,
    }
    try:
        publish_user_notification_sync(user_id, json.dumps(envelope, ensure_ascii=False))
    except Exception:
        logger.warning("Task %s: publish image_ready to global WS failed, suppressed", task_id, exc_info=True)


async def upload_image(
    user_id: str,
    task_id: str,
    image_data: bytes,
    image_format: str = "png",
) -> str:
    """上传图片到存储服务（同时上传到云存储和 MinIO）

    Args:
        user_id: 用户 ID
        task_id: 任务 ID
        image_data: 图片二进制数据
        image_format: 图片格式

    Returns:
        str: 存储对象 key
    """
    # 生成唯一文件名
    image_id = uuid.uuid4().hex
    object_key = f"summary_images/{user_id}/{task_id}/{image_id}.{image_format}"

    # 写入临时文件
    with tempfile.NamedTemporaryFile(suffix=f".{image_format}", delete=False) as tmp_file:
        tmp_file.write(image_data)
        tmp_path = tmp_file.name

    try:
        content_type = f"image/{image_format}"

        # 上传到 MinIO（供前端访问）
        try:
            minio_storage = await SmartFactory.get_service("storage", provider="minio")
            minio_storage.upload_file(object_name=object_key, file_path=tmp_path, content_type=content_type)
            logger.info(f"Uploaded summary image to MinIO: {object_key}")
        except Exception as e:
            logger.warning(f"Failed to upload to MinIO: {e}")

        # 上传到云存储（备份）
        try:
            cloud_storage = await SmartFactory.get_service("storage")
            cloud_storage.upload_file(object_name=object_key, file_path=tmp_path, content_type=content_type)
            logger.info(f"Uploaded summary image to cloud storage: {object_key}")
        except Exception as e:
            logger.warning(f"Failed to upload to cloud storage: {e}")

        return object_key

    finally:
        Path(tmp_path).unlink(missing_ok=True)


def get_image_url(object_key: str) -> str:
    """获取图片的公开访问 URL

    Args:
        object_key: 存储对象 key

    Returns:
        图片 URL
    """
    import asyncio

    async def _get_url():
        storage = await SmartFactory.get_service("storage")
        # 使用预签名 URL，有效期 7 天
        return storage.generate_presigned_url(object_key, expires_in=7 * 24 * 3600)

    return asyncio.run(_get_url())


async def generate_single_image(
    item: dict,
    user_id: str,
    task_id: str,
    content_style: str = "general",
    locale: str = "zh-CN",
    timeout: int = 60,
) -> dict:
    """生成单张图片（使用风格化提示词）

    Args:
        item: {"placeholder": "...", "type": "图片类型", "description": "描述", "key_texts": [...]}
        user_id: 用户 ID
        task_id: 任务 ID
        content_style: 内容风格 (lecture/podcast/meeting/...)
        locale: 语言 (zh-CN/en-US)
        timeout: 超时时间（秒）

    Returns:
        {"placeholder": "...", "url": "...", "status": "success|failed"}
    """
    model_id = None
    try:
        # 1. 获取提示词管理器
        prompt_manager = get_prompt_manager()

        # 2. 获取内容风格对应的图片配置
        image_config = prompt_manager.get_image_config(content_style)

        # 3. 确定图片类型（优先使用占位符指定的类型，否则使用默认）
        image_type = item.get("type") or image_config.get("default_type", "infographic")

        # 4. 构建风格化提示词
        image_prompt = prompt_manager.get_image_prompt(
            content_style=content_style,
            image_type=image_type,
            description=item["description"],
            key_texts=item.get("key_texts", []),
            locale=locale,
        )

        # 5. 获取配置和 LLM 服务
        config = get_auto_images_config()
        image_model = config.get("image_model", {})
        provider = image_model.get("provider", "image_service")
        model_id = image_model.get("model_id", "gemini-3-pro-image-preview")

        llm_service = await SmartFactory.get_service(
            "llm",
            provider=provider,
            model_id=model_id,
        )

        # 6. 获取宽高比配置
        aspect_ratio = image_config.get("aspect_ratio", "16:9")

        # 7. 生成图片
        image_data = await asyncio.wait_for(
            llm_service.generate_image(
                prompt=image_prompt,
                aspect_ratio=aspect_ratio,
                image_size="2K",
            ),
            timeout=timeout,
        )

        # 8. 上传到存储（MinIO + 云存储）
        object_key = await upload_image(user_id, task_id, image_data)

        # 9. 返回相对路径（前端通过 same-origin nginx 代理访问）
        image_path = object_key.replace("summary_images/", "")
        image_url = f"/api/v1/summaries/images/{image_path}"

        logger.info(f"Generated styled image ({image_type}/{content_style}) for '{item['description']}': {object_key}")

        return {
            "placeholder": item["placeholder"],
            "url": image_url,
            "status": "success",
            "model_id": model_id,
        }

    except TimeoutError:
        logger.warning(f"Image generation timeout for: {item['description']}")
        return {
            "placeholder": item["placeholder"],
            "url": None,
            "status": "failed",
            "error": "timeout",
            "model_id": model_id,
        }
    except Exception as e:
        logger.warning(f"Image generation failed for '{item['description']}': {e}")
        return {
            "placeholder": item["placeholder"],
            "url": None,
            "status": "failed",
            "error": str(e),
            "model_id": model_id,
        }


async def generate_images_parallel(
    placeholders: list[dict[str, Any]],
    user_id: str,
    task_id: str,
    content_style: str = "general",
    locale: str = "zh-CN",
    max_images: int = 3,
    timeout: int = 60,
    on_image_ready: Callable[[dict[str, Any], int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """并行生成多张图片，每生成一张立即回调

    Args:
        placeholders: [{"placeholder": "...", "type": "...", "description": "...", "key_texts": [...]}, ...]
        user_id: 用户 ID
        task_id: 任务 ID
        content_style: 内容风格 (lecture/podcast/meeting/...)
        locale: 语言 (zh-CN/en-US)
        max_images: 最大图片数量
        timeout: 单张图片超时（秒）
        on_image_ready: 单张图片完成时的回调函数，参数为 (result, current_index, total)

    Returns:
        [{"placeholder": "...", "url": "...", "status": "success|failed"}, ...]
    """
    # 限制数量
    placeholders = placeholders[:max_images]

    if not placeholders:
        return []

    total = len(placeholders)
    logger.info(f"Generating {total} styled images ({content_style}/{locale}) for task {task_id}")

    # 创建任务，保留索引信息
    async def generate_with_index(index: int, item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        result = await generate_single_image(item, user_id, task_id, content_style, locale, timeout)
        return index, result

    tasks = [asyncio.create_task(generate_with_index(i, p)) for i, p in enumerate(placeholders)]

    # 按完成顺序处理结果
    final_results: list[dict[str, Any]] = [{}] * total
    completed_count = 0

    for coro in asyncio.as_completed(tasks):
        try:
            index, result = await coro
            final_results[index] = result
            completed_count += 1

            # 回调通知
            if on_image_ready:
                on_image_ready(result, completed_count, total)

        except Exception as e:
            logger.error(f"Image generation exception: {e}")
            # 找到未完成的占位符
            for i, r in enumerate(final_results):
                if not r:
                    final_results[i] = {
                        "placeholder": placeholders[i]["placeholder"],
                        "url": None,
                        "status": "failed",
                        "error": str(e),
                    }
                    completed_count += 1
                    if on_image_ready:
                        on_image_ready(final_results[i], completed_count, total)
                    break

    success_count = sum(1 for r in final_results if r and r.get("status") == "success")
    logger.info(f"Image generation completed: {success_count}/{total} succeeded")

    return final_results


def replace_placeholders(content: str, image_results: list[dict]) -> str:
    """替换占位符为 Markdown 图片或移除失败的占位符

    Args:
        content: 原始摘要内容
        image_results: 图片生成结果列表

    Returns:
        替换后的内容
    """
    for result in image_results:
        placeholder = result["placeholder"]
        if result["status"] == "success" and result.get("url"):
            # 提取描述作为 alt text
            # 新格式: {{IMAGE: 类型 | 描述 | 关键文字}} 或 {IMAGE: 类型 | 描述 | 关键文字}
            # 旧格式: {{IMAGE: 描述}} 或 {IMAGE: 描述}
            description = placeholder
            # 处理新格式（取描述部分）
            if "|" in description:
                # 移除花括号（支持单双花括号）
                cleaned = description.replace("{{IMAGE:", "").replace("}}", "")
                cleaned = cleaned.replace("{IMAGE:", "").replace("}", "")
                parts = cleaned.split("|")
                description = parts[1].strip() if len(parts) >= 2 else parts[0].strip()
            else:
                # 处理旧格式
                description = description.replace("{{IMAGE: ", "").replace("}}", "")
                description = description.replace("{IMAGE: ", "").replace("}", "")

            # 替换为 Markdown 图片
            markdown_img = f"![{description}]({result['url']})"
            content = content.replace(placeholder, markdown_img)
        else:
            # 移除失败的占位符（保留一个空行避免排版问题）
            content = content.replace(placeholder, "")

    # 清理多余的空行
    content = re.sub(r"\n{3,}", "\n\n", content)

    return content


async def process_summary_images(
    content: str,
    task_id: str,
    user_id: str,
    summary_type: str,
    content_style: str | None = None,
    locale: str = "zh-CN",
    redis_client: Any | None = None,
    stream_key: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """处理摘要中的图片占位符

    Args:
        content: 摘要内容
        task_id: 任务 ID
        user_id: 用户 ID
        summary_type: 摘要类型
        content_style: 内容风格
        locale: 语言 (zh-CN/en-US)
        redis_client: Redis 客户端（用于发布事件）
        stream_key: SSE 流 key

    Returns:
        (处理后的内容, 图片结果列表)
    """
    # 检查是否启用
    if not is_auto_images_enabled(summary_type, content_style):
        logger.info(f"Auto images not enabled for {summary_type}/{content_style}")
        return content, []

    # 提取占位符。旧版流程依赖摘要提示词主动输出 {{IMAGE: ...}}；
    # 如果没有输出，overview 仍然自动规划一张文章配图，避免配图能力被 prompt 偶然性阻断。
    placeholders = extract_image_placeholders(content)
    if not placeholders:
        default_placeholder = _build_default_article_image_placeholder(content, content_style)
        if default_placeholder is None:
            logger.info(f"No image placeholders found in summary for task {task_id}")
            return content, []

        content = _insert_article_image_placeholder(content, default_placeholder["placeholder"])
        placeholders = [default_placeholder]
        logger.info(
            "Task %s: No image placeholders found; planned default article image (%s/%s)",
            task_id,
            default_placeholder.get("type"),
            content_style or "general",
        )

    logger.info(f"Found {len(placeholders)} image placeholders in task {task_id}")

    # 获取配置
    config = get_auto_images_config()
    max_images = config.get("max_images", 3)
    timeout = config.get("timeout_seconds", 60)

    # 发布处理开始事件
    if redis_client and stream_key:
        redis_client.publish(
            stream_key,
            json.dumps(
                {
                    "event": "images.processing",
                    "data": {
                        "status": "generating",
                        "total": min(len(placeholders), max_images),
                    },
                },
                ensure_ascii=False,
            ),
        )

    # 定义回调函数：每生成一张图片就发送事件
    def on_image_ready(result: dict, current: int, total: int):
        if redis_client and stream_key:
            redis_client.publish(
                stream_key,
                json.dumps(
                    {
                        "event": "image.ready",
                        "data": {
                            "placeholder": result["placeholder"],
                            "url": result.get("url"),
                            "status": result["status"],
                            "current": current,
                            "total": total,
                            "model_id": result.get("model_id"),
                        },
                    },
                    ensure_ascii=False,
                ),
            )

    # 并行生成图片，每完成一张立即通知
    image_results = await generate_images_parallel(
        placeholders,
        user_id,
        task_id,
        content_style=content_style or "general",
        locale=locale,
        max_images=max_images,
        timeout=timeout,
        on_image_ready=on_image_ready,
    )

    # 替换占位符
    final_content = replace_placeholders(content, image_results)

    # 配图整体失败（无一成功）时发 VISUAL_FAILED；成功/部分成功不发。
    if image_results and not any(r.get("status") == "success" for r in image_results):
        try:
            with get_sync_db_session() as notif_session:
                # 边缘渲染契约：notif.visual_failed 文案含 {task_title} 占位符，必须随 params 提供，
                # 否则前端/渠道会渲染出字面 {task_title}。本生产者作用域无 task 对象，就地查标题；
                # 查询失败回退默认名，绝不阻断通知本身。
                try:
                    task_obj = notif_session.query(Task).filter(Task.id == task_id).first()
                    task_title = (task_obj.title if task_obj else None) or "未命名任务"
                except Exception:
                    task_title = "未命名任务"
                NotificationService.notify(
                    notif_session,
                    type=NotificationType.VISUAL_FAILED,
                    user_id=user_id,
                    params={"summary_type": summary_type, "task_title": task_title},
                    task_id=task_id,
                )
        except Exception:  # best-effort：配图通知失败绝不影响摘要主流程
            logger.warning("Task %s: VISUAL_FAILED notify failed, suppressed", task_id, exc_info=True)

    # 发布所有图片完成事件
    if redis_client and stream_key:
        redis_client.publish(
            stream_key,
            json.dumps(
                {
                    "event": "images.completed",
                    "data": {
                        "total": len(image_results),
                        "success": sum(1 for r in image_results if r["status"] == "success"),
                        "image_model": next(
                            (
                                r["model_id"]
                                for r in image_results
                                if r.get("status") == "success" and r.get("model_id")
                            ),
                            None,
                        ),
                    },
                },
                ensure_ascii=False,
            ),
        )

    return final_content, image_results
