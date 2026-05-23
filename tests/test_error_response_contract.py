from __future__ import annotations

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app


def test_http_exception_uses_unified_response_shape() -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise HTTPException(status_code=404, detail="Config not found")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")

    assert response.status_code == 200
    assert response.json() == {
        "code": 40499,
        "message": "Config not found",
        "data": None,
        "traceId": response.json()["traceId"],
    }
