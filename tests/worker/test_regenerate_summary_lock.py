"""regenerate_summary 任务并发去重锁契约测试。

worker 侧原子锁(set nx ex + token 校验 delete)是重生防重的唯一正确性来源:
- 单条重生(comparison_id=None)抢锁;抢不到直接 return,不烧 LLM/配图。
- 对比(comparison_id 非空)按设计并发,豁免锁。
importlib 加载模式同 test_regenerate_summary_images.py;把 _regenerate_summary 换成 spy 隔离重活,
用 task.apply(..., throw=True) 在本进程同步执行任务体(自动绑定 self、提供 request 上下文)。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load() -> Any:
    p = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "regenerate_summary.py"
    spec = importlib.util.spec_from_file_location("regenerate_summary_lock_uut", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rs = _load()


class _FakeRedis:
    """最小同步 redis:set(nx,ex) / get / delete,decode_responses 语义(get 返回 str)。"""

    def __init__(self, *, preoccupied: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.preoccupied = preoccupied
        self.set_calls = 0
        self.deleted: list[str] = []

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        self.set_calls += 1
        if nx and (self.preoccupied or key in self.store):
            return None  # 已被占,SETNX 失败
        self.store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        return int(self.store.pop(key, None) is not None)


def _invoke(
    monkeypatch: pytest.MonkeyPatch, *, comparison_id: str | None, redis: _FakeRedis, spy_raises: bool = False
) -> list[tuple]:
    calls: list[tuple] = []

    def _spy(task_id: str, summary_type: str, model: Any, model_id: Any, request_id: Any, comparison_id: Any) -> None:
        calls.append((task_id, summary_type, comparison_id))
        if spy_raises:
            raise RuntimeError("boom")

    monkeypatch.setattr(rs, "get_sync_redis_client", lambda: redis)
    monkeypatch.setattr(rs, "_regenerate_summary", _spy)
    rs.regenerate_summary.apply(
        args=["t1", "overview"],
        kwargs={"comparison_id": comparison_id},
        throw=True,
    )
    return calls


def test_lock_free_runs_and_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    calls = _invoke(monkeypatch, comparison_id=None, redis=redis)
    assert len(calls) == 1  # 重活被调一次
    assert redis.set_calls == 1  # 抢过锁
    key = rs.build_regen_lock_key("t1", "overview")
    assert key not in redis.store  # 结束后锁已释放
    assert redis.deleted == [key]


def test_lock_held_skips_without_work(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis(preoccupied=True)
    calls = _invoke(monkeypatch, comparison_id=None, redis=redis)
    assert calls == []  # 抢不到锁→不烧钱
    assert redis.deleted == []  # 非持有者,绝不删他人锁


def test_comparison_bypasses_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    calls = _invoke(monkeypatch, comparison_id="cmp-1", redis=redis)
    assert len(calls) == 1
    assert calls[0][2] == "cmp-1"
    assert redis.set_calls == 0  # 对比完全不碰锁


def test_lock_released_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    with pytest.raises(RuntimeError, match="boom"):
        _invoke(monkeypatch, comparison_id=None, redis=redis, spy_raises=True)
    key = rs.build_regen_lock_key("t1", "overview")
    assert key not in redis.store  # finally 已释放
    assert redis.deleted == [key]  # 精确锁定:走了 token 校验删除,非靠 TTL/其它
