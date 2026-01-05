from __future__ import annotations

from typing import Any, Mapping


def get_config_value(config: Any, key: str, fallback: Any) -> Any:
    if config is None:
        return fallback
    if isinstance(config, Mapping):
        value = config.get(key)
        return fallback if value is None else value
    value = getattr(config, key, None)
    return fallback if value is None else value
