from __future__ import annotations

import logging
from typing import Iterable, Optional

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import settings
from app.models.llm_usage import LLMUsage
from app.models.rag_chunk import RagChunk
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.rag.chunking import RagChunkPayload, build_rag_chunks
from app.services.rag.embedder import EmbeddingClient

logger = logging.getLogger("rag.ingest")


async def ingest_task_chunks_async(
    session: AsyncSession,
    task: Task,
    transcripts: Iterable[Transcript],
    user_id: str,
) -> None:
    chunks = _build_chunks(transcripts)
    if not chunks:
        return

    await session.execute(delete(RagChunk).where(RagChunk.task_id == task.id))
    embedder = EmbeddingClient()
    embeddings, embed_model, embed_dim, usage_status = await _embed_chunks_async(embedder, chunks)
    _add_rag_chunks(session, task, user_id, chunks, embeddings, embed_model, embed_dim)
    _record_embedding_usage(session, task, user_id, embedder, usage_status)
    await session.commit()


def ingest_task_chunks_sync(
    session: Session,
    task: Task,
    transcripts: Iterable[Transcript],
    user_id: str,
) -> None:
    chunks = _build_chunks(transcripts)
    if not chunks:
        return

    session.execute(delete(RagChunk).where(RagChunk.task_id == task.id))
    embedder = EmbeddingClient()
    embeddings, embed_model, embed_dim, usage_status = _embed_chunks_sync(embedder, chunks)
    _add_rag_chunks(session, task, user_id, chunks, embeddings, embed_model, embed_dim)
    _record_embedding_usage(session, task, user_id, embedder, usage_status)
    session.commit()


def _build_chunks(transcripts: Iterable[Transcript]) -> list[RagChunkPayload]:
    if not settings.RAG_EMBEDDING_ENABLED:
        return []

    return build_rag_chunks(
        transcripts,
        chunk_size=settings.RAG_CHUNK_SIZE,
        chunk_overlap=settings.RAG_CHUNK_OVERLAP,
    )


async def _embed_chunks_async(
    embedder: EmbeddingClient, chunks: list[RagChunkPayload]
) -> tuple[list[list[float]] | None, Optional[str], Optional[int], str]:
    if not embedder.is_configured():
        logger.warning("Embedding is enabled but API key is missing, skipping vectors")
        return None, None, None, "failed"
    if embedder.provider not in {"openai", "openrouter"}:
        logger.warning(
            "Embedding provider %s not supported yet, skipping vectors", embedder.provider
        )
        return None, None, None, "failed"

    try:
        embeddings = await _embed_in_batches_async(embedder, chunks)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc, exc_info=True)
        return None, None, None, "failed"

    embedding_dim = len(embeddings[0]) if embeddings else None
    return embeddings, embedder.model, embedding_dim, "success"


def _embed_chunks_sync(
    embedder: EmbeddingClient, chunks: list[RagChunkPayload]
) -> tuple[list[list[float]] | None, Optional[str], Optional[int], str]:
    if not embedder.is_configured():
        logger.warning("Embedding is enabled but API key is missing, skipping vectors")
        return None, None, None, "failed"
    if embedder.provider not in {"openai", "openrouter"}:
        logger.warning(
            "Embedding provider %s not supported yet, skipping vectors", embedder.provider
        )
        return None, None, None, "failed"

    try:
        embeddings = _embed_in_batches_sync(embedder, chunks)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc, exc_info=True)
        return None, None, None, "failed"

    embedding_dim = len(embeddings[0]) if embeddings else None
    return embeddings, embedder.model, embedding_dim, "success"


async def _embed_in_batches_async(
    embedder: EmbeddingClient, chunks: list[RagChunkPayload]
) -> list[list[float]]:
    batch_size = max(settings.RAG_EMBED_BATCH_SIZE, 1)
    embeddings: list[list[float]] = []

    for idx in range(0, len(chunks), batch_size):
        batch = chunks[idx : idx + batch_size]
        vectors = await embedder.embed([item.content for item in batch])
        if len(vectors) != len(batch):
            raise RuntimeError("Embedding response size mismatch")
        embeddings.extend(vectors)

    return embeddings


def _embed_in_batches_sync(
    embedder: EmbeddingClient, chunks: list[RagChunkPayload]
) -> list[list[float]]:
    batch_size = max(settings.RAG_EMBED_BATCH_SIZE, 1)
    embeddings: list[list[float]] = []

    for idx in range(0, len(chunks), batch_size):
        batch = chunks[idx : idx + batch_size]
        vectors = embedder.embed_sync([item.content for item in batch])
        if len(vectors) != len(batch):
            raise RuntimeError("Embedding response size mismatch")
        embeddings.extend(vectors)

    return embeddings


def _add_rag_chunks(
    session: Session | AsyncSession,
    task: Task,
    user_id: str,
    chunks: list[RagChunkPayload],
    embeddings: list[list[float]] | None,
    embedding_model: Optional[str],
    embedding_dim: Optional[int],
) -> None:
    records: list[RagChunk] = []
    for idx, chunk in enumerate(chunks, start=1):
        embedding = embeddings[idx - 1] if embeddings else None
        records.append(
            RagChunk(
                user_id=user_id,
                task_id=str(task.id),
                transcript_id=chunk.transcript_id,
                chunk_index=idx,
                content=chunk.content,
                start_time=chunk.start_time,
                end_time=chunk.end_time,
                speaker_id=chunk.speaker_id,
                embedding=embedding,
                embedding_model=embedding_model,
                embedding_dim=embedding_dim,
            )
        )

    session.add_all(records)


def _record_embedding_usage(
    session: Session | AsyncSession,
    task: Task,
    user_id: str,
    embedder: EmbeddingClient,
    status: str,
) -> None:
    if status not in {"success", "failed"}:
        return
    usage = LLMUsage(
        user_id=user_id,
        task_id=str(task.id),
        provider=embedder.provider,
        model_id=embedder.model,
        call_type="embedding",
        summary_type=None,
        status=status,
    )
    session.add(usage)
