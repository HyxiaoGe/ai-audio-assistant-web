from __future__ import annotations

from app.config import settings
from app.services.llm.base import LLMService
from app.services.llm.doubao import DoubaoLLMService
from app.services.llm.qwen import QwenLLMService


def get_llm_service() -> LLMService:
    provider = settings.LLM_PROVIDER
    if provider == "doubao":
        return DoubaoLLMService()
    if provider == "qwen":
        return QwenLLMService()
    raise RuntimeError("LLM_PROVIDER is not set or unsupported")
