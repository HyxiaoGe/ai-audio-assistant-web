from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.config_manager import ServiceConfig, register_config_schema


@register_config_schema("feature", "discover")
class DiscoverFeatureConfig(ServiceConfig):
    """发现(/discover)功能 kill-switch。仅用继承来的 enabled(默认 True=开)。"""


class CuratedTrendingItem(BaseModel):
    """一条精选热门词。展示文本即搜索词,故仅 query 一字段。"""

    query: str = Field(min_length=1)

    @field_validator("query")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        # 在 config 端点 PUT 时就拒掉空/全空白 query,避免存进无效死条目(运行时 reader 也会兜底丢弃)。
        stripped = value.strip()
        if not stripped:
            raise ValueError("query 不能为空或全空白")
        return stripped


@register_config_schema("feature", "discover_trending")
class DiscoverTrendingConfig(ServiceConfig):
    """/discover「大家在搜」精选覆盖。

    config.items 非空且 mode=="replace" 时,trending 端点只返回这份手动精选(绕过
    组织化热度排序),用于给一个干净、可控的列表——组织化搜索会混入垃圾词/超长词。

    ⚠️ 运行时读取**不走本 schema / ConfigManager**:trending 端点直读 service_configs
    行的 config jsonb 列(见 flags.get_curated_trending_queries),理由同 is_discover_enabled
    的 DOA 注解(异步上下文里 ConfigManager 同步加载返 None 回落默认、跨 worker 有 TTL 滞后)。
    本 schema 仅供 admin 走 config 端点 PUT 时校验 config 形状。items 给默认空 → `{}` 也合法。
    """

    mode: Literal["replace"] = "replace"
    items: list[CuratedTrendingItem] = Field(default_factory=list)
