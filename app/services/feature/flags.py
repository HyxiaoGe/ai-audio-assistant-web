from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_config import ServiceConfig


async def is_discover_enabled(db: AsyncSession) -> bool:
    """发现(/discover)功能是否启用。

    直接读 service_configs 全局行(owner_user_id IS NULL)的 ``enabled`` 列——这是
    kill-switch 的唯一真源(admin 后台开关写的就是该列)。**刻意不走 ConfigManager
    实例缓存**:在异步请求上下文里,ConfigManager.get_config 对 feature 伪类型会因
    同步 DB 加载在事件循环内返回 None 而回落到 settings 默认(恒 True),且跨 uvicorn
    worker 有 TTL 滞后——两者都会让开关无法即时/可靠地拦截,kill-switch 形同虚设。
    直读列则即时生效、跨 worker 一致。

    - 无配置行 → 默认开(fail-safe default-on)。
    - admin 关过 → 列 enabled=False → 拦截。
    - 读不到(DB 异常)→ 兜 True(fail-open,内容审核层仍兜底)。
    """
    try:
        stmt = select(ServiceConfig.enabled).where(
            ServiceConfig.service_type == "feature",
            ServiceConfig.provider == "discover",
            ServiceConfig.owner_user_id.is_(None),
        )
        enabled = (await db.execute(stmt)).scalar_one_or_none()
        return True if enabled is None else bool(enabled)
    except Exception as exc:
        logger.warning("is_discover_enabled 读开关异常,fail-open 兜底为开: {}", exc)
        return True


async def get_curated_trending_queries(db: AsyncSession) -> list[str] | None:
    """读 /discover「大家在搜」精选覆盖(service_configs 全局行 feature/discover_trending 的 config jsonb)。

    直读 config 列而非走 ConfigManager——理由同 is_discover_enabled:异步上下文里 ConfigManager
    的同步 DB 加载会返 None 回落默认、跨 worker 有 TTL 滞后,读不到即时/一致的精选。

    返回:
    - 已配置且 ``mode=="replace"`` 且 items 有非空 query → 精选 query 列表(保持配置顺序,用于展示)。
    - 未配置行 / mode 非 replace / items 空或全无效 / 读错 → None(调用方回落组织化 get_trending)。

    读错兜 None(而非抛):精选只是"锦上添花"的展示覆盖,失败就回落组织化,不阻断 trending。
    """
    try:
        stmt = select(ServiceConfig.config).where(
            ServiceConfig.service_type == "feature",
            ServiceConfig.provider == "discover_trending",
            ServiceConfig.owner_user_id.is_(None),
        )
        cfg = (await db.execute(stmt)).scalar_one_or_none()
        if not isinstance(cfg, dict) or cfg.get("mode") != "replace":
            return None
        items = cfg.get("items")
        if not isinstance(items, list):  # 非 list(如直写脏数据成字符串)→ 别逐字符迭代出垃圾词,直接回落
            return None
        queries: list[str] = []
        for item in items:
            raw = item.get("query") if isinstance(item, dict) else item
            query = raw.strip() if isinstance(raw, str) else ""  # 非 str 的 query 跳过,不 str() 强转
            if query:
                queries.append(query)
        return queries or None
    except Exception as exc:
        logger.warning("get_curated_trending_queries 读精选异常,回落组织化: {}", exc)
        return None
