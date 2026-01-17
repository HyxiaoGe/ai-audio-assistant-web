from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SummaryItem(BaseModel):
    id: str
    summary_type: str
    version: int
    is_active: bool
    content: str
    model_used: Optional[str] = None
    prompt_version: Optional[str] = None
    token_count: Optional[int] = None
    created_at: datetime
    # Visual summary fields
    visual_format: Optional[str] = None
    image_url: Optional[str] = None


class SummaryListResponse(BaseModel):
    task_id: str
    total: int
    items: list[SummaryItem]


class SummaryRegenerateRequest(BaseModel):
    """重新生成摘要请求"""

    summary_type: Literal["overview", "key_points", "action_items"] = Field(
        description="要重新生成的摘要类型"
    )
    provider: Optional[str] = Field(
        default=None,
        description="服务提供商（如 doubao, deepseek, openrouter），为 None 则自动选择",
    )
    model_id: Optional[str] = Field(
        default=None, description="模型ID（如 deepseek-chat, openai/gpt-4o）"
    )


class ModelSelection(BaseModel):
    """模型选择（provider + model_id）"""

    provider: str = Field(description="服务提供商（如 doubao, deepseek, openrouter）")
    model_id: Optional[str] = Field(
        default=None, description="模型ID（如 openai/gpt-4o），用于支持多模型的服务"
    )


class SummaryCompareRequest(BaseModel):
    """多模型对比请求"""

    summary_type: Literal["overview", "key_points", "action_items"] = Field(
        description="要对比的摘要类型"
    )
    models: list[ModelSelection] = Field(
        min_length=2,
        max_length=5,
        description="要对比的模型列表（2-5个模型，每个包含 provider 和可选的 model_id）",
    )


class SummaryComparisonItem(BaseModel):
    """单个对比结果"""

    model: str
    content: str
    token_count: Optional[int] = None
    created_at: datetime
    status: str = "completed"  # completed, generating, failed


class SummaryComparisonResponse(BaseModel):
    """对比结果响应"""

    comparison_id: str
    task_id: str
    summary_type: str
    models: list[str]
    results: list[SummaryComparisonItem]


# ===== Visual Summary Schemas =====


class VisualSummaryRequest(BaseModel):
    """可视化摘要生成请求"""

    visual_type: Literal["mindmap", "timeline", "flowchart"] = Field(
        description="可视化类型：mindmap(思维导图), timeline(时间轴), flowchart(流程图)"
    )
    content_style: Optional[str] = Field(
        default=None,
        description="内容风格 (meeting/lecture/podcast/video/general)，为 None 时自动检测",
    )
    provider: Optional[str] = Field(
        default=None, description="LLM 服务提供商（如 doubao, deepseek），为 None 则自动选择"
    )
    model_id: Optional[str] = Field(
        default=None, description="模型 ID（如 deepseek-chat），用于支持多模型的服务"
    )
    generate_image: bool = Field(default=True, description="是否生成 PNG/SVG 图片（后端渲染）")
    image_format: Literal["png", "svg"] = Field(
        default="png", description="图片格式（当 generate_image=True 时）"
    )


class VisualSummaryResponse(BaseModel):
    """可视化摘要响应"""

    id: str
    task_id: str
    visual_type: str
    format: str  # "mermaid"
    content: str  # Mermaid 语法代码
    image_url: Optional[str] = None  # 生成的图片 URL（如果有）
    model_used: Optional[str] = None
    token_count: Optional[int] = None
    created_at: datetime
