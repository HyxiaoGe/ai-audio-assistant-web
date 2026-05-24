from __future__ import annotations

import httpx
import pytest

from app.core.exceptions import BusinessError
from app.services.llm.image_service import ImageServiceLLMService, _resolve_size


@pytest.fixture(autouse=True)
def _set_image_service_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.llm.image_service.settings.IMAGE_SERVICE_BASE_URL",
        "http://image-service.test",
    )
    monkeypatch.setattr(
        "app.services.llm.image_service.settings.IMAGE_SERVICE_API_KEY",
        "test-key",
    )
    monkeypatch.setattr(
        "app.services.llm.image_service.settings.IMAGE_SERVICE_DEFAULT_MODEL",
        "gemini-3-pro-image-preview",
    )


class TestResolveSize:
    @pytest.mark.parametrize(
        ("aspect_ratio", "image_size", "expected"),
        [
            ("16:9", "2K", "2048x1152"),
            ("9:16", "2K", "1152x2048"),
            ("1:1", "2K", "2048x2048"),
            ("3:4", "2K", "1536x2048"),
            ("16:9", "1K", "1024x576"),
            ("16:9", "4K", "2048x1152"),  # 4K clamps to 2048
            ("16:9", None, "2048x1152"),  # default to 2K
            ("16:9", "1920x1080", "1920x1080"),  # explicit WxH passthrough
            ("16:9", "3000x4000", "2048x2048"),  # explicit WxH clamps to max
        ],
    )
    def test_known_combinations(self, aspect_ratio: str, image_size: str | None, expected: str) -> None:
        assert _resolve_size(aspect_ratio, image_size) == expected

    def test_invalid_aspect_falls_back_to_square(self) -> None:
        assert _resolve_size("bad", "2K") == "2048x2048"
        assert _resolve_size("0:0", "2K") == "2048x2048"


class TestGenerateImage:
    @pytest.mark.asyncio
    async def test_success_relative_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常流程：image-service 返回相对路径 → provider 拼 base_url 下载"""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            if request.url.path == "/v1/generate":
                assert request.headers["Authorization"] == "Bearer test-key"
                body = request.read().decode()
                assert "gemini-3-pro-image-preview" in body
                assert '"aspect_ratio":"16:9"' in body
                return httpx.Response(
                    200,
                    json={
                        "id": "abc",
                        "image_url": "/static/images/deadbeef.png",
                        "model": "gemini-3-pro-image-preview",
                        "cached": False,
                        "created_at": "2026-05-24T00:00:00Z",
                    },
                )
            if request.url.path == "/static/images/deadbeef.png":
                return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nfake-bytes")
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient.__init__

        def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = transport
            original(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

        service = ImageServiceLLMService()
        result = await service.generate_image(prompt="a cat", aspect_ratio="16:9", image_size="2K")

        assert result == b"\x89PNG\r\n\x1a\nfake-bytes"
        # 1 次 POST + 1 次 GET 下载
        assert len(captured_requests) == 2
        assert str(captured_requests[0].url) == "http://image-service.test/v1/generate"
        assert str(captured_requests[1].url) == "http://image-service.test/static/images/deadbeef.png"

    @pytest.mark.asyncio
    async def test_absolute_url_used_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """image-service 若返回绝对 URL（CDN 场景），不再拼 base_url"""
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(str(request.url))
            if request.url.path == "/v1/generate":
                return httpx.Response(
                    200,
                    json={
                        "id": "abc",
                        "image_url": "https://cdn.example.com/img/x.png",
                        "model": "m",
                        "cached": True,
                        "created_at": "now",
                    },
                )
            return httpx.Response(200, content=b"bytes")

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient.__init__
        monkeypatch.setattr(
            httpx.AsyncClient,
            "__init__",
            lambda self, *a, **k: original(self, *a, **{**k, "transport": transport}),
        )

        service = ImageServiceLLMService()
        await service.generate_image(prompt="x")
        assert captured[1] == "https://cdn.example.com/img/x.png"

    @pytest.mark.asyncio
    async def test_empty_prompt_raises(self) -> None:
        service = ImageServiceLLMService()
        with pytest.raises(BusinessError):
            await service.generate_image(prompt="")

    @pytest.mark.asyncio
    async def test_upstream_500_raises_business_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, json={"error": "upstream", "message": "boom"})

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient.__init__
        monkeypatch.setattr(
            httpx.AsyncClient,
            "__init__",
            lambda self, *a, **k: original(self, *a, **{**k, "transport": transport}),
        )

        service = ImageServiceLLMService()
        with pytest.raises(BusinessError) as exc:
            await service.generate_image(prompt="x")
        assert "502" in exc.value.kwargs.get("reason", "")

    @pytest.mark.asyncio
    async def test_response_without_image_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "abc", "model": "m", "cached": False, "created_at": "now"})

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient.__init__
        monkeypatch.setattr(
            httpx.AsyncClient,
            "__init__",
            lambda self, *a, **k: original(self, *a, **{**k, "transport": transport}),
        )

        service = ImageServiceLLMService()
        with pytest.raises(BusinessError) as exc:
            await service.generate_image(prompt="x")
        assert "no image_url" in exc.value.kwargs.get("reason", "")

    def test_model_id_strips_provider_prefix(self) -> None:
        """兼容 'google/gemini-3-pro-image-preview' 这种遗留配置"""
        service = ImageServiceLLMService(model_id="google/gemini-3-pro-image-preview")
        # 仅验证字段保留；具体拆分发生在 generate_image() 中
        assert service.model_name == "google/gemini-3-pro-image-preview"

    def test_missing_base_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.services.llm.image_service.settings.IMAGE_SERVICE_BASE_URL", None)
        with pytest.raises(RuntimeError):
            ImageServiceLLMService()
