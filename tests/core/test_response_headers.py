from __future__ import annotations

import json

from app.core.response import error


def test_error_sets_custom_header_and_status() -> None:
    resp = error(40920, "too many", status_code=429, headers={"Retry-After": "60"})
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "60"  # starlette 头大小写不敏感
    body = json.loads(resp.body)
    assert body["code"] == 40920
    assert body["message"] == "too many"
    assert body["data"] is None
    assert "traceId" in body


def test_error_without_headers_unchanged() -> None:
    resp = error(40000, "bad param")
    assert resp.status_code == 200
    assert "retry-after" not in resp.headers
