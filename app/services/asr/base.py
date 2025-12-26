from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TranscriptSegment:
    speaker_id: Optional[str]
    start_time: float
    end_time: float
    content: str
    confidence: Optional[float]


class ASRService(ABC):
    @abstractmethod
    async def transcribe(self, audio_url: str) -> list[TranscriptSegment]:
        raise NotImplementedError
