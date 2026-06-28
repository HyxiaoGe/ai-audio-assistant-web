from app.core.i18n import get_message
from app.i18n.codes import ErrorCode


def test_channel_blocked_code_value() -> None:
    assert ErrorCode.CHANNEL_BLOCKED == 40018


def test_channel_blocked_has_dedicated_messages() -> None:
    zh = get_message(ErrorCode.CHANNEL_BLOCKED, "zh")
    en = get_message(ErrorCode.CHANNEL_BLOCKED, "en")
    assert zh and en
    # 有专门文案,不是回落到通用错误文案
    assert zh != get_message(ErrorCode.INTERNAL_SERVER_ERROR, "zh")
    assert "屏蔽" in zh
