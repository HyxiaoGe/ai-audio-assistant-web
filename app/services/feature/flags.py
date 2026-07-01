from __future__ import annotations

from app.core.config_manager import ConfigManager


def is_discover_enabled() -> bool:
    """发现功能是否启用。

    - 无 DB 配置行 → ConfigManager 回退到 settings/默认 → enabled=True(默认开)。
    - admin 关过 → 缓存/DB 里 enabled=False。
    - 极端读不到(schema 未注册 / DB 全挂)→ 兜 True(fail-open,审核层仍兜底)。
    """
    try:
        return bool(ConfigManager.get_config("feature", "discover").enabled)
    except Exception:
        return True
