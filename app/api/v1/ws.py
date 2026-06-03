from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import get_message
from app.core.security import extract_bearer_token, verify_access_token
from app.db import async_session_factory
from app.i18n.codes import ErrorCode
from app.services.notifications.bus import get_event_bus

router = APIRouter(prefix="/ws")

AUTH_TIMEOUT_SECONDS = 5
CLOSE_CODE_AUTH_TIMEOUT = 4001
CLOSE_CODE_AUTH_FAILED = 4003
CLOSE_CODE_TOKEN_EXPIRED = 4004
HEARTBEAT_INTERVAL_SECONDS = 25


@dataclass
class WsUser:
    id: str
    email: str


def _get_locale(websocket: WebSocket) -> str:
    accept_language = websocket.headers.get("Accept-Language", "zh")
    locale = accept_language.split(",")[0].strip().lower()
    if locale not in {"zh", "en"}:
        locale = "zh"
    return locale


async def _send_error(websocket: WebSocket, code: ErrorCode, locale: str, trace_id: str) -> None:
    message = get_message(code, locale)
    payload = {"code": code.value, "message": message, "data": None, "traceId": trace_id}
    await websocket.send_text(json.dumps(payload, ensure_ascii=False))


async def _send_ok(websocket: WebSocket, message: str, data: dict[str, object], trace_id: str) -> None:
    payload = {"code": 0, "message": message, "data": data, "traceId": trace_id}
    await websocket.send_text(json.dumps(payload, ensure_ascii=False))


def _get_close_code(error_code: ErrorCode) -> int:
    if error_code == ErrorCode.AUTH_TOKEN_EXPIRED:
        return CLOSE_CODE_TOKEN_EXPIRED
    return CLOSE_CODE_AUTH_FAILED


async def _authenticate_token(
    token: str, session: AsyncSession, locale: str, trace_id: str
) -> tuple[WsUser | None, ErrorCode | None]:
    try:
        auth_user = await verify_access_token(token)
    except Exception as exc:
        if hasattr(exc, "code"):
            return None, exc.code
        return None, ErrorCode.AUTH_TOKEN_INVALID

    user_id = auth_user.sub
    if not user_id:
        return None, ErrorCode.AUTH_TOKEN_INVALID

    return WsUser(id=user_id, email=auth_user.email), None


async def _authenticate_header(
    websocket: WebSocket, session: AsyncSession, locale: str, trace_id: str
) -> tuple[WsUser | None, ErrorCode | None]:
    authorization = websocket.headers.get("Authorization")
    try:
        token = extract_bearer_token(authorization)
    except Exception as exc:
        if hasattr(exc, "code"):
            return None, exc.code
        return None, ErrorCode.AUTH_TOKEN_INVALID
    return await _authenticate_token(token, session, locale, trace_id)


async def _authenticate_in_band(
    websocket: WebSocket, session: AsyncSession, locale: str, trace_id: str
) -> tuple[WsUser | None, ErrorCode | None]:
    try:
        raw_message = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except TimeoutError:
        return None, ErrorCode.AUTH_TOKEN_NOT_PROVIDED

    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError:
        return None, ErrorCode.AUTH_TOKEN_INVALID

    if not isinstance(message, dict) or message.get("type") != "authenticate":
        return None, ErrorCode.AUTH_TOKEN_INVALID

    token = message.get("token")
    if not isinstance(token, str) or not token:
        return None, ErrorCode.AUTH_TOKEN_INVALID

    return await _authenticate_token(token, session, locale, trace_id)


async def _forward_pubsub(websocket: WebSocket, user_id: str) -> None:
    """订阅用户全局频道（EventBus），把信封原样转发给 WS；同时周期发心跳 ping。

    EventBus.subscribe 返回同步 redis pubsub（worker 侧 publish 也用同步 redis），
    其 get_message 阻塞，故放进 asyncio.to_thread 以免阻塞事件循环。
    """
    pubsub = get_event_bus().subscribe(user_id)
    last_ping = 0.0
    try:
        while True:
            message = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                if isinstance(data, str):
                    await websocket.send_text(data)
            now = asyncio.get_event_loop().time()
            if now - last_ping >= HEARTBEAT_INTERVAL_SECONDS:
                await websocket.send_text(json.dumps({"kind": "ping"}))
                last_ping = now
            await asyncio.sleep(0.05)
    finally:
        await asyncio.to_thread(pubsub.unsubscribe)
        await asyncio.to_thread(pubsub.close)


@router.websocket("/user")
async def user_updates(websocket: WebSocket) -> None:
    """
    Global WebSocket endpoint for user-level updates.
    Subscribes to all task updates and notifications for the authenticated user.
    """
    await websocket.accept()
    locale = _get_locale(websocket)
    trace_id = uuid4().hex
    async with async_session_factory() as session:
        user, error_code = await _authenticate_header(websocket, session, locale, trace_id)
        if user is None:
            user, error_code = await _authenticate_in_band(websocket, session, locale, trace_id)
            if user is None:
                if error_code == ErrorCode.AUTH_TOKEN_NOT_PROVIDED:
                    await websocket.close(code=CLOSE_CODE_AUTH_TIMEOUT)
                    return
                await _send_error(websocket, error_code, locale, trace_id)
                await websocket.close(code=_get_close_code(error_code))
                return
        await _send_ok(
            websocket,
            "authenticated",
            {"type": "authenticated", "user_id": user.id},
            trace_id,
        )

    # Forward both kinds (notification + task_progress) via the EventBus seam.
    forward_task = asyncio.create_task(_forward_pubsub(websocket, user.id))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        forward_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forward_task
