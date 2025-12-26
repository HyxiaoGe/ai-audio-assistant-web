from __future__ import annotations

from abc import ABC, abstractmethod


class LLMService(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def summarize(self, text: str, style: str) -> str:
        raise NotImplementedError
