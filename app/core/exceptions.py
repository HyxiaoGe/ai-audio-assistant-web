from __future__ import annotations

from app.i18n.codes import ErrorCode


class BusinessError(Exception):
    def __init__(self, code: ErrorCode, **kwargs: str) -> None:
        super().__init__(str(code))
        self.code = code
        self.kwargs = kwargs
