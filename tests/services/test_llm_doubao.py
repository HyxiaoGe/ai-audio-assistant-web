from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.llm.doubao import DoubaoLLMService


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self) -> dict[str, object]:
        return self._payload


def _build_client(response: _FakeResponse | None, error: Exception | None):
    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(
            self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object | None
        ) -> bool:
            return False

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str]
        ) -> _FakeResponse:
            if error is not None:
                raise error
            if response is None:
                raise RuntimeError("response is not set")
            return response

    return _Client


def _set_doubao_settings() -> None:
    settings.DOUBAO_API_KEY = "test-api-key"
    settings.DOUBAO_BASE_URL = "https://example.com/api/v3"
    settings.DOUBAO_MODEL = "doubao-test"
    settings.DOUBAO_MAX_TOKENS = 256


@pytest.mark.asyncio
async def test_summarize_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_doubao_settings()
    response = _FakeResponse({"choices": [{"message": {"content": "summary"}}]})
    monkeypatch.setattr(httpx, "AsyncClient", _build_client(response=response, error=None))

    service = DoubaoLLMService()
    result = await service.summarize("hello world", "overview")

    assert result == "summary"


@pytest.mark.asyncio
async def test_summarize_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_doubao_settings()
    response = _FakeResponse({"choices": [{"message": {"content": ""}}]})
    monkeypatch.setattr(httpx, "AsyncClient", _build_client(response=response, error=None))

    service = DoubaoLLMService()
    with pytest.raises(BusinessError) as exc_info:
        await service.summarize("hello world", "overview")

    assert exc_info.value.code == ErrorCode.AI_SUMMARY_GENERATION_FAILED


@pytest.mark.asyncio
async def test_summarize_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_doubao_settings()
    request = httpx.Request("POST", "https://example.com/api/v3/chat/completions")
    error = httpx.RequestError("network error", request=request)
    monkeypatch.setattr(httpx, "AsyncClient", _build_client(response=None, error=error))

    service = DoubaoLLMService()
    with pytest.raises(BusinessError) as exc_info:
        await service.summarize("hello world", "overview")

    assert exc_info.value.code == ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE
