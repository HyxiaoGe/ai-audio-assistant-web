from app.core.i18n import get_message
from app.i18n.codes import ErrorCode


def test_discover_disabled_code_value() -> None:
    assert ErrorCode.DISCOVER_DISABLED == 40019


def test_discover_disabled_has_dedicated_messages() -> None:
    zh = get_message(ErrorCode.DISCOVER_DISABLED, "zh")
    en = get_message(ErrorCode.DISCOVER_DISABLED, "en")
    assert zh and en
    assert zh != get_message(ErrorCode.INTERNAL_SERVER_ERROR, "zh")
    assert "维护" in zh
