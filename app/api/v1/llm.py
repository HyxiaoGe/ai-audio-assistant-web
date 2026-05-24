"""LLM 模型目录 API。

薄代理：直接读取 LiteLLM Proxy 的模型目录，避免在本仓库重复维护
provider/model 列表。所有项目共享同一个 LiteLLM 模型注册表，
新增/下线模型只需要在 LiteLLM 那边操作。

返回字段保留兼容旧前端的形状（`provider` / `model_id` / `display_name` /
`description` / `is_available` / `is_recommended`），新增字段尽量复用
LiteLLM 自身的 `model_info.metadata`。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.response import success

logger = logging.getLogger("app.api.llm")

router = APIRouter(prefix="/llm", tags=["llm"])

# 拉 LiteLLM 目录的超时不要太长，前端是同步等待的
_CATALOG_TIMEOUT = 5.0

# cost_tier 用来排序 + 决定推荐项；low 最便宜、最优先
_COST_TIER_ORDER = {"low": 0, "mid": 1, "high": 2}
_COST_TIER_PRIORITY = {"low": 1, "mid": 2, "high": 3}


def _normalize_provider(provider_display: str | None, underlying_model: str | None) -> str:
    """把 metadata.provider_display 或底层 model 名归一化成稳定的 provider key。

    前端按 `provider` 字段做分组展示，所以需要一个稳定的小写 key
    （"google" / "openai" / ...），而不是给人看的显示名（"Google"）。
    """
    if provider_display:
        return provider_display.strip().lower().replace(" ", "_")
    if underlying_model and "/" in underlying_model:
        return underlying_model.split("/", 1)[0].lower()
    return "litellm"


@router.get("/models")
async def get_available_models(request: Request) -> JSONResponse:
    """返回 LiteLLM 中已配置的业务模型别名。

    数据来源：LiteLLM Proxy 的 `/model/info`（模型清单 + metadata）
    与 `/health`（每个底层模型的可用性，由 LiteLLM 后台健康检查驱动）。
    """
    base_url = settings.LITELLM_BASE_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.LITELLM_API_KEY}"} if settings.LITELLM_API_KEY else {}

    try:
        async with httpx.AsyncClient(timeout=_CATALOG_TIMEOUT) as client:
            info_task = client.get(f"{base_url}/model/info", headers=headers)
            health_task = client.get(f"{base_url}/health", headers=headers)
            info_resp, health_resp = await asyncio.gather(info_task, health_task)
            info_resp.raise_for_status()
            # /health 偶尔会因为某个上游短暂不可达返回 200 但 body 不全，宽松处理
            health_payload: dict[str, Any] = {}
            if health_resp.status_code == 200:
                health_payload = health_resp.json()
            info_payload: dict[str, Any] = info_resp.json()
    except Exception as exc:
        logger.warning("fetch LiteLLM catalog failed: %s", exc)
        return success(data={"models": []})

    # 把底层 model 名映射到健康状态：出现在 healthy_endpoints 的才算 healthy
    healthy_underlying = {
        item.get("model") for item in (health_payload.get("healthy_endpoints") or []) if item.get("model")
    }
    has_health_data = bool(healthy_underlying) or bool(health_payload.get("unhealthy_endpoints"))

    models: list[dict[str, Any]] = []
    for entry in info_payload.get("data", []):
        alias = entry.get("model_name")
        if not alias:
            continue
        model_info = entry.get("model_info") or {}
        metadata = model_info.get("metadata") or {}
        underlying = (entry.get("litellm_params") or {}).get("model")

        provider_display = metadata.get("provider_display")
        cost_tier = metadata.get("cost_tier") or "mid"

        # 没拿到 /health 数据就乐观地认为可用，避免拉清单时短暂故障让前端全灰
        is_available = (underlying in healthy_underlying) if has_health_data else True

        models.append(
            {
                "provider": _normalize_provider(provider_display, underlying),
                "model_id": alias,
                "display_name": metadata.get("display_name") or alias,
                "description": metadata.get("description") or "",
                "cost_per_million_tokens": 0,  # LiteLLM 自己有更精细的 cost，前端目前不展示
                "priority": _COST_TIER_PRIORITY.get(cost_tier, 5),
                "status": "healthy" if is_available else "unhealthy",
                "is_available": is_available,
                "is_recommended": False,  # 下面统一计算
                "cost_tier": cost_tier,
                "recommended_for": metadata.get("recommended_for") or [],
                "provider_display": provider_display or "",
            }
        )

    # 推荐：第一个 cost_tier=low 且可用的别名。没 low 就退一档。
    recommended_id: str | None = None
    for tier in ("low", "mid", "high"):
        for m in models:
            if m["cost_tier"] == tier and m["is_available"]:
                recommended_id = m["model_id"]
                break
        if recommended_id:
            break
    for m in models:
        m["is_recommended"] = m["model_id"] == recommended_id

    # 按 (不可用沉底, cost_tier, alias) 排序，保证 picker 顺序稳定
    models.sort(
        key=lambda m: (
            not m["is_available"],
            _COST_TIER_ORDER.get(m["cost_tier"], 5),
            m["model_id"],
        )
    )

    # locale 暂时不用：description/display_name 全部在 LiteLLM metadata 里设
    _lang = getattr(request.state, "locale", "zh")
    del _lang

    return success(data={"models": models})
