"""任务阶段管理服务"""

from datetime import datetime
from typing import Optional

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_stages import (
    RETRY_MODE_CLEAR_STAGES,
    RETRY_MODE_START_STAGE,
    RetryMode,
    StageStatus,
    StageType,
    get_stage_flow,
)
from app.models.summary import Summary
from app.models.task import Task
from app.models.task_stage import TaskStage
from app.models.transcript import Transcript


class TaskStageService:
    """任务阶段服务"""

    @staticmethod
    async def initialize_stages(db: AsyncSession, task: Task) -> None:
        """初始化任务的所有阶段（创建 pending 状态的阶段记录）"""
        stage_flow = get_stage_flow(task.source_type)

        for stage_type in stage_flow:
            stage = TaskStage(
                task_id=task.id,
                stage_type=stage_type.value,
                status=StageStatus.PENDING.value,
                is_active=True,
                attempt=1,
            )
            db.add(stage)

        await db.flush()

    @staticmethod
    async def get_active_stages(db: AsyncSession, task_id: str) -> list[TaskStage]:
        """获取任务的所有活跃阶段"""
        result = await db.execute(
            select(TaskStage)
            .where(
                and_(
                    TaskStage.task_id == task_id,
                    TaskStage.is_active == True,  # noqa: E712
                )
            )
            .order_by(TaskStage.created_at)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_stage(
        db: AsyncSession, task_id: str, stage_type: StageType
    ) -> Optional[TaskStage]:
        """获取指定类型的活跃阶段"""
        result = await db.execute(
            select(TaskStage).where(
                and_(
                    TaskStage.task_id == task_id,
                    TaskStage.stage_type == stage_type.value,
                    TaskStage.is_active == True,  # noqa: E712
                )
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def start_stage(db: AsyncSession, task_id: str, stage_type: StageType) -> TaskStage:
        """开始执行某个阶段"""
        stage = await TaskStageService.get_stage(db, task_id, stage_type)
        if not stage:
            # 如果阶段不存在，创建新的
            stage = TaskStage(
                task_id=task_id,
                stage_type=stage_type.value,
                status=StageStatus.PROCESSING.value,
                is_active=True,
                started_at=datetime.utcnow(),
                attempt=1,
            )
            db.add(stage)
        else:
            stage.status = StageStatus.PROCESSING.value
            stage.started_at = datetime.utcnow()

        await db.flush()
        return stage

    @staticmethod
    async def complete_stage(
        db: AsyncSession,
        task_id: str,
        stage_type: StageType,
        metadata: Optional[dict] = None,
    ) -> TaskStage:
        """标记阶段为完成"""
        stage = await TaskStageService.get_stage(db, task_id, stage_type)
        if not stage:
            raise ValueError(f"Stage {stage_type} not found for task {task_id}")

        stage.status = StageStatus.COMPLETED.value
        stage.completed_at = datetime.utcnow()
        if metadata:
            stage.stage_metadata = {**stage.stage_metadata, **metadata}

        await db.flush()
        return stage

    @staticmethod
    async def fail_stage(
        db: AsyncSession,
        task_id: str,
        stage_type: StageType,
        error_code: int,
        error_message: str,
    ) -> TaskStage:
        """标记阶段为失败"""
        stage = await TaskStageService.get_stage(db, task_id, stage_type)
        if not stage:
            raise ValueError(f"Stage {stage_type} not found for task {task_id}")

        stage.status = StageStatus.FAILED.value
        stage.completed_at = datetime.utcnow()
        stage.error_code = error_code
        stage.error_message = error_message

        await db.flush()
        return stage

    @staticmethod
    async def skip_stage(
        db: AsyncSession,
        task_id: str,
        stage_type: StageType,
        reason: str = "Reusing previous result",
    ) -> TaskStage:
        """跳过阶段（复用之前的结果）"""
        stage = await TaskStageService.get_stage(db, task_id, stage_type)
        if not stage:
            raise ValueError(f"Stage {stage_type} not found for task {task_id}")

        stage.status = StageStatus.SKIPPED.value
        stage.completed_at = datetime.utcnow()
        stage.stage_metadata = {**stage.stage_metadata, "skip_reason": reason}

        await db.flush()
        return stage

    @staticmethod
    async def prepare_retry(db: AsyncSession, task: Task, retry_mode: RetryMode) -> StageType:
        """
        准备重试：根据重试模式清理阶段状态
        返回应该开始执行的阶段
        """
        stages = await TaskStageService.get_active_stages(db, task.id)
        stage_flow = get_stage_flow(task.source_type)

        # 确定要清空的阶段
        clear_stages = RETRY_MODE_CLEAR_STAGES[retry_mode]

        if clear_stages == "all":
            # 完整重试：将所有阶段标记为 inactive，创建新的 pending 阶段
            for stage in stages:
                stage.is_active = False

            # 重新初始化所有阶段
            await TaskStageService.initialize_stages(db, task)
            start_stage = stage_flow[0]

        elif clear_stages == "from_failed":
            # 智能重试：找到第一个失败的阶段，清空它及后续阶段
            failed_stage_idx = None
            for idx, stage_type in enumerate(stage_flow):
                stage = await TaskStageService.get_stage(db, task.id, stage_type)
                if stage and stage.status == StageStatus.FAILED.value:
                    failed_stage_idx = idx
                    break

            if failed_stage_idx is None:
                # 没有找到失败阶段，默认从第一个开始
                start_stage = stage_flow[0]
            else:
                # 清空失败阶段及后续阶段
                for stage_type in stage_flow[failed_stage_idx:]:
                    stage = await TaskStageService.get_stage(db, task.id, stage_type)
                    if stage:
                        stage.is_active = False

                    # 创建新的 pending 阶段
                    new_stage = TaskStage(
                        task_id=task.id,
                        stage_type=stage_type.value,
                        status=StageStatus.PENDING.value,
                        is_active=True,
                        attempt=(stage.attempt + 1) if stage else 1,
                    )
                    db.add(new_stage)

                start_stage = stage_flow[failed_stage_idx]

        else:
            # 显式指定要清空的阶段列表
            if isinstance(clear_stages, str):
                raise ValueError(f"Invalid retry mode stages: {clear_stages}")
            for stage_type in clear_stages:
                stage = await TaskStageService.get_stage(db, task.id, stage_type)
                if stage:
                    stage.is_active = False

                # 创建新的 pending 阶段
                new_stage = TaskStage(
                    task_id=task.id,
                    stage_type=stage_type.value,
                    status=StageStatus.PENDING.value,
                    is_active=True,
                    attempt=(stage.attempt + 1) if stage else 1,
                )
                db.add(new_stage)

            # 确定起始阶段
            start_stage = RETRY_MODE_START_STAGE[retry_mode]
            if start_stage is None:
                start_stage = clear_stages[0]

        await db.flush()
        return start_stage

    @staticmethod
    async def should_execute_stage(db: AsyncSession, task_id: str, stage_type: StageType) -> bool:
        """
        判断是否应该执行某个阶段
        如果阶段已完成或已跳过，返回 False
        """
        stage = await TaskStageService.get_stage(db, task_id, stage_type)
        if not stage:
            return True  # 阶段不存在，应该执行

        return stage.status not in [
            StageStatus.COMPLETED.value,
            StageStatus.SKIPPED.value,
        ]

    @staticmethod
    async def clean_stage_artifacts(db: AsyncSession, task: Task, stage_type: StageType) -> None:
        """
        清理阶段产生的数据和文件
        用于显式重试某个阶段时，清理旧数据
        """
        if stage_type == StageType.UPLOAD_STORAGE:
            # 清理上传相关数据
            task.source_key = None
            task.duration_seconds = None
            task.detected_language = None

        elif stage_type == StageType.TRANSCRIBE:
            # 清理转写数据
            await db.execute(delete(Transcript).where(Transcript.task_id == task.id))
            task.detected_language = None

        elif stage_type == StageType.SUMMARIZE:
            # 清理摘要数据
            await db.execute(
                delete(Summary).where(
                    and_(Summary.task_id == task.id, Summary.is_active == True)  # noqa: E712
                )
            )

        await db.flush()
