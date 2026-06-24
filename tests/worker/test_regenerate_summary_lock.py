"""regenerate_summary 任务并发去重锁契约测试。

worker 侧原子锁(set nx ex + token 校验 delete)是重生防重的唯一正确性来源:
- 单条重生(comparison_id=None)抢锁;抢不到直接 return,不烧 LLM/配图。
- 对比(comparison_id 非空)按设计并发,豁免锁。
桩打在「真正执行的那个函数的 __globals__」上,而不是某个模块对象上:celery_app 在 import 时按
固定名 "worker.tasks.regenerate_summary" 注册任务,而本测试与 test_regenerate_summary_images.py
都会加载 regenerate_summary。全量串行套件里任务名只认第一个注册者,各模块的 regenerate_summary
属性可能都指向「第一个副本」的函数——把桩打在 rs 模块对象上就会失效,跑真 redis(CI 镜像无
redis→连接被拒)+ 真异步 DB(sqlite 同步上下文→MissingGreenlet),即 CI 全量套件偶发红、本地却
绿的根因。改用 monkeypatch.setitem 打到 rs.regenerate_summary.run.__globals__(.apply() 真正执行
的那个函数的全局名字空间),与「任务对象到底绑到哪个模块」无关。把 _regenerate_summary 换成 spy
隔离重活,用 task.apply(..., throw=True) 在本进程同步执行任务体(自动绑定 self、提供 request 上下文)。
"""

from __future__ import annotations

from typing import Any

import pytest

import worker.tasks.regenerate_summary as rs


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

    # 打到 .apply() 真正执行的那个函数的全局名字空间(见模块 docstring:任务对象可能绑到另一份副本的函数)
    task_globals = rs.regenerate_summary.run.__globals__
    monkeypatch.setitem(task_globals, "get_sync_redis_client", lambda: redis)
    monkeypatch.setitem(task_globals, "_regenerate_summary", _spy)
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


class _NxFailSelfTokenRedis:
    """模拟 SETNX 失败、且锁里存的就是本次 token(=自己上一条崩溃留下的陈旧自锁)。

    nx=True 的 set 记下本次尝试写入的 value 并返回 None(失败);后续 get 返回该 value →
    与任务计算出的 lock_token 必然相等,命中「自 token 接管」分支。非 nx 的 set = 续租 TTL。
    """

    def __init__(self) -> None:
        self.set_calls = 0
        self.reset_calls = 0
        self._value: str | None = None
        self.deleted: list[str] = []

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        self.set_calls += 1
        if nx:
            self._value = value
            return None
        self.reset_calls += 1
        self._value = value
        return True

    def get(self, key: str) -> str | None:
        return self._value

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        self._value = None
        return 1


class _NxFailOtherTokenRedis:
    """SETNX 失败、锁里是别人的 token → 不接管,跳过(保留原行为)。"""

    def __init__(self) -> None:
        self.set_calls = 0
        self.deleted: list[str] = []

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        self.set_calls += 1
        return None  # 永远抢不到

    def get(self, key: str) -> str | None:
        return "someone-elses-token"

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        return 0


def test_own_stale_lock_is_taken_over(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _NxFailSelfTokenRedis()
    calls = _invoke(monkeypatch, comparison_id=None, redis=redis)
    assert len(calls) == 1  # 接管续跑:重活被执行
    assert redis.reset_calls == 1  # 续租了 TTL(非 nx 的 set 调一次)
    key = rs.build_regen_lock_key("t1", "overview")
    assert redis.deleted == [key]  # finally 仍 token 校验后释放


def test_other_holder_lock_still_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _NxFailOtherTokenRedis()
    calls = _invoke(monkeypatch, comparison_id=None, redis=redis)
    assert calls == []  # 别人在跑 → 跳过,不烧钱
    assert redis.deleted == []  # 非持有者绝不删他人锁
