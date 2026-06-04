from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SummaryImageItem(BaseModel):
    """overview 配图的单项状态（持久化于 summaries.images JSONB）。

    placeholder 既是 content 里的锚点，也是前端 Map 的 key（无额外 id）。
    """

    placeholder: str
    status: Literal["pending", "ready", "failed"]
    url: str | None = None
    alt: str = ""
    model_id: str | None = None
    error: str | None = None


class SummaryItem(BaseModel):
    id: str
    summary_type: str
    version: int
    is_active: bool
    content: str
    model_used: str | None = None
    prompt_version: str | None = None
    token_count: int | None = None
    created_at: datetime
    # Visual summary fields
    visual_format: str | None = None
    image_url: str | None = None
    image_model_used: str | None = None
    # Progressive disclosure: overview 配图状态集；非 overview/无图时为 None 或 []
    images: list[SummaryImageItem] | None = None


class SummaryListResponse(BaseModel):
    task_id: str
    total: int
    items: list[SummaryItem]


class SummaryRegenerateRequest(BaseModel):
    """重新生成摘要请求"""

    summary_type: Literal["overview", "key_points", "action_items"] = Field(description="要重新生成的摘要类型")
    provider: str | None = Field(
        default=None,
        description="服务提供商（如 doubao, deepseek, openrouter），为 None 则自动选择",
    )
    model_id: str | None = Field(default=None, description="模型ID（如 deepseek-chat, openai/gpt-4o）")


class ModelSelection(BaseModel):
    """模型选择（provider + model_id）"""

    provider: str = Field(description="服务提供商（如 doubao, deepseek, openrouter）")
    model_id: str | None = Field(default=None, description="模型ID（如 openai/gpt-4o），用于支持多模型的服务")


class SummaryCompareRequest(BaseModel):
    """多模型对比请求"""

    summary_type: Literal["overview", "key_points", "action_items"] = Field(description="要对比的摘要类型")
    models: list[ModelSelection] = Field(
        min_length=2,
        max_length=5,
        description="要对比的模型列表（2-5个模型，每个包含 provider 和可选的 model_id）",
    )


class SummaryComparisonItem(BaseModel):
    """单个对比结果"""

    model: str
    content: str
    token_count: int | None = None
    created_at: datetime
    status: str = "completed"  # completed, generating, failed


class SummaryComparisonResponse(BaseModel):
    """对比结果响应"""

    comparison_id: str
    task_id: str
    summary_type: str
    models: list[str]
    results: list[SummaryComparisonItem]
