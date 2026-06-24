"""死任务巡检脚手架:错误码 + i18n + Settings 开关。"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.i18n.codes import ErrorCode

_ROOT = Path(__file__).resolve().parents[1]


def test_task_stalled_error_code() -> None:
    assert ErrorCode.TASK_STALLED.value == 50004


def test_task_stalled_i18n_present() -> None:
    for name in ("zh.json", "en.json"):
        data = json.loads((_ROOT / "app" / "i18n" / name).read_text(encoding="utf-8"))
        assert "50004" in data and data["50004"].strip()


def test_dead_task_sweep_enabled_default_true() -> None:
    assert settings.DEAD_TASK_SWEEP_ENABLED is True
