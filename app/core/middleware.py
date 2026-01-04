from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.response import reset_request_id, set_request_id

logger = logging.getLogger("app.middleware")


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
