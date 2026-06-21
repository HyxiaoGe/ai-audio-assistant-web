"""ClientErrorReport schema:精确截断 + 控制字符清洗(防日志注入)单测。

评审 HIGH:字段直接进 logger.warning(%s),若含换行/回车,攻击者可伪造日志行,被 Kuma/Feishu
日志扫描栈当成真事件。截断不防注入——必须清洗控制字符。
"""

from __future__ import annotations

from app.schemas.client_error import (
    _MAX_MESSAGE,
    _MAX_STACK,
    ClientErrorReport,
)


def test_fields_truncated_to_exact_caps() -> None:
    r = ClientErrorReport(message="x" * 5_000, stack="y" * 30_000)
    assert len(r.message) == _MAX_MESSAGE
    assert len(r.stack or "") == _MAX_STACK


def test_control_chars_stripped_to_prevent_log_injection() -> None:
    forged = "real error\nWARNING app.auth FORGED admin login\r\nINFO done"
    r = ClientErrorReport(
        message=forged,
        stack="line1\nline2\r\nline3",
        source="a\nb",
        url="http://x/\n/y",
        digest="d\r1",
        release="rel\n1",
    )
    for value in (r.message, r.stack, r.source, r.url, r.digest, r.release):
        assert value is not None
        assert "\n" not in value
        assert "\r" not in value
    # 内容仍保留(只是被压到同一行),不丢报告
    assert "real error" in r.message
    assert "FORGED admin login" in r.message
