from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

_current_user_id: ContextVar[Optional[str]] = ContextVar("current_user_id", default=None)


def set_current_user_id(user_id: str) -> Token[Optional[str]]:
    return _current_user_id.set(user_id)


def reset_current_user_id(token: Token[Optional[str]]) -> None:
    _current_user_id.reset(token)


def get_current_user_id() -> Optional[str]:
    return _current_user_id.get()
