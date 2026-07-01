from __future__ import annotations

from app.core.config_manager import ServiceConfig, register_config_schema


@register_config_schema("feature", "discover")
class DiscoverFeatureConfig(ServiceConfig):
    """发现(/discover)功能 kill-switch。仅用继承来的 enabled(默认 True=开)。"""
