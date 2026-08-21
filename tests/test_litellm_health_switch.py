"""LITELLM_HEALTH_ENABLED 开关与 Redis 多实例协调测试。

背景：/health 会对 LiteLLM DB 里每个模型各打一次真实 completion（qwen reasoning
模型每次数百 reasoning token），多 worker（AI Audio --workers 2）与多服务（Fusion）
各自起探测循环会重复烧钱。修复后：
1. LITELLM_HEALTH_ENABLED 默认 false —— 不启动后台循环、不请求 /health；
2. 开启后用 Redis round-claim 协调 —— 每周期全集群最多一轮探测，结果写共享快照。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core import litellm_health


class _FakeAsyncRedis:
    """SET NX EX / GET 语义的最小 fake，模拟跨实例共享的 Redis。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def set(self, name: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and name in self._data:
            return None
        self._data[name] = value
        return True

    async def get(self, name: str) -> str | None:
        return self._data.get(name)


class _SyncFake:
    """同步读客户端（Redis 快照读取路径）。"""

    def __init__(self, inner: _FakeAsyncRedis) -> None:
        self._inner = inner

    def get(self, name: str) -> str | None:
        return self._inner._data.get(name)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self, responses: dict, **_kwargs) -> None:
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url: str):
        return _FakeResponse(self._responses[url.rsplit("/", 1)[-1]])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每个用例重置模块状态；默认不接真实 Redis。"""
    with litellm_health._lock:
        litellm_health._by_alias.clear()
        litellm_health._last_checked_at = 0.0
    litellm_health._refresh_task = None
    litellm_health._last_redis_sync_at = 0.0
    litellm_health._sync_redis_client = None
    # 开关走 pydantic Settings（.env / os.environ 都会进 settings，但 settings 是单例，
    # 测试里直接 patch 字段，与「cp .env.example .env 可配置」的仓库约定一致）
    monkeypatch.setattr(litellm_health.settings, "LITELLM_HEALTH_ENABLED", False)
    monkeypatch.delenv("LITELLM_HEALTH_INTERVAL_SECONDS", raising=False)
    monkeypatch.setattr(litellm_health, "_get_async_redis", lambda: None)
    monkeypatch.setattr(litellm_health, "_get_sync_redis", lambda: None)
    yield
    task = litellm_health._refresh_task
    litellm_health._refresh_task = None
    if task is not None and not task.done():
        task.cancel()


# ── 开关：enabled / disabled ────────────────────────────────────


def test_disabled_by_default():
    """默认关闭：避免 dev 环境继续产生全模型探活费用。"""
    assert litellm_health._is_enabled() is False


def test_enabled_flag_parsing(monkeypatch):
    monkeypatch.setattr(litellm_health.settings, "LITELLM_HEALTH_ENABLED", True)
    assert litellm_health._is_enabled() is True


@pytest.mark.asyncio
async def test_start_disabled_does_not_start_loop():
    """disabled 时 startup 不得启动任何后台循环。"""
    await litellm_health.start()
    assert litellm_health._refresh_task is None


@pytest.mark.asyncio
async def test_start_enabled_starts_loop(monkeypatch):
    """enabled 时启动一个后台循环。"""
    monkeypatch.setattr(litellm_health.settings, "LITELLM_HEALTH_ENABLED", True)
    await litellm_health.start()
    assert litellm_health._refresh_task is not None
    await litellm_health.stop()
    assert litellm_health._refresh_task is None


def test_disabled_get_health_returns_unknown():
    """disabled 时健康状态回退 unknown（调用方按可用处理，不阻塞用户）。"""
    assert litellm_health.get_health("qwen3.7-max") == {
        "status": "unknown",
        "error": None,
        "checked_at": None,
    }
    assert litellm_health.has_data() is False


def test_disabled_reader_never_touches_redis(monkeypatch):
    """disabled 时读取不碰 Redis 快照，直接返回进程内状态。"""
    called = False

    def _probe():
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(litellm_health, "_get_sync_redis", _probe)
    assert litellm_health.get_health("qwen3.7-max")["status"] == "unknown"
    assert called is False


# ── Redis round-claim：多 worker / 多服务协调 ────────────────────


@pytest.mark.asyncio
async def test_concurrent_claim_single_winner(monkeypatch):
    """模拟 2 个 audio worker + 1 个 fusion 同时启动：只有一方抢到本轮探测权。"""
    fake = _FakeAsyncRedis()
    monkeypatch.setattr(litellm_health, "_get_async_redis", lambda: fake)
    results = await asyncio.gather(
        litellm_health._try_claim_round(),
        litellm_health._try_claim_round(),
        litellm_health._try_claim_round(),
    )
    assert sum(1 for r in results if r) == 1


@pytest.mark.asyncio
async def test_claim_rejected_while_held(monkeypatch):
    """同一周期内第二个实例抢不到探测权。"""
    fake = _FakeAsyncRedis()
    monkeypatch.setattr(litellm_health, "_get_async_redis", lambda: fake)
    assert await litellm_health._try_claim_round() is True
    assert await litellm_health._try_claim_round() is False


@pytest.mark.asyncio
async def test_claim_fails_closed_when_redis_unavailable(monkeypatch):
    """Redis 不可用时跳过本轮：宁可 unknown，也不能失去协调地重复探测烧钱。"""
    fake = _FakeAsyncRedis()

    async def _boom(*_a, **_k):
        raise ConnectionError("redis down")

    fake.set = _boom  # type: ignore[method-assign]
    monkeypatch.setattr(litellm_health, "_get_async_redis", lambda: fake)
    assert await litellm_health._try_claim_round() is False


@pytest.mark.asyncio
async def test_two_workers_share_one_probe_per_round(monkeypatch):
    """两个 worker 同时启动时，每轮最多执行一次探测（_fetch_once）。"""
    fake = _FakeAsyncRedis()
    monkeypatch.setattr(litellm_health, "_get_async_redis", lambda: fake)
    probe_count = 0

    async def _fake_fetch():
        nonlocal probe_count
        probe_count += 1

    monkeypatch.setattr(litellm_health, "_fetch_once", _fake_fetch)

    async def _worker_round():
        if await litellm_health._try_claim_round():
            await litellm_health._fetch_once()

    await asyncio.gather(_worker_round(), _worker_round())
    assert probe_count == 1


# ── 共享快照：探测结果写 Redis，其它实例可读 ────────────────────


@pytest.mark.asyncio
async def test_probe_writes_shared_snapshot(monkeypatch):
    """探测成功后健康结果写入 Redis 共享快照（供其它 worker / 其它服务读取）。"""
    fake = _FakeAsyncRedis()
    monkeypatch.setattr(litellm_health, "_get_async_redis", lambda: fake)
    responses = {
        "info": {"data": [{"model_name": "qwen3.7-max", "model_info": {"id": "uuid-qwen"}}]},
        "health": {"healthy_endpoints": [{"model_id": "uuid-qwen"}], "unhealthy_endpoints": []},
    }
    monkeypatch.setattr(
        litellm_health.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeHttpClient(responses, **kwargs),
    )
    monkeypatch.setattr(litellm_health.time, "time", lambda: 456.0)

    await litellm_health._fetch_once()

    assert litellm_health.get_health("qwen3.7-max") == {
        "status": "healthy",
        "error": None,
        "checked_at": 456.0,
    }
    raw = await fake.get(litellm_health._SNAPSHOT_KEY)
    assert raw is not None
    payload = json.loads(raw)
    assert payload["checked_at"] == 456.0
    assert payload["by_alias"]["qwen3.7-max"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_reader_syncs_from_shared_snapshot(monkeypatch):
    """另一个实例（worker/服务）启动后，能从 Redis 快照恢复健康状态。"""
    monkeypatch.setattr(litellm_health.settings, "LITELLM_HEALTH_ENABLED", True)
    fake = _FakeAsyncRedis()
    fake._data[litellm_health._SNAPSHOT_KEY] = json.dumps(
        {
            "checked_at": 456.0,
            "by_alias": {"qwen3.7-max": {"status": "healthy", "error": None}},
        }
    )
    monkeypatch.setattr(litellm_health, "_get_sync_redis", lambda: _SyncFake(fake))

    assert litellm_health.has_data() is True
    assert litellm_health.get_health("qwen3.7-max") == {
        "status": "healthy",
        "error": None,
        "checked_at": 456.0,
    }


@pytest.mark.asyncio
async def test_probe_failure_keeps_stale_state(monkeypatch):
    """探测失败时保留旧快照，不清空健康状态。"""
    fake = _FakeAsyncRedis()
    monkeypatch.setattr(litellm_health, "_get_async_redis", lambda: fake)
    responses = {
        "info": {"data": [{"model_name": "qwen3.7-max", "model_info": {"id": "uuid-qwen"}}]},
        "health": {"healthy_endpoints": [{"model_id": "uuid-qwen"}], "unhealthy_endpoints": []},
    }
    monkeypatch.setattr(
        litellm_health.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeHttpClient(responses, **kwargs),
    )
    monkeypatch.setattr(litellm_health.time, "time", lambda: 456.0)
    await litellm_health._fetch_once()
    assert litellm_health.get_health("qwen3.7-max")["status"] == "healthy"

    # 下一轮探测失败（LiteLLM 不可达）→ 旧状态保留
    class _BadClient(_FakeHttpClient):
        async def get(self, url: str):
            raise RuntimeError("litellm down")

    monkeypatch.setattr(
        litellm_health.httpx,
        "AsyncClient",
        lambda **kwargs: _BadClient(responses, **kwargs),
    )
    await litellm_health._fetch_once()
    assert litellm_health.get_health("qwen3.7-max")["status"] == "healthy"
    assert litellm_health.has_data() is True


@pytest.mark.asyncio
async def test_snapshot_removes_aliases_absent_from_new_snapshot(monkeypatch):
    """更新快照里消失的 alias 应收敛为 unknown，不残留本地旧值。"""
    monkeypatch.setattr(litellm_health.settings, "LITELLM_HEALTH_ENABLED", True)
    fake = _FakeAsyncRedis()
    # 先同步一份含 qwen-vl-max 的旧快照（checked_at=456）
    fake._data[litellm_health._SNAPSHOT_KEY] = json.dumps(
        {
            "checked_at": 456.0,
            "by_alias": {
                "qwen-vl-max": {"status": "unhealthy", "error": "服务商暂时不可用"},
            },
        }
    )
    monkeypatch.setattr(litellm_health, "_get_sync_redis", lambda: _SyncFake(fake))
    assert litellm_health.get_health("qwen-vl-max")["status"] == "unhealthy"

    # 新快照（checked_at=789）不再包含该 alias → 读取后收敛为 unknown
    fake._data[litellm_health._SNAPSHOT_KEY] = json.dumps(
        {
            "checked_at": 789.0,
            "by_alias": {"qwen3.7-max": {"status": "healthy", "error": None}},
        }
    )
    litellm_health._last_redis_sync_at = 0.0
    assert litellm_health.get_health("qwen-vl-max")["status"] == "unknown"
    assert litellm_health.get_health("qwen3.7-max")["status"] == "healthy"


@pytest.mark.asyncio
async def test_snapshot_older_than_local_does_not_overwrite(monkeypatch):
    """快照不比本地新时不覆盖：本地刚探测完成（快照写入未落盘）不能被旧快照回退。"""
    monkeypatch.setattr(litellm_health.settings, "LITELLM_HEALTH_ENABLED", True)
    fake = _FakeAsyncRedis()
    with litellm_health._lock:
        litellm_health._by_alias["qwen3.7-max"] = {"status": "healthy", "error": None}
        litellm_health._last_checked_at = 1000.0
    fake._data[litellm_health._SNAPSHOT_KEY] = json.dumps(
        {
            "checked_at": 456.0,
            "by_alias": {"qwen3.7-max": {"status": "unhealthy", "error": "服务商暂时不可用"}},
        }
    )
    monkeypatch.setattr(litellm_health, "_get_sync_redis", lambda: _SyncFake(fake))
    assert litellm_health.get_health("qwen3.7-max")["status"] == "healthy"
