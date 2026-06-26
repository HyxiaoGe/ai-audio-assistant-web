from __future__ import annotations

from app.config import settings
from app.core.i18n import get_message
from app.i18n.codes import ErrorCode


def test_moderation_settings_defaults() -> None:
    assert settings.MODERATION_SERVICE_URL == "http://content-moderation-service:8200"
    # 模式默认 off（安全：未配置即不调 CMS、不影响线上）
    assert settings.MODERATION_SEARCH_MODE == "off"
    assert settings.MODERATION_PUBLISH_MODE == "off"
    assert isinstance(settings.MODERATION_TIMEOUT, float)


def test_new_error_codes_have_messages() -> None:
    assert int(ErrorCode.PUBLISH_CONTENT_BLOCKED) == 40017
    assert int(ErrorCode.MODERATION_SERVICE_UNAVAILABLE) == 51400
    # 两种 locale 都要有文案（无 kwargs 直接返回模板）
    for code in (ErrorCode.PUBLISH_CONTENT_BLOCKED, ErrorCode.MODERATION_SERVICE_UNAVAILABLE):
        zh = get_message(code, "zh")
        en = get_message(code, "en")
        assert zh and zh != "未知错误"
        assert en and en != "未知错误"
