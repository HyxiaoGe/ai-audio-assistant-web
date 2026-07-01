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
