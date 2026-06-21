"""前端未捕获错误上报体(P3-3)。

各字段在校验后**截断**而非拒绝——这是尽力而为的遥测,不该因某个字段超长就丢掉整份
错误报告。整体 body 大小另由端点的 content-length 守卫兜底(防超大 payload 灌爆内存)。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, model_validator

_MAX_MESSAGE = 2000
_MAX_STACK = 8000
_MAX_URL = 2000
_MAX_SOURCE = 64
_MAX_DIGEST = 128
_MAX_RELEASE = 128

# C0 控制符 + DEL(含 \r \n \t)。这些字段会进 logger 的 %s——换行/回车会被攻击者用来伪造
# 日志行(被 Kuma/Feishu 日志扫描栈误当真事件),必须在落日志前清洗掉,绝不能只截断。
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean(value: str, limit: int) -> str:
    """清洗控制字符(防日志注入)后截断;清洗是 1:1 字符替换,不影响长度上界。"""
    return _CONTROL_CHARS.sub(" ", value)[:limit]


class ClientErrorReport(BaseModel):
    """前端 error boundary / window.onerror / unhandledrejection 的上报载荷。"""

    message: str
    source: str | None = None  # error_boundary | window.onerror | unhandledrejection
    stack: str | None = None
    url: str | None = None  # 出错时的页面 URL
    digest: str | None = None  # Next.js error boundary 的 error.digest(用于关联服务端日志)
    release: str | None = None  # 构建标识,用于定位是哪个部署版本出错

    @model_validator(mode="after")
    def _sanitize_and_truncate(self) -> ClientErrorReport:
        self.message = _clean(self.message, _MAX_MESSAGE)
        if self.source is not None:
            self.source = _clean(self.source, _MAX_SOURCE)
        if self.stack is not None:
            self.stack = _clean(self.stack, _MAX_STACK)
        if self.url is not None:
            self.url = _clean(self.url, _MAX_URL)
        if self.digest is not None:
            self.digest = _clean(self.digest, _MAX_DIGEST)
        if self.release is not None:
            self.release = _clean(self.release, _MAX_RELEASE)
        return self
