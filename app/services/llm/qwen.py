from __future__ import annotations

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.llm.base import LLMService


class QwenLLMService(LLMService):
    def __init__(self) -> None:
        api_key = settings.QWEN_API_KEY
        model = settings.QWEN_MODEL
        if not api_key or not model:
            raise RuntimeError("QWEN settings are not set")
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def summarize(self, text: str, style: str) -> str:
        if not text:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="text")
        raise BusinessError(ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE)
