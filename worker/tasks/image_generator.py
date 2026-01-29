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
from pathlib import Path
from typing import Optional

from app.core.smart_factory import SmartFactory
from app.prompts.manager import get_prompt_manager

logger = logging.getLogger(__name__)

# 图片占位符正则表达式（支持单花括号和双花括号两种格式）
IMAGE_PLACEHOLDER_PATTERN = r"\{?\{IMAGE:\s*([^}]+)\}?\}"


def extract_image_placeholders(content: str) -> list[dict]:
    """提取所有图片占位符

    Args:
        content: 摘要内容

    Returns:
        [{"placeholder": "原始占位符", "description": "描述"}, ...]
    """
    # 匹配 {{IMAGE: xxx}} 或 {IMAGE: xxx} 格式
    results = []
    # 先尝试双花括号格式
    double_matches = re.findall(r"\{\{IMAGE:\s*([^}]+)\}\}", content)
    for m in double_matches:
        results.append({"placeholder": f"{{{{IMAGE: {m}}}}}", "description": m.strip()})

    # 再尝试单花括号格式（排除已匹配的双花括号）
    single_matches = re.findall(r"(?<!\{)\{IMAGE:\s*([^}]+)\}(?!\})", content)
    for m in single_matches:
        results.append({"placeholder": f"{{IMAGE: {m}}}", "description": m.strip()})

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
        return False

    supported_types = config.get("supported_summary_types", [])
    if summary_type not in supported_types:
        return False

    if content_style:
        supported_styles = config.get("supported_content_styles", [])
        if content_style not in supported_styles:
            return False

    return True


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
    image_id = str(uuid.uuid4())[:8]
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
            minio_storage.upload_file(
                object_name=object_key, file_path=tmp_path, content_type=content_type
            )
            logger.info(f"Uploaded summary image to MinIO: {object_key}")
        except Exception as e:
            logger.warning(f"Failed to upload to MinIO: {e}")

        # 上传到云存储（备份）
        try:
            cloud_storage = await SmartFactory.get_service("storage")
            cloud_storage.upload_file(
                object_name=object_key, file_path=tmp_path, content_type=content_type
            )
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
    timeout: int = 60,
) -> dict:
    """生成单张图片

    Args:
        item: {"placeholder": "{{IMAGE: xxx}}", "description": "xxx"}
        user_id: 用户 ID
        task_id: 任务 ID
        timeout: 超时时间（秒）

    Returns:
        {"placeholder": "...", "url": "...", "status": "success|failed"}
    """
    try:
        config = get_auto_images_config()
        image_model = config.get("image_model", {})
        provider = image_model.get("provider", "openrouter")
        model_id = image_model.get("model_id", "google/gemini-2.0-flash-exp:free")

        # 获取 LLM 服务（支持图像生成的模型）
        llm_service = await SmartFactory.get_service(
            "llm",
            provider=provider,
            model_id=model_id,
        )

        # 生成图片
        image_prompt = f"生成一张信息图，用于文章配图。要求：\n1. 风格：现代、简洁、专业\n2. 主题：{item['description']}\n3. 配色：和谐、适合阅读\n4. 不要包含任何文字"

        image_data = await asyncio.wait_for(
            llm_service.generate_image(
                prompt=image_prompt,
                aspect_ratio="16:9",
                image_size="2K",
            ),
            timeout=timeout,
        )

        # 上传到存储（MinIO + 云存储）
        object_key = await upload_image(user_id, task_id, image_data)

        # 返回后端 API URL，前端通过 API 代理访问图片
        from app.config import settings

        # 本地开发用 localhost:8000，生产环境需要设置 API_BASE_URL
        api_base = (settings.API_BASE_URL or "http://localhost:8000").rstrip("/")
        image_path = object_key.replace("summary_images/", "")
        image_url = f"{api_base}/api/v1/summaries/images/{image_path}"

        logger.info(f"Generated image for '{item['description']}': {object_key}")

        return {
            "placeholder": item["placeholder"],
            "url": image_url,
            "status": "success",
        }

    except asyncio.TimeoutError:
        logger.warning(f"Image generation timeout for: {item['description']}")
        return {
            "placeholder": item["placeholder"],
            "url": None,
            "status": "failed",
            "error": "timeout",
        }
    except Exception as e:
        logger.warning(f"Image generation failed for '{item['description']}': {e}")
        return {
            "placeholder": item["placeholder"],
            "url": None,
            "status": "failed",
            "error": str(e),
        }


async def generate_images_parallel(
    placeholders: list[dict],
    user_id: str,
    task_id: str,
    max_images: int = 3,
    timeout: int = 60,
    on_image_ready: Optional[callable] = None,
) -> list[dict]:
    """并行生成多张图片，每生成一张立即回调

    Args:
        placeholders: [{"placeholder": "{{IMAGE: xxx}}", "description": "xxx"}, ...]
        user_id: 用户 ID
        task_id: 任务 ID
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
    logger.info(f"Generating {total} images in parallel for task {task_id}")

    # 创建任务，保留索引信息
    async def generate_with_index(index: int, item: dict) -> tuple[int, dict]:
        result = await generate_single_image(item, user_id, task_id, timeout)
        return index, result

    tasks = [asyncio.create_task(generate_with_index(i, p)) for i, p in enumerate(placeholders)]

    # 按完成顺序处理结果
    final_results = [None] * total
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
                if r is None:
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

    success_count = sum(1 for r in final_results if r and r["status"] == "success")
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
            # 提取描述作为 alt text（处理单双花括号两种格式）
            description = placeholder
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
    redis_client: Optional[object] = None,
    stream_key: Optional[str] = None,
) -> tuple[str, list[dict]]:
    """处理摘要中的图片占位符

    Args:
        content: 摘要内容
        task_id: 任务 ID
        user_id: 用户 ID
        summary_type: 摘要类型
        content_style: 内容风格
        redis_client: Redis 客户端（用于发布事件）
        stream_key: SSE 流 key

    Returns:
        (处理后的内容, 图片结果列表)
    """
    # 检查是否启用
    if not is_auto_images_enabled(summary_type, content_style):
        logger.info(f"Auto images not enabled for {summary_type}/{content_style}")
        return content, []

    # 提取占位符
    placeholders = extract_image_placeholders(content)
    if not placeholders:
        logger.info(f"No image placeholders found in summary for task {task_id}")
        return content, []

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
        max_images=max_images,
        timeout=timeout,
        on_image_ready=on_image_ready,
    )

    # 替换占位符
    final_content = replace_placeholders(content, image_results)

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
                    },
                },
                ensure_ascii=False,
            ),
        )

    return final_content, image_results
