"""前端未捕获错误上报体(P3-3)。

各字段在校验后**截断**而非拒绝——这是尽力而为的遥测,不该因某个字段超长就丢掉整份
错误报告。整体 body 大小另由端点的 content-length 守卫兜底(防超大 payload 灌爆内存)。
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator

_MAX_MESSAGE = 2000
_MAX_STACK = 8000
_MAX_URL = 2000
_MAX_SOURCE = 64
_MAX_DIGEST = 128
_MAX_RELEASE = 128


class ClientErrorReport(BaseModel):
    """前端 error boundary / window.onerror / unhandledrejection 的上报载荷。"""

    message: str
    source: str | None = None  # error_boundary | window.onerror | unhandledrejection
    stack: str | None = None
    url: str | None = None  # 出错时的页面 URL
    digest: str | None = None  # Next.js error boundary 的 error.digest(用于关联服务端日志)
    release: str | None = None  # 构建标识,用于定位是哪个部署版本出错

    @model_validator(mode="after")
    def _truncate_fields(self) -> ClientErrorReport:
        self.message = self.message[:_MAX_MESSAGE]
        if self.source is not None:
            self.source = self.source[:_MAX_SOURCE]
        if self.stack is not None:
            self.stack = self.stack[:_MAX_STACK]
        if self.url is not None:
            self.url = self.url[:_MAX_URL]
        if self.digest is not None:
            self.digest = self.digest[:_MAX_DIGEST]
        if self.release is not None:
            self.release = self.release[:_MAX_RELEASE]
        return self
