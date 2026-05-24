from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.api.v1 import media as media_module
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode


class _FakeStorage:
    """SmartFactory.get_service("storage") 返回的最小存根。"""

    def __init__(self, base: str = "https://cloud.example/bucket") -> None:
        self._base = base

    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:  # noqa: ARG002
        return f"{self._base}/{object_name}?sig=fake"


def _build_test_app(
    monkeypatch: pytest.MonkeyPatch,
    mock_handler: Any,
    providers: list[str] | None = None,
) -> FastAPI:
    """Wire SmartFactory + an httpx MockTransport into a fresh FastAPI app."""

    async def fake_get_service(
        service_type: str, **kwargs: Any
    ) -> _FakeStorage:  # noqa: ARG001
        provider = kwargs.get("provider") or "minio"
        return _FakeStorage(base=f"https://{provider}.example/bucket")

    monkeypatch.setattr(media_module.SmartFactory, "get_service", fake_get_service)
    monkeypatch.setattr(
        media_module,
        "_candidate_providers",
        lambda: providers if providers is not None else ["minio"],
    )

    transport = httpx.MockTransport(mock_handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        # 只在没显式指定 transport 时注入 mock，避免覆盖测试客户端的 ASGITransport
        if "transport" not in kwargs:
            kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    app = FastAPI()
    app.include_router(media_module.router, prefix="/api/v1/media")

    # 把 BusinessError 统一转成 4xx，方便断言
    @app.exception_handler(BusinessError)
    async def _handler(_request: Any, exc: BusinessError) -> Any:
        from fastapi.responses import JSONResponse

        status_map = {ErrorCode.RESOURCE_NOT_FOUND: 404, ErrorCode.FILE_STORAGE_SERVICE_ERROR: 502}
        return JSONResponse({"code": int(exc.code), "kwargs": exc.kwargs}, status_code=status_map.get(exc.code, 500))

    return app


@pytest.mark.asyncio
async def test_full_get_streams_bytes_and_preserves_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") is None
        return httpx.Response(
            200,
            content=b"audio-data-payload",
            headers={
                "Content-Type": "audio/wav",
                "Content-Length": "18",
                "Accept-Ranges": "bytes",
            },
        )

    app = _build_test_app(monkeypatch, handler)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/media/youtube/u/x.wav")

    assert resp.status_code == 200
    assert resp.content == b"audio-data-payload"
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_range_request_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["range"] = request.headers.get("range", "")
        return httpx.Response(
            206,
            content=b"partial",
            headers={
                "Content-Type": "audio/wav",
                "Content-Range": "bytes 100-106/2000",
                "Content-Length": "7",
                "Accept-Ranges": "bytes",
            },
        )

    app = _build_test_app(monkeypatch, handler)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/media/x.wav", headers={"Range": "bytes=100-106"})

    assert seen["range"] == "bytes=100-106"
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 100-106/2000"
    assert resp.content == b"partial"


@pytest.mark.asyncio
async def test_upstream_404_falls_back_to_next_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """MinIO 没有时应继续尝试 COS，命中则正常返回。"""
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        if request.url.host.startswith("minio"):
            return httpx.Response(404, content=b"<NoSuchKey/>", headers={"Content-Type": "application/xml"})
        return httpx.Response(
            200,
            content=b"hit-on-cos",
            headers={"Content-Type": "audio/wav", "Content-Length": "10"},
        )

    app = _build_test_app(monkeypatch, handler, providers=["minio", "cos"])
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/media/legacy.wav")

    assert resp.status_code == 200
    assert resp.content == b"hit-on-cos"
    assert seen_hosts[0].startswith("minio")
    assert seen_hosts[1].startswith("cos")


@pytest.mark.asyncio
async def test_404_on_all_backends_maps_to_resource_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"<NoSuchKey/>", headers={"Content-Type": "application/xml"})

    app = _build_test_app(monkeypatch, handler, providers=["minio", "cos"])
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/media/missing.wav")

    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


@pytest.mark.asyncio
async def test_upstream_5xx_maps_to_storage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 404 的上游错误直接报错，不再尝试下一个 backend（避免掩盖真实故障）。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"upstream is sad")

    app = _build_test_app(monkeypatch, handler, providers=["minio", "cos"])
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/media/x.wav")

    assert resp.status_code == 502
    assert resp.json()["code"] == int(ErrorCode.FILE_STORAGE_SERVICE_ERROR)
