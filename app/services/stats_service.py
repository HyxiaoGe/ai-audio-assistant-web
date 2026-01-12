from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.llm_usage import LLMUsage
from app.models.task import Task
from app.models.task_stage import TaskStage
from app.models.user import User

_TIME_RANGE_VALUES = {"today", "week", "month", "all"}
_SERVICE_TYPES = ("asr", "llm")
_SERVICE_STAGE = {"asr": "transcribe", "llm": "summarize"}


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_duration(total_seconds: float) -> str:
    total = int(total_seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours}h {minutes}m {seconds}s"


class StatsService:
    def __init__(self, db: AsyncSession, user: User):
        self.db = db
        self.user = user

    async def _parse_time_range(
        self,
        time_range: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> tuple[datetime, datetime]:
        if time_range and time_range not in _TIME_RANGE_VALUES:
            raise BusinessError(ErrorCode.PARAMETER_ERROR, reason="invalid time_range")

        now = datetime.now(timezone.utc)

        if time_range == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif time_range == "week":
            start = now - timedelta(days=7)
            end = now
        elif time_range == "month":
            start = now - timedelta(days=30)
            end = now
        elif time_range == "all":
            stmt = select(func.min(Task.created_at)).where(
                Task.user_id == self.user.id,
                Task.deleted_at.is_(None),
            )
            result = await self.db.execute(stmt)
            earliest = result.scalar_one_or_none()
            start = earliest or (now - timedelta(days=30))
            end = now
        else:
            start = start_date or (now - timedelta(days=30))
            end = end_date or now

        start = _ensure_utc(start)
        end = _ensure_utc(end)
        if start > end:
            raise BusinessError(ErrorCode.PARAMETER_ERROR, reason="start_date after end_date")

        return start, end

    async def get_service_usage_overview(
        self,
        time_range: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        start, end = await self._parse_time_range(time_range, start_date, end_date)

        usage_by_provider: list[Dict[str, Any]] = []
        usage_by_service_type: list[Dict[str, Any]] = []

        for service_type in _SERVICE_TYPES:
            stage_type = _SERVICE_STAGE[service_type]
            provider_stats: dict[str, Dict[str, Any]] = defaultdict(
                lambda: {
                    "total": 0,
                    "completed": 0,
                    "failed": 0,
                    "pending": 0,
                    "processing": 0,
                }
            )
            duration_map: dict[str, float] = {}
            durations_by_provider: dict[str, list[float]] = defaultdict(list)

            if service_type == "asr":
                provider_field = Task.asr_provider.label("provider")
                time_field = Task.created_at

                status_query = select(
                    provider_field,
                    Task.status,
                    func.count(func.distinct(Task.id)).label("count"),
                ).where(
                    Task.user_id == self.user.id,
                    time_field >= start,
                    time_field <= end,
                    Task.deleted_at.is_(None),
                    Task.asr_provider.is_not(None),
                )
                status_rows = (await self.db.execute(
                    status_query.group_by(provider_field, Task.status)
                )).all()

                for row in status_rows:
                    provider = row.provider
                    if not provider:
                        continue
                    provider_stats[provider]["total"] += row.count
                    if row.status == "completed":
                        provider_stats[provider]["completed"] += row.count
                    elif row.status == "failed":
                        provider_stats[provider]["failed"] += row.count
                    elif row.status == "pending":
                        provider_stats[provider]["pending"] += row.count
                    elif row.status == "processing":
                        provider_stats[provider]["processing"] += row.count

                duration_stmt = (
                    select(provider_field, func.sum(Task.duration_seconds).label("duration"))
                    .where(
                        Task.user_id == self.user.id,
                        time_field >= start,
                        time_field <= end,
                        Task.deleted_at.is_(None),
                        Task.asr_provider.is_not(None),
                    )
                    .group_by(provider_field)
                )
                duration_rows = (await self.db.execute(duration_stmt)).all()
                duration_map = {
                    row.provider: float(row.duration or 0.0) for row in duration_rows
                }

                stage_stmt = (
                    select(
                        provider_field,
                        TaskStage.started_at,
                        TaskStage.completed_at,
                    )
                    .join(TaskStage, TaskStage.task_id == Task.id)
                    .where(
                        Task.user_id == self.user.id,
                        time_field >= start,
                        time_field <= end,
                        Task.deleted_at.is_(None),
                        provider_field.is_not(None),
                        TaskStage.stage_type == stage_type,
                        TaskStage.is_active.is_(True),
                        TaskStage.started_at.is_not(None),
                        TaskStage.completed_at.is_not(None),
                    )
                )
                stage_rows = (await self.db.execute(stage_stmt)).all()
                for row in stage_rows:
                    provider = row.provider
                    if not provider:
                        continue
                    durations_by_provider[provider].append(
                        (row.completed_at - row.started_at).total_seconds()
                    )
            else:
                provider_field = LLMUsage.model_id.label("provider")
                time_field = LLMUsage.created_at

                status_query = (
                    select(
                        provider_field,
                        LLMUsage.status,
                        func.count(LLMUsage.id).label("count"),
                    )
                    .where(
                        LLMUsage.user_id == self.user.id,
                        time_field >= start,
                        time_field <= end,
                        LLMUsage.model_id.is_not(None),
                        LLMUsage.model_id != "",
                    )
                    .group_by(provider_field, LLMUsage.status)
                )
                status_rows = (await self.db.execute(status_query)).all()
                for row in status_rows:
                    provider = row.provider
                    if not provider:
                        continue
                    provider_stats[provider]["total"] += row.count
                    if row.status == "failed":
                        provider_stats[provider]["failed"] += row.count
                    elif row.status == "pending":
                        provider_stats[provider]["pending"] += row.count
                    elif row.status == "processing":
                        provider_stats[provider]["processing"] += row.count
                    else:
                        provider_stats[provider]["completed"] += row.count

                provider_task_subq = (
                    select(
                        LLMUsage.model_id.label("provider"),
                        LLMUsage.task_id.label("task_id"),
                    )
                    .where(
                        LLMUsage.user_id == self.user.id,
                        time_field >= start,
                        time_field <= end,
                        LLMUsage.model_id.is_not(None),
                        LLMUsage.model_id != "",
                        LLMUsage.task_id.is_not(None),
                    )
                    .distinct()
                    .subquery()
                )
                stage_stmt = (
                    select(
                        provider_task_subq.c.provider,
                        TaskStage.started_at,
                        TaskStage.completed_at,
                    )
                    .join(
                        TaskStage,
                        TaskStage.task_id == provider_task_subq.c.task_id,
                    )
                    .where(
                        TaskStage.stage_type == stage_type,
                        TaskStage.is_active.is_(True),
                        TaskStage.started_at.is_not(None),
                        TaskStage.completed_at.is_not(None),
                    )
                )
                stage_rows = (await self.db.execute(stage_stmt)).all()
                for row in stage_rows:
                    provider = row.provider
                    if not provider:
                        continue
                    durations_by_provider[provider].append(
                        (row.completed_at - row.started_at).total_seconds()
                    )

            service_durations: list[float] = []
            for provider, stats in provider_stats.items():
                durations = durations_by_provider.get(provider, [])
                service_durations.extend(durations)
                total = stats["total"]
                completed = stats["completed"]
                failed = stats["failed"]
                success_rate = (completed / total * 100) if total > 0 else 0.0
                failure_rate = (failed / total * 100) if total > 0 else 0.0
                avg_stage = sum(durations) / len(durations) if durations else 0.0
                median_stage = statistics.median(durations) if durations else 0.0
                usage_by_provider.append(
                    {
                        "service_type": service_type,
                        "provider": provider,
                        "call_count": total,
                        "success_count": completed,
                        "failure_count": failed,
                        "pending_count": stats["pending"],
                        "processing_count": stats["processing"],
                        "success_rate": round(success_rate, 1),
                        "failure_rate": round(failure_rate, 1),
                        "avg_stage_seconds": round(avg_stage, 1),
                        "median_stage_seconds": round(median_stage, 1),
                        "total_audio_duration_seconds": round(
                            duration_map.get(provider, 0.0), 1
                        ),
                    }
                )

            total_calls = sum(stats["total"] for stats in provider_stats.values())
            total_completed = sum(stats["completed"] for stats in provider_stats.values())
            total_failed = sum(stats["failed"] for stats in provider_stats.values())
            total_pending = sum(stats["pending"] for stats in provider_stats.values())
            total_processing = sum(stats["processing"] for stats in provider_stats.values())
            success_rate = (total_completed / total_calls * 100) if total_calls > 0 else 0.0
            failure_rate = (total_failed / total_calls * 100) if total_calls > 0 else 0.0
            avg_stage = sum(service_durations) / len(service_durations) if service_durations else 0.0
            median_stage = statistics.median(service_durations) if service_durations else 0.0
            total_audio_duration = sum(duration_map.values()) if service_type == "asr" else 0.0

            usage_by_service_type.append(
                {
                    "service_type": service_type,
                    "provider": None,
                    "call_count": total_calls,
                    "success_count": total_completed,
                    "failure_count": total_failed,
                    "pending_count": total_pending,
                    "processing_count": total_processing,
                    "success_rate": round(success_rate, 1),
                    "failure_rate": round(failure_rate, 1),
                    "avg_stage_seconds": round(avg_stage, 1),
                    "median_stage_seconds": round(median_stage, 1),
                    "total_audio_duration_seconds": round(total_audio_duration, 1),
                }
            )

        usage_by_provider.sort(
            key=lambda item: (item["service_type"], item["call_count"]), reverse=True
        )

        asr_usage_by_provider = [
            item for item in usage_by_provider if item["service_type"] == "asr"
        ]
        llm_usage_by_provider = [
            item for item in usage_by_provider if item["service_type"] == "llm"
        ]

        return {
            "time_range": {"start": start, "end": end},
            "usage_by_service_type": usage_by_service_type,
            "usage_by_provider": usage_by_provider,
            "asr_usage_by_provider": asr_usage_by_provider,
            "llm_usage_by_provider": llm_usage_by_provider,
        }

    async def get_task_overview(
        self,
        time_range: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        start, end = await self._parse_time_range(time_range, start_date, end_date)

        status_stmt = (
            select(Task.status, func.count(Task.id).label("count"))
            .where(
                Task.user_id == self.user.id,
                Task.created_at >= start,
                Task.created_at <= end,
                Task.deleted_at.is_(None),
            )
            .group_by(Task.status)
        )
        status_result = await self.db.execute(status_stmt)
        status_rows = status_result.all()

        status_distribution = {row.status: row.count for row in status_rows}
        total_tasks = sum(status_distribution.values())

        completed = status_distribution.get("completed", 0)
        failed = status_distribution.get("failed", 0)
        success_rate = (completed / total_tasks * 100) if total_tasks > 0 else 0.0
        failure_rate = (failed / total_tasks * 100) if total_tasks > 0 else 0.0

        time_stmt = (
            select(
                Task.id,
                func.min(TaskStage.started_at).label("first_start"),
                func.max(TaskStage.completed_at).label("last_complete"),
            )
            .join(TaskStage, Task.id == TaskStage.task_id)
            .where(
                Task.user_id == self.user.id,
                Task.created_at >= start,
                Task.created_at <= end,
                Task.deleted_at.is_(None),
                TaskStage.is_active.is_(True),
            )
            .group_by(Task.id)
        )
        time_result = await self.db.execute(time_stmt)
        time_rows = time_result.all()

        processing_times = [
            (row.last_complete - row.first_start).total_seconds()
            for row in time_rows
            if row.first_start and row.last_complete
        ]
        avg_time = sum(processing_times) / len(processing_times) if processing_times else 0.0
        median_time = statistics.median(processing_times) if processing_times else 0.0

        stage_stmt = (
            select(
                TaskStage.stage_type,
                func.avg(
                    func.extract("epoch", TaskStage.completed_at - TaskStage.started_at)
                ).label("avg_seconds"),
            )
            .where(
                TaskStage.task_id.in_(
                    select(Task.id).where(
                        Task.user_id == self.user.id,
                        Task.created_at >= start,
                        Task.created_at <= end,
                        Task.deleted_at.is_(None),
                    )
                ),
                TaskStage.status == "completed",
                TaskStage.is_active.is_(True),
            )
            .group_by(TaskStage.stage_type)
        )
        stage_result = await self.db.execute(stage_stmt)
        stage_rows = stage_result.all()

        processing_time_by_stage = {
            row.stage_type: round(row.avg_seconds or 0.0, 1) for row in stage_rows
        }

        duration_stmt = select(func.sum(Task.duration_seconds)).where(
            Task.user_id == self.user.id,
            Task.created_at >= start,
            Task.created_at <= end,
            Task.deleted_at.is_(None),
        )
        duration_result = await self.db.execute(duration_stmt)
        total_duration = duration_result.scalar_one_or_none() or 0

        return {
            "time_range": {"start": start, "end": end},
            "total_tasks": total_tasks,
            "status_distribution": status_distribution,
            "success_rate": round(success_rate, 1),
            "failure_rate": round(failure_rate, 1),
            "avg_processing_time_seconds": round(avg_time, 1),
            "median_processing_time_seconds": round(median_time, 1),
            "processing_time_by_stage": processing_time_by_stage,
            "total_audio_duration_seconds": float(total_duration),
            "total_audio_duration_formatted": _format_duration(float(total_duration)),
        }
