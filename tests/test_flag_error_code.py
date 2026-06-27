import json
from pathlib import Path

from app.i18n.codes import ErrorCode


def test_flag_already_resolved_code_value() -> None:
    assert ErrorCode.FLAG_ALREADY_RESOLVED == 40906


def test_flag_already_resolved_has_i18n_messages() -> None:
    base = Path(__file__).resolve().parent.parent / "app" / "i18n"
    for name in ("zh.json", "en.json"):
        data = json.loads((base / name).read_text(encoding="utf-8"))
        assert "40906" in data and data["40906"].strip()
