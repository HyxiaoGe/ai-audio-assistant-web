from __future__ import annotations

import json

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.main import business_error_handler, create_app


def test_rate_limit_returns_real_429_with_retry_after() -> None:
    app = create_app()

    @app.get("/boom-ratelimit")
    async def _boom() -> None:
        raise BusinessError(ErrorCode.RATE_LIMIT_EXCEEDED, retry_after="60")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/boom-ratelimit")

    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    body = r.json()
    assert body["code"] == 40920
    assert "60" in body["message"]  # i18n 把 retry_after 渲染进文案
    assert body["data"] is None
    assert "traceId" in body


def test_non_rate_limit_business_error_still_200() -> None:
    app = create_app()

    @app.get("/boom-notfound")
    async def _boom() -> None:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/boom-notfound")

    # 回归:非限流业务错误维持 HTTP 200 + 信封(只有 40920 改了状态)
    assert r.status_code == 200
    assert r.json()["code"] == int(ErrorCode.TASK_NOT_FOUND)


def _make_request(path: str, method: str = "GET", locale: str = "zh") -> Request:
    req = Request(
        {"type": "http", "method": method, "path": path, "headers": [], "query_string": b""}
    )
    req.state.locale = locale
    return req


async def test_rate_limit_on_media_path_also_429() -> None:
    # 媒体字节流路径上的限流也应是 429:40920 特判优先于 _media_http_status 的 400 区间映射。
    req = _make_request("/api/v1/media/upload/u1/t1.mp3")
    resp = await business_error_handler(
        req, BusinessError(ErrorCode.RATE_LIMIT_EXCEEDED, retry_after="60")
    )
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "60"
    assert json.loads(resp.body)["code"] == 40920


def test_retry_after_is_cors_exposed() -> None:
    # 跨域可见性:Retry-After 必须进 Access-Control-Expose-Headers,否则跨域前端 JS 读不到。
    app = create_app()

    @app.get("/boom-ratelimit-cors")
    async def _boom() -> None:
        raise BusinessError(ErrorCode.RATE_LIMIT_EXCEEDED, retry_after="60")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/boom-ratelimit-cors", headers={"Origin": "http://localhost:3000"})

    assert r.status_code == 429
    # CORSMiddleware 对允许源的实际响应回写 expose 清单;含 Retry-After 才算跨域可读。
    assert "Retry-After" in r.headers.get("access-control-expose-headers", "")
