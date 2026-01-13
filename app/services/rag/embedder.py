from __future__ import annotations

import logging
from typing import Iterable, Optional

import httpx

from app.config import settings


logger = logging.getLogger("rag.embedder")


class EmbeddingClient:
    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._provider = (provider or settings.RAG_EMBEDDING_PROVIDER or "openai").lower()
        self._model = model or settings.RAG_EMBEDDING_MODEL or "text-embedding-3-small"
        self._http_referer = settings.OPENROUTER_HTTP_REFERER or settings.API_BASE_URL
        self._app_title = settings.OPENROUTER_APP_TITLE

        if self._provider == "openrouter":
            self._api_key = api_key or settings.OPENROUTER_API_KEY
            self._base_url = (
                base_url or settings.OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1"
            ).rstrip("/")
        else:
            self._api_key = api_key or settings.OPENAI_API_KEY
            self._base_url = (
                base_url or settings.OPENAI_BASE_URL or "https://api.openai.com/v1"
            ).rstrip("/")

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _build_headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._provider == "openrouter":
            if self._http_referer:
                headers["HTTP-Referer"] = self._http_referer
            if self._app_title:
                headers["X-Title"] = self._app_title
        return headers

    async def embed(self, texts: Iterable[str]) -> list[list[float]]:
        if not self._api_key:
            raise RuntimeError("Embedding API key is not set")
        payload = {"model": self._model, "input": list(texts)}
        headers = self._build_headers()

        async with httpx.AsyncClient(base_url=self._base_url, timeout=60.0) as client:
            response = await client.post("/embeddings", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        embeddings = [item.get("embedding", []) for item in data.get("data", [])]
        if not embeddings:
            raise RuntimeError("Embedding response is empty")
        return embeddings

    def embed_sync(self, texts: Iterable[str]) -> list[list[float]]:
        if not self._api_key:
            raise RuntimeError("Embedding API key is not set")
        payload = {"model": self._model, "input": list(texts)}
        headers = self._build_headers()

        with httpx.Client(base_url=self._base_url, timeout=60.0) as client:
            response = client.post("/embeddings", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        embeddings = [item.get("embedding", []) for item in data.get("data", [])]
        if not embeddings:
            raise RuntimeError("Embedding response is empty")
        return embeddings
