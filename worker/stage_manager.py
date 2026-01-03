"""Worker 阶段管理器

提供阶段级别的执行控制和错误处理
"""

import logging
from contextlib import contextmanager
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.core.task_stages import StageStatus, StageType
from app.models.task import Task
from app.models.task_stage import TaskStage
from worker.db import get_sync_db_session

logger = logging.getLogger(__name__)


class StageManager:
    """阶段管理器"""

    def __init__(self, task_id: str, request_id: Optional[str] = None):
        self.task_id = task_id
        self.request_id = request_id

    def should_execute(self, session: Session, stage_type: StageType) -> bool:
        """判断是否应该执行某个阶段"""
        stage = self._get_stage(session, stage_type)
        if not stage:
            return True  # 阶段不存在，应该执行

        # 如果阶段已完成或已跳过，不执行
        return stage.status not in [
            StageStatus.COMPLETED.value,
            StageStatus.SKIPPED.value,
        ]

    def start_stage(self, session: Session, stage_type: StageType) -> None:
        """开始执行阶段"""
        from datetime import datetime

        stage = self._get_stage(session, stage_type)
        if not stage:
            # 创建新阶段
            task = session.query(Task).filter(Task.id == self.task_id).first()
            if not task:
                return

            stage = TaskStage(
                task_id=self.task_id,
                stage_type=stage_type.value,
                status=StageStatus.PROCESSING.value,
                is_active=True,
                started_at=datetime.utcnow(),
                attempt=1,
            )
            session.add(stage)
        else:
            stage.status = StageStatus.PROCESSING.value
            stage.started_at = datetime.utcnow()

        session.commit()
        logger.info(
            f"[{self.request_id}] Stage {stage_type.value} started for task {self.task_id}"
        )

    def complete_stage(
        self, session: Session, stage_type: StageType, metadata: Optional[dict] = None
    ) -> None:
        """标记阶段为完成"""
        from datetime import datetime

        stage = self._get_stage(session, stage_type)
        if not stage:
            logger.warning(
                f"[{self.request_id}] Stage {stage_type.value} not found for task {self.task_id}"
            )
            return

        stage.status = StageStatus.COMPLETED.value
        stage.completed_at = datetime.utcnow()
        if metadata:
            stage.stage_metadata = {**stage.stage_metadata, **metadata}

        session.commit()
        logger.info(
            f"[{self.request_id}] Stage {stage_type.value} completed for task {self.task_id}"
        )

    def fail_stage(
        self, session: Session, stage_type: StageType, error_code: int, error_message: str
    ) -> None:
        """标记阶段为失败"""
        from datetime import datetime

        stage = self._get_stage(session, stage_type)
        if not stage:
            logger.warning(
                f"[{self.request_id}] Stage {stage_type.value} not found for task {self.task_id}"
            )
            return

        stage.status = StageStatus.FAILED.value
        stage.completed_at = datetime.utcnow()
        stage.error_code = error_code
        stage.error_message = error_message

        session.commit()
        logger.error(
            f"[{self.request_id}] Stage {stage_type.value} failed for task {self.task_id}: {error_message}"
        )

    def skip_stage(
        self, session: Session, stage_type: StageType, reason: str = "Reusing previous result"
    ) -> None:
        """跳过阶段（复用之前的结果）"""
        from datetime import datetime

        stage = self._get_stage(session, stage_type)
        if not stage:
            logger.warning(
                f"[{self.request_id}] Stage {stage_type.value} not found for task {self.task_id}"
            )
            return

        stage.status = StageStatus.SKIPPED.value
        stage.completed_at = datetime.utcnow()
        stage.stage_metadata = {**stage.stage_metadata, "skip_reason": reason}

        session.commit()
        logger.info(
            f"[{self.request_id}] Stage {stage_type.value} skipped for task {self.task_id}: {reason}"
        )

    @contextmanager
    def execute_stage(
        self,
        stage_type: StageType,
        task: Task,
        check_artifacts: Optional[Callable[[Task], bool]] = None,
    ):
        """
        执行阶段的上下文管理器

        Args:
            stage_type: 阶段类型
            task: 任务对象
            check_artifacts: 检查阶段产物是否存在的函数，返回 True 表示存在

        Usage:
            with stage_manager.execute_stage(StageType.DOWNLOAD, task,
                check_artifacts=lambda t: t.source_key is not None) as should_execute:
                if should_execute:
                    # 执行下载逻辑
                    download_file()
        """
        with get_sync_db_session() as session:
            # 检查是否应该执行
            if not self.should_execute(session, stage_type):
                logger.info(
                    f"[{self.request_id}] Stage {stage_type.value} already completed, skipping"
                )
                yield False
                return

            # 检查产物是否存在（用于复用）
            if check_artifacts and check_artifacts(task):
                self.skip_stage(session, stage_type, "Artifacts already exist")
                yield False
                return

            # 开始执行
            self.start_stage(session, stage_type)

        try:
            yield True  # 应该执行
        except Exception as exc:
            # 失败处理
            with get_sync_db_session() as session:
                from app.core.exceptions import BusinessError

                if isinstance(exc, BusinessError):
                    self.fail_stage(session, stage_type, exc.code, exc.message)
                else:
                    self.fail_stage(session, stage_type, 50000, str(exc))
            raise

        # 成功完成（由调用方调用 complete_stage）

    def _get_stage(self, session: Session, stage_type: StageType) -> Optional[TaskStage]:
        """获取指定类型的活跃阶段"""
        return (
            session.query(TaskStage)
            .filter(
                TaskStage.task_id == self.task_id,
                TaskStage.stage_type == stage_type.value,
                TaskStage.is_active == True,  # noqa: E712
            )
            .first()
        )
