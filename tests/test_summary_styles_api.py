from __future__ import annotations


def _client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


CANONICAL = ["meeting", "conversation", "lecture", "tutorial", "review", "news", "general"]
DEPRECATED = {"podcast", "interview", "explainer", "documentary", "video"}


def test_summary_styles_returns_auto_first_then_seven_zh() -> None:
    r = _client().get("/api/v1/summary-styles", headers={"Accept-Language": "zh-CN"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    styles = body["data"]["styles"]
    ids = [s["id"] for s in styles]
    assert ids[0] == "auto"
    assert ids[1:] == CANONICAL
    assert not (set(ids) & DEPRECATED)
    auto = styles[0]
    assert auto["name"] == "自动识别"


def test_summary_styles_auto_localized_en() -> None:
    r = _client().get("/api/v1/summary-styles", headers={"Accept-Language": "en-US"})
    assert r.status_code == 200
    styles = r.json()["data"]["styles"]
    assert styles[0]["id"] == "auto"
    assert styles[0]["name"] == "Auto-detect"
    assert [s["id"] for s in styles[1:]] == CANONICAL
