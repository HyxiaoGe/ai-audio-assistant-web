"""匿名前端错误上报端点(P3-3)——log-only sink。

前端 error boundary / window.onerror / unhandledrejection 把未捕获错误 POST 到这里,后端
只把它落进结构化日志(由 P3-1 注入 trace_id、由既有 Kuma/Feishu 日志扫描栈捕获)。刻意**不**
发飞书 webhook、不存库、不引新密钥——避免引入新的运维面/告警噪声。

防滥用三层:匿名但按 IP 限流(挡日志刷屏)、字段截断(见 schema)、body 大小守卫(挡超大 payload
在解析前就拒绝)。匿名是必须的——错误常发生在登录前/登录页,带鉴权就收不到这些最关键的报告。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.rate_limit import rate_limit_by_ip
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.schemas.client_error import ClientErrorReport

logger = logging.getLogger("app.api.client_errors")

router = APIRouter(tags=["client-errors"])

# 单请求 body 上限:正常上报(message+stack 截断后)远小于此,超过即视作滥用/异常。
_MAX_BODY_BYTES = 64 * 1024
_MAX_UA_LEN = 256

_rate_limit = rate_limit_by_ip(limit=settings.RATE_LIMIT_CLIENT_ERRORS_PER_MIN, scope="client_errors")


async def _guard_body_size(request: Request) -> None:
    """据 Content-Length 在解析 body 前挡掉超大上报(依赖先于 body 参数解析,故不会先读进内存)。"""
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        size = int(raw)
    except ValueError:
        return
    if size > _MAX_BODY_BYTES:
        raise BusinessError(ErrorCode.INVALID_PARAMETER, reason="payload too large")


@router.post("/client-errors")
async def report_client_error(
    report: ClientErrorReport,
    request: Request,
    _rl: None = Depends(_rate_limit),
    _sz: None = Depends(_guard_body_size),
) -> JSONResponse:
    """接收一份前端错误报告,仅落结构化日志(WARNING——是客户端故障,非服务端 5xx)。"""
    user_agent = request.headers.get("user-agent", "")[:_MAX_UA_LEN]
    logger.warning(
        "client error report: source=%s url=%s digest=%s release=%s ua=%s message=%s stack=%s",
        report.source,
        report.url,
        report.digest,
        report.release,
        user_agent,
        report.message,
        report.stack,
    )
    return success(data={"received": True})
