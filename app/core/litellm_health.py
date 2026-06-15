"""LiteLLM `/health` 探测的后台缓存层。

为什么要单独搞一个模块：
- `/health` 会真的对每个 model 打一次 completion，单次 5~30s 不止；不能放
  在 `/api/v1/llm/models` 请求路径上同步触发（之前就是同步调，所以 picker
  偶尔会被 LiteLLM 慢响应拖到 5s 超时）。
- 但前端又需要知道哪些 alias 当前是真的能用、哪些挂了。

设计：
- startup 起一个后台 asyncio 任务，固定间隔（默认 5min）拉一次 `/health`
- 把结果按 LiteLLM 内部 `model_id` (UUID) 索引，并通过 `/model/info` 的
  UUID → alias 映射，反推每个 alias 当前是 healthy / unhealthy / unknown
- 首次未拉到时，所有 alias 返回 status="unknown"，调用方按 healthy 处理
  （避免冷启动期间整个 picker 被误灰）
- 探测失败（网络问题、proxy 重启）不会清空上一次的结果——保留 stale 数据
  比突然全清空更稳

对外接口：get_health(alias) -> {status, error, checked_at}。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 探测间隔默认值（秒）。/health 会对 LiteLLM DB 里每个模型各打一次真实 completion，
# 其中 qwen 等 reasoning 模型每次会生成数百 reasoning token（且 enable_thinking /
# max_tokens 都压不掉），探得越频繁、在服务商侧产生的真实费用越高。模型存活状态变化
# 很慢，30min 刷新足够；可用 LITELLM_HEALTH_INTERVAL_SECONDS 覆盖。
# 详见 https://github.com/HyxiaoGe/ai-audio-assistant-web/issues/68
_DEFAULT_REFRESH_INTERVAL_SECONDS = 1800.0


def _resolve_refresh_interval() -> float:
    """读取探测间隔（秒）。每轮循环都读一次：运维改 env + 重启即可即时调节，无需改代码。"""
    return float(
        os.environ.get("LITELLM_HEALTH_INTERVAL_SECONDS", str(_DEFAULT_REFRESH_INTERVAL_SECONDS))
    )
# 单次 `/health` 调用超时——LiteLLM 会并发探测所有端点，但慢的 provider 可能拖到 1min+
_HEALTH_REQUEST_TIMEOUT = float(os.environ.get("LITELLM_HEALTH_REQUEST_TIMEOUT", "90"))

_lock = threading.Lock()
# alias -> {"status": "healthy"|"unhealthy", "error": str|None}
_by_alias: dict[str, dict[str, Any]] = {}
_last_checked_at: float = 0.0
_refresh_task: asyncio.Task | None = None


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

    if (
        "rate limit" in lower
        or "ratelimit" in lower
        or "quota" in lower
        or "insufficient" in lower
        or " 429 " in head
    ):
        return "服务商额度不足或被限流，稍后再试"

    if "timeout" in lower or "connectionerror" in lower:
        return "连接服务商超时，稍后再试"

    return "服务商暂时不可用"


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

    with _lock:
        global _last_checked_at
        _by_alias.clear()
        _by_alias.update(new_state)
        _last_checked_at = time.time()
    logger.info(
        "litellm_health: probe done, healthy=%d, unhealthy=%d",
        sum(1 for v in new_state.values() if v["status"] == "healthy"),
        sum(1 for v in new_state.values() if v["status"] == "unhealthy"),
    )


async def _refresh_loop() -> None:
    try:
        while True:
            await _fetch_once()
            await asyncio.sleep(_resolve_refresh_interval())
    except asyncio.CancelledError:
        logger.info("litellm_health: refresh loop cancelled")
        raise


async def start() -> None:
    """在 startup 阶段调用。"""
    global _refresh_task
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
    with _lock:
        entry = _by_alias.get(alias)
        if entry is None:
            return {"status": "unknown", "error": None, "checked_at": _last_checked_at or None}
        return {**entry, "checked_at": _last_checked_at or None}


def has_data() -> bool:
    """有没有探测过至少一次（用于乐观 fallback：还没探完就别全灰）。"""
    with _lock:
        return _last_checked_at > 0
