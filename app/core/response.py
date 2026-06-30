from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional
from uuid import uuid4

from fastapi.responses import JSONResponse

DataPayload = Optional[object]

_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(trace_id: str) -> Token[str | None]:
    return _request_id_ctx.set(trace_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id_ctx.reset(token)


def get_request_id() -> str:
    trace_id = _request_id_ctx.get()
    if trace_id:
        return trace_id
    return uuid4().hex


def _build_response(
    code: int,
    message: str,
    data: DataPayload,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    trace_id = get_request_id()
    return JSONResponse(
        {
            "code": code,
            "message": message,
            "data": data,
            "traceId": trace_id,
        },
        status_code=status_code,
        headers=headers,
    )


def success(data: DataPayload = None, message: str = "成功") -> JSONResponse:
    return _build_response(0, message, data)


def error(
    code: int,
    message: str,
    data: DataPayload = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build an error envelope.

    ``status_code`` defaults to 200 (the unified-response contract the frontend
    api-client parses by the ``code`` field). Pass a real HTTP status for
    responses consumed directly by the browser (e.g. the media byte-stream
    proxy) or for rate limiting (real 429). ``headers`` lets callers attach
    response headers such as ``Retry-After``.
    """
    return _build_response(code, message, data, status_code, headers)
