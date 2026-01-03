from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional


@dataclass(frozen=True)
class TranscriptSegment:
    speaker_id: Optional[str]
    start_time: float
    end_time: float
    content: str
    confidence: Optional[float]


class ASRService(ABC):
    @abstractmethod
    async def transcribe(
        self,
        audio_url: str,
        status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> list[TranscriptSegment]:
        raise NotImplementedError
