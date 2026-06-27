"""审核三态开关的唯一读取 seam。

当前实现:env(pydantic-settings)。这是 spec 明文授权的回退——ConfigManager 被硬编码
限定在 {llm,asr,storage},复用它需改动共享子系统,得不偿失。未来若要运行时翻档(不重启),
只需替换这两个函数的内部实现,gate 不动。
"""

from __future__ import annotations

from app.config import settings


def search_mode() -> str:
    """search_query 场景三态:off | shadow | enforce。"""
    return settings.MODERATION_SEARCH_MODE


def publish_mode() -> str:
    """ugc_publish 场景三态:off | shadow | enforce。"""
    return settings.MODERATION_PUBLISH_MODE


def display_mode() -> str:
    """ugc_display 场景三态:off | shadow | enforce。"""
    return settings.MODERATION_DISPLAY_MODE
