from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from app.models.transcript import Transcript


@dataclass(frozen=True)
class RagChunkPayload:
    content: str
    start_time: Optional[float]
    end_time: Optional[float]
    speaker_id: Optional[str]
    transcript_id: Optional[str]


def _segment_length(segment: Transcript) -> int:
    return len(segment.content or "")


def _take_overlap(segments: list[Transcript], overlap_chars: int) -> list[Transcript]:
    if overlap_chars <= 0:
        return []
    total = 0
    overlap: list[Transcript] = []
    for segment in reversed(segments):
        total += _segment_length(segment)
        overlap.insert(0, segment)
        if total >= overlap_chars:
            break
    return overlap


def build_rag_chunks(
    transcripts: Iterable[Transcript],
    chunk_size: int,
    chunk_overlap: int,
) -> list[RagChunkPayload]:
    segments = [segment for segment in transcripts if segment.content]
    segments.sort(key=lambda item: item.sequence)

    chunks: list[RagChunkPayload] = []
    current: list[Transcript] = []
    current_len = 0

    for segment in segments:
        current.append(segment)
        current_len += _segment_length(segment)

        if current_len >= chunk_size and current:
            chunks.append(_finalize_chunk(current))
            current = _take_overlap(current, chunk_overlap)
            current_len = sum(_segment_length(seg) for seg in current)

    if current:
        chunks.append(_finalize_chunk(current))

    return chunks


def _finalize_chunk(segments: list[Transcript]) -> RagChunkPayload:
    content = "\n".join(seg.content.strip() for seg in segments if seg.content)
    start_time = segments[0].start_time if segments else None
    end_time = segments[-1].end_time if segments else None
    speaker_ids = {seg.speaker_id for seg in segments if seg.speaker_id}
    speaker_id = segments[0].speaker_id if len(speaker_ids) == 1 else None
    transcript_id = segments[0].id if len(segments) == 1 else None

    return RagChunkPayload(
        content=content,
        start_time=start_time,
        end_time=end_time,
        speaker_id=speaker_id,
        transcript_id=transcript_id,
    )
