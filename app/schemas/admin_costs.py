"""管理员成本看板响应 schema。

双币种刻意分两列、绝不在后端跨币种求和:ASR/配图为人民币(¥),LLM 为美元($)。
cny_total 只合并同币种(ASR + 配图);llm_usd 单列,LiteLLM 来源不可用时为 None。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UserCostRow(BaseModel):
    user_id: str
    display_name: str | None = None
    # 该行是否为发起请求的管理员自己(前端据此给名字加「(你)」);默认 False 保持向后兼容。
    is_self: bool = False

    # 人民币列(¥):厂商直连按用量计 + 配图按张估。
    asr_cny: float
    asr_paid_cny: float  # 扣免费额度后实付(estimated 为毛成本)
    asr_calls: int
    image_cny: float
    cny_total: float  # asr_cny + image_cny(同币种,可合并)

    # 美元列($):LiteLLM 记账的 end-user spend;来源不可用(无 master key)时为 None。
    llm_usd: float | None = None


class AdminCostsResponse(BaseModel):
    items: list[UserCostRow]
    # "litellm" = 已读到 LiteLLM spend;"unavailable" = 无 master key,LLM 列为 None。
    llm_source: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    currency_note: str
