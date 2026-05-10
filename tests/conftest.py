from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# CI 环境（如 GHCR build job 内跑 pytest）没有 .env 文件，
# 给几个在 module load 时校验的 env 设占位，避免触发 RuntimeError("XXX is not set")：
#   - DATABASE_URL: app/db.py 顶层 create_async_engine
#   - REDIS_URL: worker/celery_app.py 顶层 Celery(broker=...)
# 真要跑这些资源的测试自己用 monkeypatch / fixture 覆盖。
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
