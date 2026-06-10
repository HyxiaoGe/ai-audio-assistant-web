from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.response import reset_request_id, set_request_id
from app.core.security import extract_bearer_token, get_jwt_validator
from app.core.user_context import reset_current_user_id, set_current_user_id

logger = logging.getLogger("app.middleware")


class PathExcludingGZipMiddleware:
    """按路径前缀跳过压缩的 GZip 薄封装(starlette 原生 GZipMiddleware 不支持按路径排除)。

    排除路径(流式媒体代理:/api/v1/media、/api/v1/summaries/images)直接透传内层 app:
    这些端点转发 Range 请求/206 分段字节,gzip 会让 Content-Range/Content-Length 语义错位,
    且音频/WebP 本身已是压缩格式,再压无益。其余请求委托给标准 GZipMiddleware 实例。

    SSE 无需在此额外处理:锁定的 starlette 0.50.0(见 uv.lock)GZipMiddleware 已默认按
    content-type 排除 text/event-stream(starlette/middleware/gzip.py 的
    DEFAULT_EXCLUDED_CONTENT_TYPES),摘要对比 /stream 等 SSE 端点不会被缓冲压缩。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        exclude_path_prefixes: tuple[str, ...],
        minimum_size: int = 1024,
    ) -> None:
        self.app = app
        self.exclude_path_prefixes = exclude_path_prefixes
        self._gzip_app = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith(self.exclude_path_prefixes):
            await self.app(scope, receive, send)
            return
        await self._gzip_app(scope, receive, send)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        header_request_id = request.headers.get("X-Request-Id")
        trace_id = header_request_id.strip() if header_request_id else uuid4().hex
        request.state.trace_id = trace_id
        token = set_request_id(trace_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-Id"] = trace_id
        return response


class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        accept_language = request.headers.get("Accept-Language", "zh")
        # 提取第一个语言标签（逗号前的部分）
        locale = accept_language.split(",")[0].strip().lower()
        # 提取语言前缀（如 zh-CN -> zh, en-US -> en）
        lang = locale.split("-")[0]
        # 只支持 zh 和 en
        if lang not in {"zh", "en"}:
            lang = "zh"
        request.state.locale = lang
        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        trace_id = getattr(request.state, "trace_id", "")
        logger.info(
            "request %s %s status=%s duration_ms=%s trace_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            trace_id,
        )
        return response


class UserContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        authorization = request.headers.get("Authorization")
        token_handle = None
        if authorization:
            try:
                token = extract_bearer_token(authorization)
                validator = get_jwt_validator()
                auth_user = await validator.verify_async(token)
                if auth_user.sub:
                    token_handle = set_current_user_id(auth_user.sub)
            except Exception:
                token_handle = None
        try:
            return await call_next(request)
        finally:
            if token_handle is not None:
                reset_current_user_id(token_handle)
