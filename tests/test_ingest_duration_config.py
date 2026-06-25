from __future__ import annotations

import json
import pathlib


def test_media_too_long_error_code_is_40015() -> None:
    from app.i18n.codes import ErrorCode

    assert ErrorCode.MEDIA_TOO_LONG == 40015


def test_ingest_max_duration_default_is_four_hours() -> None:
    # 断言字段默认值（不受 env 覆盖影响）
    from app.config import Settings

    assert Settings.model_fields["INGEST_MAX_DURATION_SECONDS"].default == 14400


def test_media_too_long_has_zh_en_messages_with_param() -> None:
    base = pathlib.Path(__file__).resolve().parents[1] / "app" / "i18n"
    zh = json.loads((base / "zh.json").read_text(encoding="utf-8"))
    en = json.loads((base / "en.json").read_text(encoding="utf-8"))
    assert "40015" in zh and "{max_minutes}" in zh["40015"]
    assert "40015" in en and "{max_minutes}" in en["40015"]
