"""YouTube auto-transcription Celery task.

Automatically creates transcription tasks for new videos from subscribed channels
that have auto_transcribe enabled.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from celery import shared_task
from sqlalchemy import func, select

from app.core.registry import ServiceRegistry
from app.models.notification import Notification
from app.models.task import Task
from app.models.youtube_auto_transcribe_log import YouTubeAutoTranscribeLog
from app.models.youtube_subscription import YouTubeSubscription
from app.models.youtube_video import YouTubeVideo
from worker.db import get_sync_db_session
from worker.redis_client import publish_user_notification_sync

logger = logging.getLogger(__name__)

# Default max duration for auto-transcribe (2 hours)
DEFAULT_MAX_DURATION = 7200
# Max concurrent auto-transcribe tasks per user
MAX_CONCURRENT_AUTO_TASKS = 3


def _generate_content_hash(content: str) -> str:
    """Generate SHA256 hash for content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _check_asr_quota_available(session, user_id: str) -> bool:
    """Check if user has ASR quota available (sync version).

    Args:
        session: Database session
        user_id: User ID

    Returns:
        True if quota is available
    """
    from app.services.asr_quota_service import check_any_provider_available_sync

    all_providers = ServiceRegistry.list_services("asr")
    if not all_providers:
        return False

    has_quota, _ = check_any_provider_available_sync(
        session, all_providers, user_id, variant="file"
    )
    return has_quota


def _get_processing_task_count(session, user_id: str) -> int:
    """Get count of currently processing tasks for user.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        Number of processing tasks
    """
    result = session.execute(
        select(func.count(Task.id)).where(
            Task.user_id == user_id,
            Task.status.in_(["queued", "processing", "transcribing", "extracting"]),
            Task.deleted_at.is_(None),
        )
    )
    return result.scalar() or 0


def _process_single_video(
    session,
    user_id: str,
    subscription: YouTubeSubscription,
    video: YouTubeVideo,
    max_duration: int,
    language: Optional[str],
    request_id: Optional[str],
) -> Dict[str, Any]:
    """Process a single video for auto-transcription.

    Args:
        session: Database session
        user_id: User ID
        subscription: YouTubeSubscription
        video: YouTubeVideo to process
        max_duration: Max duration in seconds
        language: Language preference
        request_id: Request ID for tracing

    Returns:
        Dict with status and details
    """
    video_id = video.video_id

    # Check if already processed
    existing_log = session.execute(
        select(YouTubeAutoTranscribeLog).where(
            YouTubeAutoTranscribeLog.user_id == user_id,
            YouTubeAutoTranscribeLog.video_id == video_id,
        )
    ).scalar_one_or_none()

    if existing_log:
        return {
            "status": "skipped",
            "video_id": video_id,
            "reason": "already_processed",
        }

    # Check duration
    if video.duration_seconds and video.duration_seconds > max_duration:
        # Log skip
        log = YouTubeAutoTranscribeLog(
            user_id=user_id,
            video_id=video_id,
            subscription_id=str(subscription.id),
            status="skipped",
            skip_reason=f"duration_exceeded:{video.duration_seconds}>{max_duration}",
        )
        session.add(log)
        session.commit()
        return {
            "status": "skipped",
            "video_id": video_id,
            "reason": "duration_exceeded",
            "duration": video.duration_seconds,
            "max_duration": max_duration,
        }

    # Check if task already exists
    content_hash = _generate_content_hash(f"youtube:{video_id}")
    existing_task = session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.content_hash == content_hash,
            Task.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if existing_task:
        # Log skip
        log = YouTubeAutoTranscribeLog(
            user_id=user_id,
            video_id=video_id,
            subscription_id=str(subscription.id),
            task_id=str(existing_task.id),
            status="skipped",
            skip_reason="task_exists",
        )
        session.add(log)
        session.commit()
        return {
            "status": "skipped",
            "video_id": video_id,
            "reason": "task_exists",
            "task_id": str(existing_task.id),
        }

    # Create task
    import json

    task = Task(
        user_id=user_id,
        content_hash=content_hash,
        title=video.title,
        source_type="youtube",
        source_url=f"https://www.youtube.com/watch?v={video_id}",
        source_metadata=json.dumps(
            {
                "auto_transcribed": True,
                "channel_id": subscription.channel_id,
                "channel_title": subscription.channel_title,
            }
        ),
        options=json.dumps({"language": language} if language else {}),
        status="queued",
        progress=1,
        stage="queued",
        request_id=request_id,
    )
    session.add(task)
    session.flush()  # Get task.id

    # Log auto-transcribe
    log = YouTubeAutoTranscribeLog(
        user_id=user_id,
        video_id=video_id,
        subscription_id=str(subscription.id),
        task_id=str(task.id),
        status="created",
    )
    session.add(log)

    # Create notification for auto-transcribe start
    notification = Notification(
        user_id=user_id,
        task_id=str(task.id),
        category="task",
        action="auto_transcribe_started",
        title=f"自动转写《{video.title[:50]}》",
        message=f"频道「{subscription.channel_title}」的新视频已开始自动转写",
        action_url=f"/tasks/{task.id}",
        priority="normal",
        extra_data={
            "video_id": video_id,
            "channel_id": subscription.channel_id,
            "channel_title": subscription.channel_title,
            "auto_transcribed": True,
        },
    )
    session.add(notification)
    session.commit()

    # Publish WebSocket notification
    import json as json_module

    ws_notification = json_module.dumps(
        {
            "code": 0,
            "message": "success",
            "data": {
                "type": "auto_transcribe_started",
                "task_id": str(task.id),
                "video_id": video_id,
                "title": video.title,
                "channel_id": subscription.channel_id,
                "channel_title": subscription.channel_title,
            },
            "traceId": request_id or "",
        },
        ensure_ascii=False,
    )
    publish_user_notification_sync(user_id, ws_notification)

    # Trigger Celery task
    from worker.celery_app import celery_app

    celery_app.send_task(
        "worker.tasks.process_youtube.process_youtube",
        args=[str(task.id)],
        kwargs={"request_id": request_id},
    )

    logger.info(f"Created auto-transcribe task {task.id} for video {video_id}")

    return {
        "status": "created",
        "video_id": video_id,
        "task_id": str(task.id),
        "title": video.title,
    }


@shared_task(
    name="worker.tasks.youtube_auto_transcribe.process_auto_transcriptions",
    bind=True,
    soft_time_limit=600,
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def process_auto_transcriptions(
    self,
    user_id: str,
    channel_id: str,
    video_ids: List[str],
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Process auto-transcription for newly synced videos.

    Called by sync_channel_videos after new videos are discovered.

    Args:
        user_id: User ID
        channel_id: YouTube channel ID
        video_ids: List of new video IDs to process
        request_id: Request ID for tracing

    Returns:
        Dict with results summary
    """
    logger.info(
        f"Starting auto-transcription for user {user_id}, "
        f"channel {channel_id}, {len(video_ids)} videos"
    )

    results: Dict[str, List[Dict[str, Any]]] = {
        "created": [],
        "skipped": [],
        "failed": [],
    }

    try:
        with get_sync_db_session() as session:
            # Get subscription settings
            subscription = session.execute(
                select(YouTubeSubscription).where(
                    YouTubeSubscription.user_id == user_id,
                    YouTubeSubscription.channel_id == channel_id,
                )
            ).scalar_one_or_none()

            if not subscription:
                logger.warning(f"Subscription not found for channel {channel_id}")
                return {
                    "status": "error",
                    "error": "subscription_not_found",
                    "results": results,
                }

            if not subscription.auto_transcribe:
                logger.info(f"Auto-transcribe disabled for channel {channel_id}")
                return {
                    "status": "skipped",
                    "reason": "auto_transcribe_disabled",
                    "results": results,
                }

            max_duration = subscription.auto_transcribe_max_duration or DEFAULT_MAX_DURATION
            language = subscription.auto_transcribe_language

            # Check ASR quota
            if not _check_asr_quota_available(session, user_id):
                logger.warning(f"No ASR quota available for user {user_id}")
                return {
                    "status": "skipped",
                    "reason": "no_asr_quota",
                    "results": results,
                }

            # Check concurrent task limit
            processing_count = _get_processing_task_count(session, user_id)
            if processing_count >= MAX_CONCURRENT_AUTO_TASKS:
                logger.warning(
                    f"Concurrent task limit reached for user {user_id}: "
                    f"{processing_count}/{MAX_CONCURRENT_AUTO_TASKS}"
                )
                return {
                    "status": "skipped",
                    "reason": "concurrent_limit_reached",
                    "processing_count": processing_count,
                    "results": results,
                }

            # Get videos from database
            videos_result = session.execute(
                select(YouTubeVideo).where(
                    YouTubeVideo.user_id == user_id,
                    YouTubeVideo.video_id.in_(video_ids),
                )
            )
            videos = {v.video_id: v for v in videos_result.scalars().all()}

            # Process each video
            for video_id in video_ids:
                video = videos.get(video_id)
                if not video:
                    results["skipped"].append({"video_id": video_id, "reason": "video_not_found"})
                    continue

                # Re-check concurrent limit for each video
                processing_count = _get_processing_task_count(session, user_id)
                if processing_count >= MAX_CONCURRENT_AUTO_TASKS:
                    results["skipped"].append(
                        {"video_id": video_id, "reason": "concurrent_limit_reached"}
                    )
                    continue

                try:
                    result = _process_single_video(
                        session,
                        user_id,
                        subscription,
                        video,
                        max_duration,
                        language,
                        request_id,
                    )
                    results[result["status"]].append(result)
                except Exception as e:
                    logger.exception(f"Failed to process video {video_id}: {e}")
                    results["failed"].append({"video_id": video_id, "error": str(e)})

        logger.info(
            f"Auto-transcription complete for channel {channel_id}: "
            f"created={len(results['created'])}, skipped={len(results['skipped'])}, "
            f"failed={len(results['failed'])}"
        )

        return {
            "status": "success",
            "results": results,
            "summary": {
                "created": len(results["created"]),
                "skipped": len(results["skipped"]),
                "failed": len(results["failed"]),
            },
        }

    except Exception as e:
        logger.exception(f"Unexpected error in auto-transcription: {e}")
        raise
