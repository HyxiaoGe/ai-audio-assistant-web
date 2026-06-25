"""OpenAPI docs 网关:安全默认(默认关闭,显式 ENABLE_DOCS=true 才开)。

公网可达的单机部署若未设 ENABLE_DOCS,/docs、/openapi.json、/redoc 必须默认关闭,
避免接口结构对外暴露。本测试锁住「默认关 / 显式开」两侧契约。
"""

from __future__ import annotations

import pytest

from app.main import create_app


def test_docs_closed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    app = create_app()
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_docs_open_when_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_DOCS", "true")
    app = create_app()
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"
