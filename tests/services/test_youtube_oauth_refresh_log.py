"""YouTubeOAuthService.refresh_access_token 的日志止血。

invalid_grant(refresh token 失效/吊销)是预期内、已处理(转 BusinessError)的状态,
应以 WARNING(不带 traceback)记录,避免触发 dev-ops-sentinel 的日志错误尖峰告警。
其它未知异常仍按 ERROR + traceback 记录。
"""

from __future__ import annotations

import logging

import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.youtube import oauth_service as oauth_module


def _service() -> oauth_module.YouTubeOAuthService:
    svc = oauth_module.YouTubeOAuthService()
    svc._client_id = "cid"
    svc._client_secret = "csec"
    svc._redirect_uri = "https://example.test/cb"
    return svc


def test_refresh_invalid_grant_warns_without_traceback(monkeypatch, caplog) -> None:
    def _raise(_self: object, _request: object) -> None:
        raise Exception("('invalid_grant: Token has been expired or revoked.', {'error': 'invalid_grant'})")

    monkeypatch.setattr(oauth_module.Credentials, "refresh", _raise)

    svc = _service()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(BusinessError) as exc_info:
            svc.refresh_access_token("rt")

    assert exc_info.value.code == ErrorCode.YOUTUBE_TOKEN_EXPIRED

    refresh_errors = [
        r for r in caplog.records if r.levelno >= logging.ERROR and "refresh token" in r.getMessage().lower()
    ]
    assert refresh_errors == [], "invalid_grant 不应再以 ERROR/traceback 记录"
    assert any(r.levelno == logging.WARNING and "refresh" in r.getMessage().lower() for r in caplog.records)


def test_refresh_unknown_error_still_logs_exception(monkeypatch, caplog) -> None:
    """非 invalid_grant 的未知异常仍应以 ERROR(traceback)记录,避免吞掉真问题。"""

    def _raise(_self: object, _request: object) -> None:
        raise Exception("connection reset by peer")

    monkeypatch.setattr(oauth_module.Credentials, "refresh", _raise)

    svc = _service()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(BusinessError):
            svc.refresh_access_token("rt")

    assert any(r.levelno >= logging.ERROR and "refresh token" in r.getMessage().lower() for r in caplog.records)
