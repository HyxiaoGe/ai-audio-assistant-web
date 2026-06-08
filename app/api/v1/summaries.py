from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db, get_media_user, get_stream_user
from app.api.v1.media import assert_owns_media_key, serve_media_object
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.rate_limit import rate_limit, rate_limit_query
from app.core.response import success
from app.core.security import SCOPE_STREAM, issue_scoped_token
from app.i18n.codes import ErrorCode
from app.models.summary import Summary
from app.models.task import Task
from app.schemas.summary import (
    SummaryCompareRequest,
    SummaryImageItem,
    SummaryItem,
    SummaryListResponse,
    SummaryRegenerateRequest,
)
from app.services.media_url import build_media_download_url

router = APIRouter(prefix="/summaries")


def _text_capable_llm_providers() -> set[str]:
    """已注册且支持文本生成的 LLM provider 白名单。

    排除 image_service 这类 supports_text_generation=False 的「只生图」provider——
    重新生成与多模型对比走的是 summarize()/generate()，
    若放进白名单会一路到 worker 才崩。
    """
    from app.core.registry import ServiceRegistry

    return set(ServiceRegistry.list_text_llm_providers())


def _to_summary_item(summary: Summary, image_url: str | None) -> SummaryItem:
    """把 Summary ORM 映射为出参 SummaryItem（含渐进式展示的 images 图集）。

    images 直接透传 JSONB 列；非 overview/无图时 summary.images 为 None -> 出参 images 为 None。
    """
    images: list[SummaryImageItem] | None = None
    if summary.images:
        images = [SummaryImageItem(**item) for item in summary.images]
    return SummaryItem(
        id=str(summary.id),
        summary_type=summary.summary_type,
        version=summary.version,
        is_active=summary.is_active,
        content=summary.content,
        model_used=summary.model_used,
        prompt_version=summary.prompt_version,
        token_count=summary.token_count,
        created_at=summary.created_at,
        visual_format=summary.visual_format,
        image_url=image_url,
        image_model_used=summary.image_model_used,
        images=images,
    )


@router.get("/{task_id}")
async def get_summaries(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    # Verify task exists and belongs to user
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task_result = await db.execute(task_stmt)
    task = task_result.scalar_one_or_none()

    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    # Get all active summaries for this task
    summary_stmt = (
        select(Summary)
        .where(Summary.task_id == task_id, Summary.is_active.is_(True))
        .order_by(Summary.summary_type, Summary.version.desc())
    )
    summary_result = await db.execute(summary_stmt)
    summaries = summary_result.scalars().all()

    items = []
    for summary in summaries:
        image_url = None
        if summary.image_key:
            image_url = await build_media_download_url(summary.image_key, user.id)
        items.append(_to_summary_item(summary, image_url))

    response = SummaryListResponse(task_id=task_id, total=len(items), items=items)
    return success(data=jsonable_encoder(response))


@router.post("/{task_id}/regenerate")
async def regenerate_summary(
    request: Request,
    task_id: str,
    data: SummaryRegenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """重新生成指定类型的摘要"""
    from worker.celery_app import celery_app

    # Verify task exists and belongs to user
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task_result = await db.execute(task_stmt)
    task = task_result.scalar_one_or_none()

    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    # Check if task has transcripts
    from app.models.transcript import Transcript

    transcript_stmt = select(Transcript).where(Transcript.task_id == task_id).limit(1)
    transcript_result = await db.execute(transcript_stmt)
    has_transcripts = transcript_result.scalar_one_or_none() is not None

    if not has_transcripts:
        raise BusinessError(ErrorCode.PARAMETER_ERROR, reason="任务没有转写结果，无法生成摘要")

    # 校验 provider：与 compare 端点一致，把"未知 / 仅支持生图(image_service)"的 provider
    # 拦在 endpoint 层，而不是放到 worker 里崩。provider 为 None 表示自动选择，放行。
    if data.provider:
        valid_providers = _text_capable_llm_providers()
        if data.provider not in valid_providers:
            raise BusinessError(
                ErrorCode.PARAMETER_ERROR,
                reason=f"未知或不支持文本生成的 LLM provider: {data.provider}（可用: {sorted(valid_providers)}）",
            )

    # Submit regeneration task
    trace_id = getattr(request.state, "trace_id", None)
    celery_app.send_task(
        "worker.tasks.regenerate_summary",
        args=[task_id, data.summary_type],
        kwargs={
            "model": data.provider,
            "model_id": data.model_id,
            "request_id": trace_id,
        },
    )

    return success(
        data={
            "task_id": task_id,
            "summary_type": data.summary_type,
            "provider": data.provider or "auto",
            "model_id": data.model_id,
            "status": "queued",
        }
    )


@router.post("/{task_id}/stream-ticket")
async def mint_stream_ticket(
    task_id: str,
    summary_type: str = Query(..., description="摘要类型"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """签发短期 stream 票据（绑定 task_id+summary_type），供 EventSource 用 ?token= 订阅。

    需 Authorization header 鉴权并校验任务归属；票据仅能用于该任务、该摘要类型的流。
    """
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task = (await db.execute(task_stmt)).scalar_one_or_none()
    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    token = issue_scoped_token(
        sub=user.id,
        scope=SCOPE_STREAM,
        ttl=settings.MEDIA_TOKEN_TTL,
        resource={"task_id": task_id, "summary_type": summary_type},
    )
    return success(data={"token": token, "expires_in": settings.MEDIA_TOKEN_TTL})


@router.get("/{task_id}/stream")
async def stream_summary_regeneration(
    task_id: str,
    summary_type: str = Query(..., description="摘要类型"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_stream_user),
) -> StreamingResponse:

    # Verify task belongs to user
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task_result = await db.execute(task_stmt)
    task = task_result.scalar_one_or_none()

    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    async def event_generator() -> AsyncIterator[str]:
        import logging
        import queue
        import threading

        from redis import Redis

        logger = logging.getLogger("api.summaries")

        from app.config import settings

        redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        stream_key = f"summary_stream:{task_id}:{summary_type}"

        msg_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()

        def redis_subscriber():
            try:
                pubsub = redis_client.pubsub()
                pubsub.subscribe(stream_key)

                while not stop_event.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if message and message.get("type") == "message":
                        msg_queue.put(message)

            except Exception as e:
                logger.error(f"Redis subscriber error: {e}")
                msg_queue.put({"error": str(e)})
            finally:
                try:
                    pubsub.unsubscribe(stream_key)
                    pubsub.close()
                except Exception:
                    logger.warning("pubsub close failed", exc_info=True)

        subscriber_thread = threading.Thread(target=redis_subscriber, daemon=True)
        subscriber_thread.start()
        await asyncio.sleep(0.2)

        try:
            yield "event: connected\n"
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"

            import time

            start_time = time.time()
            timeout_seconds = 120

            while (time.time() - start_time) < timeout_seconds:
                messages_processed = 0
                while not msg_queue.empty() and messages_processed < 50:
                    try:
                        message = msg_queue.get_nowait()
                        messages_processed += 1

                        if "error" in message:
                            return

                        data = message.get("data")
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")

                        try:
                            msg_obj = json.loads(data)
                            event_type = msg_obj.get("event", "message")
                            event_data = msg_obj.get("data", {})
                            msg_type = event_data.get("type")

                            if msg_type == "ping":
                                continue

                            payload = json.dumps(event_data, ensure_ascii=False)
                            yield f"event: {event_type}\ndata: {payload}\n\n"

                            if msg_type in ("summary.completed", "error"):
                                return
                        except json.JSONDecodeError:
                            yield f"data: {data}\n\n"

                    except queue.Empty:
                        break

                if messages_processed == 0:
                    try:
                        message = await asyncio.to_thread(msg_queue.get, timeout=0.1)

                        if "error" in message:
                            return

                        data = message.get("data")
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")

                        try:
                            msg_obj = json.loads(data)
                            event_type = msg_obj.get("event", "message")
                            event_data = msg_obj.get("data", {})
                            msg_type = event_data.get("type")

                            if msg_type == "ping":
                                continue

                            payload = json.dumps(event_data, ensure_ascii=False)
                            yield f"event: {event_type}\ndata: {payload}\n\n"

                            if msg_type in ("summary.completed", "error"):
                                return
                        except json.JSONDecodeError:
                            yield f"data: {data}\n\n"

                    except queue.Empty:
                        yield ": heartbeat\n\n"

        finally:
            stop_event.set()
            subscriber_thread.join(timeout=2.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{task_id}/{summary_id}/activate")
async def activate_summary(
    task_id: str,
    summary_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """将对比结果设置为当前活跃版本

    Args:
        task_id: 任务 ID
        summary_id: 要激活的摘要 ID

    Returns:
        更新后的摘要信息
    """
    # Verify task belongs to user
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task_result = await db.execute(task_stmt)
    task = task_result.scalar_one_or_none()

    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    # Get the summary to activate
    summary_stmt = select(Summary).where(
        Summary.id == summary_id,
        Summary.task_id == task_id,
    )
    summary_result = await db.execute(summary_stmt)
    target_summary = summary_result.scalar_one_or_none()

    if not target_summary:
        raise BusinessError(ErrorCode.SUMMARY_NOT_FOUND)

    # Deactivate all summaries of the same type
    active_stmt = select(Summary).where(
        Summary.task_id == task_id,
        Summary.summary_type == target_summary.summary_type,
        Summary.is_active.is_(True),
    )
    active_result = await db.execute(active_stmt)
    active_summaries = active_result.scalars().all()

    for summary in active_summaries:
        summary.is_active = False

    # Activate the target summary
    target_summary.is_active = True

    await db.commit()
    await db.refresh(target_summary)

    return success(
        data={
            "summary_id": str(target_summary.id),
            "task_id": task_id,
            "summary_type": target_summary.summary_type,
            "version": target_summary.version,
            "model_used": target_summary.model_used,
            "is_active": target_summary.is_active,
            "comparison_id": target_summary.comparison_id,
        },
        message="摘要已设置为当前版本",
    )


@router.post("/{task_id}/compare")
async def compare_models(
    request: Request,
    task_id: str,
    data: SummaryCompareRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(rate_limit(limit=settings.RATE_LIMIT_SUMMARY_COMPARE_PER_MIN, scope="summary_compare")),
) -> JSONResponse:
    """并行生成多个模型的摘要用于对比

    Args:
        task_id: 任务 ID
        data: 对比请求（包含摘要类型和模型列表）

    Returns:
        对比 ID 和任务 ID 列表
    """
    from uuid import uuid4

    from worker.celery_app import celery_app

    # Verify task exists and belongs to user
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task_result = await db.execute(task_stmt)
    task = task_result.scalar_one_or_none()

    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    # Check if task has transcripts
    from app.models.transcript import Transcript

    transcript_stmt = select(Transcript).where(Transcript.task_id == task_id).limit(1)
    transcript_result = await db.execute(transcript_stmt)
    has_transcripts = transcript_result.scalar_one_or_none() is not None

    if not has_transcripts:
        raise BusinessError(ErrorCode.PARAMETER_ERROR, reason="任务没有转写结果，无法生成摘要")

    # 校验 provider 是否在已注册的 LLM 服务白名单内（目前只有 proxy 与 image_service，
    # 之前 deepseek/qwen/doubao/moonshot/openrouter 已下线，应拦在 endpoint 层而不是 worker 里）。
    # 对比生成的是文本摘要，必须排除 image_service 这类只支持生图的 provider，
    # 否则 worker 调用 summarize() 会崩。
    valid_providers = _text_capable_llm_providers()
    for model_selection in data.models:
        if model_selection.provider not in valid_providers:
            raise BusinessError(
                ErrorCode.PARAMETER_ERROR,
                reason=f"未知或不支持文本生成的 LLM provider: {model_selection.provider}"
                f"（可用: {sorted(valid_providers)}）",
            )

    # Generate comparison ID
    comparison_id = uuid4().hex
    trace_id = getattr(request.state, "trace_id", None)

    # Submit regeneration tasks for each model
    celery_task_ids = []
    for model_selection in data.models:
        task_result = celery_app.send_task(
            "worker.tasks.regenerate_summary",
            args=[task_id, data.summary_type],
            kwargs={
                "model": model_selection.provider,
                "model_id": model_selection.model_id,
                "comparison_id": comparison_id,
                "request_id": trace_id,
            },
        )
        celery_task_ids.append(str(task_result.id))

    return success(
        data={
            "comparison_id": comparison_id,
            "task_id": task_id,
            "summary_type": data.summary_type,
            "models": [{"provider": m.provider, "model_id": m.model_id} for m in data.models],
            "celery_task_ids": celery_task_ids,
            "status": "queued",
        }
    )


@router.get("/{task_id}/compare/{comparison_id}")
async def get_comparison_results(
    task_id: str,
    comparison_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """获取多模型对比结果

    Args:
        task_id: 任务 ID
        comparison_id: 对比 ID

    Returns:
        对比结果列表
    """
    # Verify task belongs to user
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task_result = await db.execute(task_stmt)
    task = task_result.scalar_one_or_none()

    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    # Query all summaries with this comparison_id
    from app.models.summary import Summary

    summary_stmt = (
        select(Summary)
        .where(
            Summary.task_id == task_id,
            Summary.comparison_id == comparison_id,
        )
        .order_by(Summary.created_at.desc())
    )
    summary_result = await db.execute(summary_stmt)
    summaries = summary_result.scalars().all()

    from app.schemas.summary import SummaryComparisonItem, SummaryComparisonResponse

    items = [
        SummaryComparisonItem(
            model=s.model_used or "unknown",
            content=s.content,
            token_count=s.token_count,
            created_at=s.created_at,
            status="completed",
        )
        for s in summaries
    ]

    # Get the summary type from the first summary (all should have the same type)
    summary_type = summaries[0].summary_type if summaries else "unknown"

    response = SummaryComparisonResponse(
        comparison_id=comparison_id,
        task_id=task_id,
        summary_type=summary_type,
        models=[item.model for item in items],
        results=items,
    )

    return success(data=jsonable_encoder(response))


@router.get("/{task_id}/compare/{comparison_id}/stream")
async def stream_comparison(
    task_id: str,
    comparison_id: str,
    summary_type: str = Query(..., description="摘要类型"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_stream_user),
    _rl: None = Depends(
        rate_limit_query(
            limit=settings.RATE_LIMIT_SUMMARY_COMPARE_PER_MIN,
            scope="summary_compare",
            auth=get_stream_user,
        )
    ),
) -> StreamingResponse:
    """流式获取多模型对比结果

    同时监听多个模型的生成流，实时返回每个模型的进度
    """
    # Verify task belongs to user
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task_result = await db.execute(task_stmt)
    task = task_result.scalar_one_or_none()

    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    async def event_generator() -> AsyncIterator[str]:
        import logging
        import queue
        import threading

        from redis import Redis

        logger = logging.getLogger("api.summaries")

        from app.config import settings

        redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

        # 使用通配符订阅该任务和类型的所有流
        stream_key = f"summary_stream:{task_id}:{summary_type}"

        msg_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()

        def redis_subscriber():
            try:
                pubsub = redis_client.pubsub()
                pubsub.subscribe(stream_key)

                while not stop_event.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if message and message.get("type") == "message":
                        msg_queue.put(message)

            except Exception as e:
                logger.error(f"Redis subscriber error: {e}")
                msg_queue.put({"error": str(e)})
            finally:
                try:
                    pubsub.unsubscribe(stream_key)
                    pubsub.close()
                except Exception:
                    logger.warning("pubsub close failed", exc_info=True)

        subscriber_thread = threading.Thread(target=redis_subscriber, daemon=True)
        subscriber_thread.start()
        await asyncio.sleep(0.2)

        try:
            yield "event: connected\n"
            yield f"data: {json.dumps({'type': 'connected', 'comparison_id': comparison_id})}\n\n"

            import time

            start_time = time.time()
            timeout_seconds = 300  # 5分钟超时（多个模型需要更长时间）
            completed_summaries = set()  # 跟踪已完成的摘要ID

            while (time.time() - start_time) < timeout_seconds:
                messages_processed = 0
                while not msg_queue.empty() and messages_processed < 50:
                    try:
                        message = msg_queue.get_nowait()
                        messages_processed += 1

                        if "error" in message:
                            return

                        data = message.get("data")
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")

                        try:
                            msg_obj = json.loads(data)
                            event_type = msg_obj.get("event", "message")
                            event_data = msg_obj.get("data", {})
                            msg_type = event_data.get("type")

                            if msg_type == "ping":
                                continue

                            # 添加 comparison_id 到所有事件
                            event_data["comparison_id"] = comparison_id

                            payload = json.dumps(event_data, ensure_ascii=False)
                            yield f"event: {event_type}\ndata: {payload}\n\n"

                            # 跟踪完成的摘要
                            if msg_type == "summary.completed":
                                summary_id = event_data.get("summary_id")
                                if summary_id:
                                    completed_summaries.add(summary_id)

                        except json.JSONDecodeError:
                            yield f"data: {data}\n\n"

                    except queue.Empty:
                        break

                if messages_processed == 0:
                    try:
                        message = await asyncio.to_thread(msg_queue.get, timeout=0.1)

                        if "error" in message:
                            return

                        data = message.get("data")
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")

                        try:
                            msg_obj = json.loads(data)
                            event_type = msg_obj.get("event", "message")
                            event_data = msg_obj.get("data", {})
                            msg_type = event_data.get("type")

                            if msg_type == "ping":
                                continue

                            event_data["comparison_id"] = comparison_id
                            payload = json.dumps(event_data, ensure_ascii=False)
                            yield f"event: {event_type}\ndata: {payload}\n\n"

                            if msg_type == "summary.completed":
                                summary_id = event_data.get("summary_id")
                                if summary_id:
                                    completed_summaries.add(summary_id)

                        except json.JSONDecodeError:
                            yield f"data: {data}\n\n"

                    except queue.Empty:
                        yield ": heartbeat\n\n"

        finally:
            stop_event.set()
            subscriber_thread.join(timeout=2.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ===== Summary Images Endpoint =====


@router.get("/images/{path:path}")
async def get_summary_image(
    path: str,
    user: CurrentUser = Depends(get_media_user),
) -> Response:
    """获取摘要配图（从统一存储 OSS 服务端代理返回图片内容）

    path 格式: {user_id}/{task_id}/{image_id}.png

    需携带 token（header 或 ?token=，供文章内联 <img> 使用），且仅限对象归属者访问。
    图片小且不可变：用 allow_redirect=False 强制服务端代理（同源 URL 稳定可被浏览器长缓存，
    优于每次重签的 307 直下），并打 private+immutable 缓存头。
    """
    object_key = f"summary_images/{path}"
    assert_owns_media_key(object_key, user.id)

    resp = await serve_media_object(object_key, allow_redirect=False)
    # 图片按随机 image_id 命名、内容不可变 → 可长缓存。带 media token 鉴权属私有内容，
    # 用 private（仅浏览器缓存、不让 CF/共享代理缓存）+ immutable，让同一会话内反复进详情页
    # 命中浏览器缓存、免去重复经隧道回源。强制覆盖而非 setdefault：_proxy_media 会转发上游
    # OSS 的 cache-control，setdefault 对已存在 key 是 no-op，会让私有/不可变语义被悄悄丢弃
    # （甚至若上游回 public，私有图会带可共享缓存头穿过 CF）。本端点单点掌控缓存语义。
    resp.headers["Cache-Control"] = "private, max-age=2592000, immutable"
    return resp
