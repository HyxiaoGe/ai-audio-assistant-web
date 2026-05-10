from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# CI 环境（如 GHCR build job 内跑 pytest）没有 .env 文件，
# 给 DATABASE_URL 设默认 sqlite 占位，避免 app.db / worker.db 在 import time 触发 RuntimeError。
# 真要跑 DB 的测试自己用 monkeypatch / fixture 覆盖。
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
