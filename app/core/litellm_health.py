"""LiteLLM `/health` 探测的后台缓存层。

为什么要单独搞一个模块：
- `/health` 会真的对每个 model 打一次 completion，单次 5~30s 不止；不能放
  在 `/api/v1/llm/models` 请求路径上同步触发（之前就是同步调，所以 picker
  偶尔会被 LiteLLM 慢响应拖到 5s 超时）。
- 但前端又需要知道哪些 alias 当前是真的能用、哪些挂了。

成本与多实例问题（2026-08 修复的根因）：
- `/health` 对 LiteLLM DB 里每个模型各打一次真实 completion，其中 qwen 等
  reasoning 模型每次生成数百 reasoning token（enable_thinking/max_tokens 都
  压不掉），探得越频繁、在服务商侧产生的真实费用越高。
- AI Audio 以 `--workers 2` 部署，Fusion 等其它服务也可能各自起探测循环；
  模块级 `_by_alias` / `_refresh_task` 是进程内状态，拦不住多 worker 与多服务
  重复探测同一个 LiteLLM（历史上 dev 每 30min 3 个循环 × 3 模型 ≈ 432 次/天
  Qwen 推理，阿里云账单每天固定 1.5 元+）。

本模块的策略：
1. 总开关 `LITELLM_HEALTH_ENABLED`（默认 **false**）。关闭时 FastAPI startup
   不启动后台循环、绝不请求 `/health`；模型列表照常返回，健康状态回退到
   unknown（调用方一律按可用处理），不阻塞任何业务。
2. 开启时用 Redis 做跨实例协调（分布式 round-claim）：
   - 每轮先 `SET litellm:health:probe:claim:v1 NX EX <interval>` 抢「本轮探测
     权」，只有抢到的实例才真正打 `/health`；其它实例——同一服务的其它 uvicorn
     worker，以及共享同一 Redis 的 fusion-api 等其它服务——本轮直接跳过 →
     每个周期全集群最多执行一轮 `/health`。
   - 探测成功后把健康快照写 Redis `litellm:health:snapshot:v1`（TTL 7 天），
     所有 worker/服务读取时先限频（30s）从该快照同步本地缓存 → 健康结果不再
     只存在单个 worker 内存里。
   - Redis 不可用（未配置/连接失败）时本轮跳过探测：宁可显示 unknown，也不能
     失去协调地重复探测烧钱。
3. 保留原行为：首次未拉到时 alias 返回 status="unknown"，调用方按 healthy
   处理（避免冷启动期间整个 picker 被误灰）；探测失败不会清空上一次的结果
   （保留 stale 数据比突然全清空更稳）。

对外接口：get_health(alias) -> {status, error, checked_at}、has_data()。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
import uuid
from typing import Any

import httpx
import redis

from app.config import settings
from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

# 探测间隔默认值（秒）。/health 会对 LiteLLM DB 里每个模型各打一次真实 completion，
# 其中 qwen 等 reasoning 模型每次会生成数百 reasoning token（且 enable_thinking /
# max_tokens 都压不掉），探得越频繁、在服务商侧产生的真实费用越高。模型存活状态变化
# 很慢，30min 刷新足够；可用 LITELLM_HEALTH_INTERVAL_SECONDS 覆盖。
# 详见 https://github.com/HyxiaoGe/ai-audio-assistant-web/issues/68
_DEFAULT_REFRESH_INTERVAL_SECONDS = 1800.0

# 单次 `/health` 调用超时——LiteLLM 会并发探测所有端点，但慢的 provider 可能拖到 1min+
_HEALTH_REQUEST_TIMEOUT = float(os.environ.get("LITELLM_HEALTH_REQUEST_TIMEOUT", "90"))

# ── Redis 协调常量 ──────────────────────────────────────────────
# 与 fusion-api 的 app/ai/litellm_health.py 保持同一组 key（两服务共享同一 Redis，
# 才能协调出「每个周期最多一轮」）。改 key 必须两边同步。
_PROBE_CLAIM_KEY = "litellm:health:probe:claim:v1"  # round-claim 分布式锁
_SNAPSHOT_KEY = "litellm:health:snapshot:v1"  # 健康快照（跨实例共享）
_SNAPSHOT_TTL_SECONDS = 7 * 24 * 3600  # 快照保留 7 天：探测停止后 stale 数据自愈过期
_READ_CACHE_TTL_SECONDS = 30.0  # 读侧从快照同步的限频窗口

_lock = threading.Lock()
# alias -> {"status": "healthy"|"unhealthy", "error": str|None}
_by_alias: dict[str, dict[str, Any]] = {}
_last_checked_at: float = 0.0
_refresh_task: asyncio.Task | None = None

# 读侧的同步 Redis 客户端（懒创建，仅 enabled 时使用）
_sync_redis_client: redis.Redis | None = None
_last_redis_sync_at: float = 0.0


def _is_enabled() -> bool:
    """总开关：LITELLM_HEALTH_ENABLED，默认 false。

    默认关闭是为了避免 dev 环境继续产生全模型探活费用；需要模型健康灰度时再显式开启。
    """
    return os.environ.get("LITELLM_HEALTH_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_refresh_interval() -> float:
    """读取探测间隔（秒）。每轮循环都读一次：运维改 env + 重启即可即时调节，无需改代码。"""
    return float(os.environ.get("LITELLM_HEALTH_INTERVAL_SECONDS", str(_DEFAULT_REFRESH_INTERVAL_SECONDS)))


def _claim_ttl_seconds() -> int:
    """round-claim 锁的 TTL。

    取探测间隔（保证一个周期内最多一轮探测），下限 300s 防止 interval 误配得太短
    导致探测超时（最长 90s）与锁过期重叠。
    """
    return max(int(_resolve_refresh_interval()), 300)


def _get_async_redis() -> redis.asyncio.Redis | None:
    """探测循环用的异步 Redis 客户端；未配置/创建失败返回 None。"""
    try:
        return get_redis_client()
    except Exception:
        return None


def _get_sync_redis() -> redis.Redis | None:
    """读侧用的同步 Redis 客户端（懒创建）；未配置返回 None。"""
    global _sync_redis_client
    if _sync_redis_client is None:
        if not settings.REDIS_URL:
            return None
        _sync_redis_client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
    return _sync_redis_client


def _build_alias_index(model_info: list[dict[str, Any]]) -> dict[str, str]:
    """从 /model/info 的 data 列表里抽 alias → model_id (UUID) 映射。

    LiteLLM 给每条 model 配置都会分配一个 UUID（model_info.id），它也是
    `/health` 返回 entries 里的 `model_id` 字段。两个端点拿不同字段，必须
    自己拼起来。
    """
    index: dict[str, str] = {}
    for entry in model_info:
        alias = entry.get("model_name")
        uuid = (entry.get("model_info") or {}).get("id")
        if alias and uuid:
            index[alias] = uuid
    return index


def _classify_error(raw_error: str) -> str:
    """把 LiteLLM 抛出的 stack trace 翻成给用户看的中文一句话。

    分类思路：先看异常类型（AuthenticationError / NotFoundError / BadRequest），
    再看消息体里的关键词（invalid api key / not activated / Terms Of Service /
    only support stream / quota / rate limit / timeout）。识别不到时 fallback
    到 "服务商暂时不可用"。
    """
    if not raw_error:
        return "服务商暂时不可用"

    head = raw_error.split("\n", 1)[0]
    lower = head.lower()

    if (
        "authenticationerror" in lower
        or "invalid api key" in lower
        or "invalid authentication" in lower
        or "authorized_error" in lower
        or '"http_code":"401"' in head
        or " 401 " in head
    ):
        return "服务商认证失败：API key 无效或已过期，请联系管理员补全密钥"

    if "has not activated the model" in head or "activate the model service" in lower:
        return "服务商账号未开通此模型，请到服务商控制台启用后再用"

    if "terms of service" in lower or "prohibited" in lower or '"code":403' in head or " 403 " in head:
        return "请求被服务商拒绝（额度/合规策略），暂不可用"

    if "only support stream" in lower or "stream parameter" in lower:
        return "调用参数不兼容（此模型仅支持流式调用），已在排查"

    if "model_not_found" in lower or "does not exist" in head.lower() or "permission denied" in lower:
        return "模型不存在或当前账号无权访问"

    if "rate limit" in lower or "ratelimit" in lower or "quota" in lower or "insufficient" in lower or " 429 " in head:
        return "服务商额度不足或被限流，稍后再试"

    if "timeout" in lower or "connectionerror" in lower:
        return "连接服务商超时，稍后再试"

    return "服务商暂时不可用"


async def _try_claim_round() -> bool:
    """抢「本轮探测权」。只有抢到的实例才允许打 /health，其它实例本轮跳过。

    Redis 不可用/抢锁失败一律返回 False：宁可跳过本轮，也不能失去协调地
    重复探测烧钱。
    """
    client = _get_async_redis()
    if client is None:
        return False
    try:
        nonce = uuid.uuid4().hex
        acquired = await client.set(_PROBE_CLAIM_KEY, nonce, nx=True, ex=_claim_ttl_seconds())
        return bool(acquired)
    except Exception as exc:
        logger.warning("litellm_health: redis claim failed, skip this round: %s", exc)
        return False


async def _write_snapshot(by_alias: dict[str, dict[str, Any]], checked_at: float) -> None:
    """把健康快照写 Redis，供同一服务其它 worker / 其它服务读取。失败不影响本地状态。"""
    client = _get_async_redis()
    if client is None:
        return
    try:
        payload = json.dumps({"checked_at": checked_at, "by_alias": by_alias})
        await client.set(_SNAPSHOT_KEY, payload, ex=_SNAPSHOT_TTL_SECONDS)
    except Exception as exc:
        logger.warning("litellm_health: write snapshot failed: %s", exc)


async def _fetch_once() -> None:
    """跑一次完整的探测，更新 _by_alias。失败时保留旧数据。"""
    base_url = settings.LITELLM_BASE_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.LITELLM_API_KEY}"} if settings.LITELLM_API_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_REQUEST_TIMEOUT, headers=headers) as client:
            info_resp, health_resp = await asyncio.gather(
                client.get(f"{base_url}/model/info"),
                client.get(f"{base_url}/health"),
            )
            info_resp.raise_for_status()
            health_resp.raise_for_status()
            info_data = info_resp.json().get("data", []) or []
            health_data = health_resp.json() or {}
    except Exception as exc:
        logger.warning("litellm_health: probe failed (keeping stale data): %s", exc)
        return

    alias_to_uuid = _build_alias_index(info_data)
    healthy_uuids = {e.get("model_id") for e in (health_data.get("healthy_endpoints") or []) if e.get("model_id")}
    unhealthy_by_uuid: dict[str, str] = {}
    for e in health_data.get("unhealthy_endpoints") or []:
        uuid = e.get("model_id")
        if uuid:
            unhealthy_by_uuid[uuid] = _classify_error(e.get("error") or "")

    new_state: dict[str, dict[str, Any]] = {}
    for alias, uuid in alias_to_uuid.items():
        if uuid in healthy_uuids:
            new_state[alias] = {"status": "healthy", "error": None}
        elif uuid in unhealthy_by_uuid:
            new_state[alias] = {"status": "unhealthy", "error": unhealthy_by_uuid[uuid] or "探测失败"}
        # 既不在 healthy 也不在 unhealthy：不写入，get_health 兜底返回 unknown

    checked_at = time.time()
    with _lock:
        global _last_checked_at
        _by_alias.clear()
        _by_alias.update(new_state)
        _last_checked_at = checked_at
    await _write_snapshot(new_state, checked_at)
    logger.info(
        "litellm_health: probe done, healthy=%d, unhealthy=%d",
        sum(1 for v in new_state.values() if v["status"] == "healthy"),
        sum(1 for v in new_state.values() if v["status"] == "unhealthy"),
    )


def _sync_from_redis() -> None:
    """从 Redis 共享快照同步一次（限频 30s），合并进本地缓存。

    只有 enabled 时才读 Redis：关闭状态下保持进程内状态（重启后即 unknown，符合
    「关闭时健康状态使用 unknown 或已有 stale 数据」）。
    快照里出现的 alias 以快照为准（探测结果权威）；本地有、快照没有的 alias
    （如 record_success 标记过的）保留本地值。
    """
    if not _is_enabled():
        return
    global _last_redis_sync_at
    now = time.time()
    if now - _last_redis_sync_at < _READ_CACHE_TTL_SECONDS:
        return
    _last_redis_sync_at = now

    client = _get_sync_redis()
    if client is None:
        return
    try:
        raw = client.get(_SNAPSHOT_KEY)
        if not raw:
            return
        payload = json.loads(raw)
    except Exception as exc:
        logger.debug("litellm_health: redis snapshot read failed: %s", exc)
        return

    by_alias = payload.get("by_alias") or {}
    try:
        checked_at = float(payload.get("checked_at") or 0.0)
    except (TypeError, ValueError):
        checked_at = 0.0

    with _lock:
        global _last_checked_at
        merged: dict[str, dict[str, Any]] = {}
        for alias, entry in by_alias.items():
            if isinstance(entry, dict):
                merged[alias] = {"status": entry.get("status", "unknown"), "error": entry.get("error")}
        for alias, entry in _by_alias.items():
            if alias not in merged:
                merged[alias] = entry
        _by_alias.clear()
        _by_alias.update(merged)
        if checked_at > _last_checked_at:
            _last_checked_at = checked_at


async def _refresh_loop() -> None:
    try:
        while True:
            if await _try_claim_round():
                await _fetch_once()
            else:
                logger.debug("litellm_health: probe round claimed by another instance, skip")
            await asyncio.sleep(_resolve_refresh_interval())
    except asyncio.CancelledError:
        logger.info("litellm_health: refresh loop cancelled")
        raise


async def start() -> None:
    """在 startup 阶段调用。LITELLM_HEALTH_ENABLED=false 时不启动任何后台循环。"""
    global _refresh_task
    if not _is_enabled():
        logger.info("litellm_health: disabled (LITELLM_HEALTH_ENABLED=false), background refresh NOT started")
        return
    if _refresh_task is None or _refresh_task.done():
        _refresh_task = asyncio.create_task(_refresh_loop(), name="litellm_health_refresh")
        logger.info("litellm_health: background refresh started, interval=%ss", _resolve_refresh_interval())


async def stop() -> None:
    """在 shutdown 阶段调用。"""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        _refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _refresh_task
        _refresh_task = None


def get_health(alias: str) -> dict[str, Any]:
    """返回某个 alias 的当前健康。未探测过的返回 status=unknown。"""
    _sync_from_redis()
    with _lock:
        entry = _by_alias.get(alias)
        if entry is None:
            return {"status": "unknown", "error": None, "checked_at": _last_checked_at or None}
        return {**entry, "checked_at": _last_checked_at or None}


def has_data() -> bool:
    """有没有探测过至少一次（用于乐观 fallback：还没探完就别全灰）。"""
    _sync_from_redis()
    with _lock:
        return _last_checked_at > 0
