from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.api.deps import CurrentUser, get_db, get_media_user
from app.api.v1 import media as media_module
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode

# 满足 assert_owns_media_key（`<prefix>/<user_id>/...`）的归属者合法 key。
_OWNER = "u1"
_OWNED_KEY = f"upload/{_OWNER}/2026/05/30/abc.wav"


class _FakeStorage:
    """SmartFactory.get_service("storage") 返回的最小存根。"""

    def __init__(self, base: str = "https://cloud.example/bucket") -> None:
        self._base = base

    def file_exists(self, object_name: str) -> bool:  # noqa: ARG002
        # 显式「不在 OSS」：让 allow_redirect=True 的音频测试确定性地经 file_exists→False
        # 落到服务端代理，而不是依赖 stub 缺少 file_exists 抛 AttributeError 的偶然路径。
        # 测 307 直下的用例自带 file_exists→True 的子类覆盖。
        return False

    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:  # noqa: ARG002
        return f"{self._base}/{object_name}?sig=fake"


async def _fake_db() -> AsyncIterator[None]:
    yield None


def _build_test_app(
    monkeypatch: pytest.MonkeyPatch,
    mock_handler: Any,
    user_id: str | None = _OWNER,
) -> tuple[FastAPI, dict[str, int]]:
    """Wire SmartFactory + an httpx MockTransport into a fresh FastAPI app.

    Returns (app, calls) where calls["storage"] counts how many times the proxy
    reached storage — lets tests assert the auth/ownership gate short-circuits
    BEFORE any presign/upstream work.
    """
    calls = {"storage": 0}

    async def fake_get_service(service_type: str, **kwargs: Any) -> _FakeStorage:  # noqa: ARG001
        calls["storage"] += 1
        provider = kwargs.get("provider") or "oss"
        return _FakeStorage(base=f"https://{provider}.example/bucket")

    monkeypatch.setattr(media_module.SmartFactory, "get_service", fake_get_service)

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
    # 媒体接口现在依赖 token 鉴权；测试里用 fake DB + 可选的用户覆盖。
    app.dependency_overrides[get_db] = _fake_db
    if user_id is not None:
        app.dependency_overrides[get_media_user] = lambda: CurrentUser(id=user_id, email=f"{user_id}@example.com")

    # 把 BusinessError 统一转成 4xx，方便断言
    @app.exception_handler(BusinessError)
    async def _handler(_request: Any, exc: BusinessError) -> Any:
        from fastapi.responses import JSONResponse

        status_map = {
            ErrorCode.AUTH_TOKEN_NOT_PROVIDED: 401,
            ErrorCode.RESOURCE_NOT_FOUND: 404,
            ErrorCode.FILE_STORAGE_SERVICE_ERROR: 502,
        }
        return JSONResponse({"code": int(exc.code), "kwargs": exc.kwargs}, status_code=status_map.get(exc.code, 500))

    return app, calls


# ---------------------------------------------------------------------------
# 归属校验单元测试（纯函数，不触网/不触库）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "object_key",
    [
        f"upload/{_OWNER}/2026/05/30/abc.wav",
        f"youtube/{_OWNER}/v.wav",
        f"visuals/{_OWNER}/t/mindmap_x.png",
        f"summary_images/{_OWNER}/t/img.png",
    ],
)
def test_assert_owns_media_key_allows_owner(object_key: str) -> None:
    media_module.assert_owns_media_key(object_key, _OWNER)


@pytest.mark.parametrize(
    "object_key",
    [
        "upload/u2/2026/05/30/abc.wav",  # 跨租户
        "summary_images/u2/t/img.png",  # 跨租户
        f"secrets/{_OWNER}/x",  # 未知前缀
        f"upload/{_OWNER}/../u2/x.wav",  # 目录穿越
        f"/upload/{_OWNER}/x.wav",  # 绝对路径
        f"upload/{_OWNER}",  # 段数不足
        "",  # 空
    ],
)
def test_assert_owns_media_key_rejects(object_key: str) -> None:
    with pytest.raises(BusinessError) as ei:
        media_module.assert_owns_media_key(object_key, _OWNER)
    assert ei.value.code == ErrorCode.RESOURCE_NOT_FOUND


# ---------------------------------------------------------------------------
# 代理行为（owner 合法 key）
# ---------------------------------------------------------------------------


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

    app, _ = _build_test_app(monkeypatch, handler)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/youtube/{_OWNER}/x.wav")

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

    app, _ = _build_test_app(monkeypatch, handler)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/{_OWNED_KEY}", headers={"Range": "bytes=100-106"})

    assert seen["range"] == "bytes=100-106"
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 100-106/2000"
    assert resp.content == b"partial"


@pytest.mark.asyncio
async def test_oss_404_maps_to_resource_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """统一存储后只有 OSS 一个后端：OSS 上 404 即 RESOURCE_NOT_FOUND（无 cos/minio 回落）。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"<NoSuchKey/>", headers={"Content-Type": "application/xml"})

    app, _ = _build_test_app(monkeypatch, handler)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/upload/{_OWNER}/missing.wav")

    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


@pytest.mark.asyncio
async def test_upstream_5xx_maps_to_storage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 404 的上游错误直接报存储错误（不掩盖真实故障）。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"upstream is sad")

    app, _ = _build_test_app(monkeypatch, handler)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/youtube/{_OWNER}/x.wav")

    assert resp.status_code == 502
    assert resp.json()["code"] == int(ErrorCode.FILE_STORAGE_SERVICE_ERROR)


# ---------------------------------------------------------------------------
# 鉴权 / 越权（gate 在触达存储前短路）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 token、无 Authorization → 401，且不触达存储。"""

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        return httpx.Response(200, content=b"should-not-reach")

    app, calls = _build_test_app(monkeypatch, handler, user_id=None)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/{_OWNED_KEY}")

    assert resp.status_code == 401
    assert resp.json()["code"] == int(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    assert calls["storage"] == 0


@pytest.mark.asyncio
async def test_cross_tenant_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """u1 请求 u2 的对象 → 404 RESOURCE_NOT_FOUND，且不触达存储。"""

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        return httpx.Response(200, content=b"should-not-reach")

    app, calls = _build_test_app(monkeypatch, handler, user_id=_OWNER)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/media/upload/u2/2026/05/30/abc.wav")

    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    assert calls["storage"] == 0


@pytest.mark.asyncio
async def test_unknown_prefix_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        return httpx.Response(200, content=b"should-not-reach")

    app, calls = _build_test_app(monkeypatch, handler, user_id=_OWNER)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/secrets/{_OWNER}/x")

    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    assert calls["storage"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["upload", "youtube", "visuals", "summary_images"])
async def test_owned_prefixes_stream_for_owner(monkeypatch: pytest.MonkeyPatch, prefix: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok", headers={"Content-Type": "image/png", "Content-Length": "2"})

    app, _ = _build_test_app(monkeypatch, handler)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/{prefix}/{_OWNER}/t/obj.png")

    assert resp.status_code == 200
    assert resp.content == b"ok"


# ---------------------------------------------------------------------------
# OSS 直下（统一存储后的主路径）：对象在 OSS → 307 重定向，不经服务端代理
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oss_present_redirects_307_without_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """对象在 OSS 时 307 直下到预签名 URL，浏览器直连，不触发服务端代理。"""

    class _OssStorage(_FakeStorage):
        def file_exists(self, object_name: str) -> bool:  # noqa: ARG002
            return True

    proxied = {"hit": False}

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 直下不应经代理
        proxied["hit"] = True
        return httpx.Response(200, content=b"should-not-proxy")

    app, _ = _build_test_app(monkeypatch, handler)

    async def oss_get_service(_service_type: str, **_kwargs: Any) -> _OssStorage:
        return _OssStorage(base="https://oss.example/bucket")

    monkeypatch.setattr(media_module.SmartFactory, "get_service", oss_get_service)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/{_OWNED_KEY}")

    assert resp.status_code == 307
    assert resp.headers["location"] == f"https://oss.example/bucket/{_OWNED_KEY}?sig=fake"
    assert proxied["hit"] is False


# ---------------------------------------------------------------------------
# _proxy_media 的三条错误前置分支：acquire / presign / transport 失败 → 502；
# 以及 307 预签名失败时回落到服务端代理仍能正常出字节（统一存储后仅剩的兜底缝）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_acquire_oss_failure_maps_to_storage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """拿不到 OSS storage 服务（如 OSS 宕机）→ 502 FILE_STORAGE_SERVICE_ERROR，而非 500/崩溃。"""

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应触达上游
        return httpx.Response(200, content=b"should-not-reach")

    app, _ = _build_test_app(monkeypatch, handler)

    async def boom_get_service(_service_type: str, **_kwargs: Any) -> Any:
        raise RuntimeError("oss down")

    monkeypatch.setattr(media_module.SmartFactory, "get_service", boom_get_service)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/{_OWNED_KEY}")

    assert resp.status_code == 502
    assert resp.json()["code"] == int(ErrorCode.FILE_STORAGE_SERVICE_ERROR)


@pytest.mark.asyncio
async def test_proxy_presign_failure_maps_to_storage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """presign 抛错 → 502 FILE_STORAGE_SERVICE_ERROR。"""

    class _PresignBoom(_FakeStorage):
        def generate_presigned_url(self, object_name: str, expires_in: int) -> str:  # noqa: ARG002
            raise RuntimeError("presign failed")

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应触达上游
        return httpx.Response(200, content=b"should-not-reach")

    app, _ = _build_test_app(monkeypatch, handler)

    async def get_service(_service_type: str, **_kwargs: Any) -> _PresignBoom:
        return _PresignBoom()

    monkeypatch.setattr(media_module.SmartFactory, "get_service", get_service)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/{_OWNED_KEY}")

    assert resp.status_code == 502
    assert resp.json()["code"] == int(ErrorCode.FILE_STORAGE_SERVICE_ERROR)


@pytest.mark.asyncio
async def test_proxy_transport_error_maps_to_storage_error_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上游连接异常（httpx.HTTPError）→ 502，且抛错前必须 aclose 已打开的 client（无泄漏）。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    app, _ = _build_test_app(monkeypatch, handler)

    closed = {"n": 0}
    orig_aclose = httpx.AsyncClient.aclose

    async def spy_aclose(self: httpx.AsyncClient) -> None:
        closed["n"] += 1
        await orig_aclose(self)

    monkeypatch.setattr(httpx.AsyncClient, "aclose", spy_aclose)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/{_OWNED_KEY}")
        # 外层测试 client 尚未退出 async with；计数==1 即 _proxy_media 内层 client 已在错误路径关闭
        assert closed["n"] == 1

    assert resp.status_code == 502
    assert resp.json()["code"] == int(ErrorCode.FILE_STORAGE_SERVICE_ERROR)


@pytest.mark.asyncio
async def test_redirect_presign_error_falls_back_to_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """对象在 OSS 但 307 长效预签名失败时，回落到服务端代理（短效预签名）正常出字节，而非报错。"""

    class _RedirectPresignBoom(_FakeStorage):
        def file_exists(self, object_name: str) -> bool:  # noqa: ARG002
            return True

        def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
            # 长效（307 直下）签名失败、短效（代理）签名成功 → 验证 redirect→proxy 回落缝
            if expires_in == media_module._REDIRECT_PRESIGN_EXPIRES:
                raise RuntimeError("redirect presign failed")
            return f"https://oss.example/bucket/{object_name}?sig=proxy"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"served-via-proxy",
            headers={"Content-Type": "audio/wav", "Content-Length": "16"},
        )

    app, _ = _build_test_app(monkeypatch, handler)

    async def get_service(_service_type: str, **_kwargs: Any) -> _RedirectPresignBoom:
        return _RedirectPresignBoom()

    monkeypatch.setattr(media_module.SmartFactory, "get_service", get_service)

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/media/{_OWNED_KEY}")

    assert resp.status_code == 200
    assert resp.content == b"served-via-proxy"
