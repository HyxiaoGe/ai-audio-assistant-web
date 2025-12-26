from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import get_message
from app.core.redis import get_redis_client
from app.core.security import decode_access_token, extract_bearer_token
from app.i18n.codes import ErrorCode
from app.models.task import Task
from app.models.user import User
from app.db import async_session_factory

router = APIRouter(prefix="/ws")


def _get_locale(websocket: WebSocket) -> str:
    accept_language = websocket.headers.get("Accept-Language", "zh")
    locale = accept_language.split(",")[0].strip().lower()
    if locale not in {"zh", "en"}:
        locale = "zh"
    return locale


async def _send_error(
    websocket: WebSocket, code: ErrorCode, locale: str, trace_id: str
) -> None:
    message = get_message(code, locale)
    payload = {"code": code.value, "message": message, "data": None, "traceId": trace_id}
    await websocket.send_text(json.dumps(payload, ensure_ascii=False))


async def _authenticate(
    websocket: WebSocket, session: AsyncSession, locale: str, trace_id: str
) -> Optional[User]:
    authorization = websocket.headers.get("Authorization")
    try:
        token = extract_bearer_token(authorization)
        payload = decode_access_token(token)
    except Exception as exc:
        if hasattr(exc, "code"):
            await _send_error(websocket, exc.code, locale, trace_id)
        else:
            await _send_error(websocket, ErrorCode.AUTH_TOKEN_INVALID, locale, trace_id)
        return None

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        await _send_error(websocket, ErrorCode.AUTH_TOKEN_INVALID, locale, trace_id)
        return None

    result = await session.execute(
        select(User).where(User.id == subject, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None:
        await _send_error(websocket, ErrorCode.USER_NOT_FOUND, locale, trace_id)
        return None
    return user


async def _forward_pubsub(websocket: WebSocket, channel: str) -> None:
    client = get_redis_client()
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message.get("type") == "message":
                data = message.get("data")
                if isinstance(data, str):
                    await websocket.send_text(data)
            await asyncio.sleep(0.05)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()


@router.websocket("/tasks/{task_id}")
async def task_progress(websocket: WebSocket, task_id: str) -> None:
    await websocket.accept()
    locale = _get_locale(websocket)
    trace_id = uuid4().hex
    async with async_session_factory() as session:
        user = await _authenticate(websocket, session, locale, trace_id)
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        result = await session.execute(
            select(Task).where(
                Task.id == task_id,
                Task.user_id == user.id,
                Task.deleted_at.is_(None),
            )
        )
        task = result.scalar_one_or_none()
        if task is None:
            await _send_error(websocket, ErrorCode.TASK_NOT_FOUND, locale, trace_id)
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    channel = f"tasks:{task_id}"
    forward_task = asyncio.create_task(_forward_pubsub(websocket, channel))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        forward_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forward_task
