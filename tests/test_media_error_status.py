"""Media byte-stream errors must surface as REAL HTTP status codes.

The unified-response contract returns HTTP 200 + JSON envelope for every error
so the frontend api-client can read the ``code`` field. But the media proxy
(GET /api/v1/media/<key>) is consumed directly by browser ``<audio>``/``<img>``
elements, which need a non-2xx status to fire their ``error`` event (which in
turn drives the frontend's short-ticket refresh + retry). Production previously
returned 200 + JSON for media auth/404/storage failures, so playback failed
silently. These tests pin the real-handler behavior (the existing media-proxy
tests install their OWN handler, so prod 200-vs-4xx was untested).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.main import _media_http_status, business_error_handler, database_error_handler


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(DBAPIError, database_error_handler)

    @app.get("/api/v1/media/notfound")
    async def _media_notfound() -> None:
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)

    @app.get("/api/v1/media/noauth")
    async def _media_noauth() -> None:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)

    @app.get("/api/v1/media/expired")
    async def _media_expired() -> None:
        raise BusinessError(ErrorCode.AUTH_TOKEN_EXPIRED)

    @app.get("/api/v1/media/storage")
    async def _media_storage() -> None:
        raise BusinessError(ErrorCode.FILE_STORAGE_SERVICE_ERROR)

    # A DBAPIError on the media path (get_media_user → _resolve_user db.get/flush blip)
    # must also surface a real HTTP status, not a 200 envelope.
    @app.get("/api/v1/media/dberror")
    async def _media_dberror() -> None:
        raise DBAPIError("SELECT 1", {}, Exception("connection lost"))

    @app.get("/api/v1/media/dbbaduuid")
    async def _media_dbbaduuid() -> None:
        raise DBAPIError("SELECT 1", {}, Exception("invalid UUID 'zzz'"))

    # POST /media/ticket is consumed by api-client → must keep envelope (HTTP 200).
    @app.post("/api/v1/media/ticket")
    async def _media_ticket() -> None:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)

    # A non-media endpoint → must keep the unified 200 envelope.
    @app.get("/api/v1/tasks/x")
    async def _task() -> None:
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)

    # A non-media DBAPIError → must keep the unified 200 envelope.
    @app.get("/api/v1/tasks/dberror")
    async def _task_dberror() -> None:
        raise DBAPIError("SELECT 1", {}, Exception("connection lost"))

    return app


# --- pure mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        (ErrorCode.AUTH_TOKEN_NOT_PROVIDED, 401),
        (ErrorCode.AUTH_TOKEN_INVALID, 401),
        (ErrorCode.AUTH_TOKEN_EXPIRED, 401),
        (ErrorCode.PERMISSION_DENIED, 403),
        (ErrorCode.RESOURCE_NOT_FOUND, 404),
        (ErrorCode.TASK_NOT_FOUND, 404),
        (ErrorCode.FILE_STORAGE_SERVICE_ERROR, 502),
        (ErrorCode.INTERNAL_SERVER_ERROR, 500),
    ],
)
def test_media_http_status_mapping(code: ErrorCode, expected: int) -> None:
    assert _media_http_status(code) == expected


# --- handler behavior on the media stream path -----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,expected_status,expected_code",
    [
        ("/api/v1/media/notfound", 404, ErrorCode.RESOURCE_NOT_FOUND),
        ("/api/v1/media/noauth", 401, ErrorCode.AUTH_TOKEN_NOT_PROVIDED),
        ("/api/v1/media/expired", 401, ErrorCode.AUTH_TOKEN_EXPIRED),
        ("/api/v1/media/storage", 502, ErrorCode.FILE_STORAGE_SERVICE_ERROR),
    ],
)
async def test_media_stream_returns_real_http_status(
    path: str, expected_status: int, expected_code: ErrorCode
) -> None:
    app = _build_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(path)
    assert resp.status_code == expected_status
    body: dict[str, Any] = resp.json()
    assert body["code"] == int(expected_code)  # envelope still carried for debugging


@pytest.mark.asyncio
async def test_media_ticket_post_keeps_envelope_200() -> None:
    """POST /media/ticket goes through api-client → unified 200 envelope, not 401."""
    app = _build_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/media/ticket")
    assert resp.status_code == 200
    assert resp.json()["code"] == int(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)


@pytest.mark.asyncio
async def test_non_media_endpoint_keeps_envelope_200() -> None:
    app = _build_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/tasks/x")
    assert resp.status_code == 200
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


# --- DBAPIError on the media path (the sibling handler that was left behind) ---


@pytest.mark.asyncio
async def test_media_db_error_returns_real_http_status() -> None:
    """A DB blip during media GET must yield HTTP 502/500, not a 200 envelope."""
    app = _build_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/media/dberror")
    assert resp.status_code == 500
    assert resp.json()["code"] == int(ErrorCode.DATABASE_SERVICE_ERROR)


@pytest.mark.asyncio
async def test_media_db_invalid_uuid_returns_404() -> None:
    """The invalid-UUID branch on the media path maps to a real 404, not 200."""
    app = _build_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/media/dbbaduuid")
    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.TASK_NOT_FOUND)


@pytest.mark.asyncio
async def test_non_media_db_error_keeps_envelope_200() -> None:
    """Off the media path, DBAPIError keeps the unified 200 envelope for api-client."""
    app = _build_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/tasks/dberror")
    assert resp.status_code == 200
    assert resp.json()["code"] == int(ErrorCode.DATABASE_SERVICE_ERROR)
