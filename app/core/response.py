from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional
from uuid import uuid4

from fastapi.responses import JSONResponse


DataPayload = Optional[object]

_request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def set_request_id(trace_id: str) -> Token[Optional[str]]:
    return _request_id_ctx.set(trace_id)


def reset_request_id(token: Token[Optional[str]]) -> None:
    _request_id_ctx.reset(token)


def get_request_id() -> str:
    trace_id = _request_id_ctx.get()
    if trace_id:
        return trace_id
    return uuid4().hex


def _build_response(code: int, message: str, data: DataPayload) -> JSONResponse:
    trace_id = get_request_id()
    return JSONResponse(
        {
            "code": code,
            "message": message,
            "data": data,
            "traceId": trace_id,
        }
    )


def success(data: DataPayload = None, message: str = "成功") -> JSONResponse:
    return _build_response(0, message, data)


def error(code: int, message: str, data: DataPayload = None) -> JSONResponse:
    return _build_response(code, message, data)
