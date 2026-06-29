"""管理后台「看用户任务」出参 schema(/api/v1/admin/users/{id}/tasks)。

仅列表项需要新形状:在私有 TaskListItem 基础上多透出 channel_title(频道名)
与 error_message(失败原因,排障)。详情/转写/摘要复用既有 schema。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AdminUserTaskItem(BaseModel):
    id: str
    title: str | None
    source_type: str
    status: str
    progress: int
    duration_seconds: int | None
    created_at: datetime
    channel_title: str | None = None  # YouTube 频道名;非 youtube 任务为 None
    error_message: str | None = None  # 失败任务原因
