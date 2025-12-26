from __future__ import annotations

from typing import Optional

import httpx

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.llm.base import LLMService


_SUMMARY_PROMPTS = {
    "overview": "请为以下转写文本生成一段简洁的内容概述（200字以内）。",
    "key_points": "请从以下转写文本中提取 5-10 个关键要点，用列表形式输出。",
    "action_items": "请从以下转写文本中提取待办事项/行动项，用列表形式输出。",
}


class DoubaoLLMService(LLMService):
    def __init__(self) -> None:
        api_key = settings.DOUBAO_API_KEY
        base_url = settings.DOUBAO_BASE_URL
        model = settings.DOUBAO_MODEL
        max_tokens = settings.DOUBAO_MAX_TOKENS
        if not api_key or not base_url or not model or not max_tokens:
            raise RuntimeError("Doubao settings are not set")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model_name(self) -> str:
        return self._model

    async def summarize(self, text: str, style: str) -> str:
        if not text:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="text")
        prompt = _SUMMARY_PROMPTS.get(style)
        if prompt is None:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="summary_type")

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": "你是一个专业的中文会议纪要助手。"},
                {"role": "user", "content": f"{prompt}\n\n{text}"},
            ],
            "max_tokens": self._max_tokens,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=60.0) as client:
                response = await client.post("/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not isinstance(content, str) or not content.strip():
                raise BusinessError(ErrorCode.AI_SUMMARY_GENERATION_FAILED, reason="empty response")
            return content.strip()
        except httpx.HTTPError as exc:
            raise BusinessError(ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE, reason=str(exc)) from exc
