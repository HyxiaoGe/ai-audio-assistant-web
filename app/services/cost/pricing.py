"""配图计价:每张图按其 model_id 取单价(¥),无配置时回落默认价。

远端 image-service 不回成本,故配图成本只能 app 侧估算。单价随模型/调价在 config 维护
(IMAGE_COST_CNY_BY_MODEL 覆盖、IMAGE_COST_CNY_DEFAULT 兜底),不硬编码在逻辑里。
"""

from __future__ import annotations

from app.config import settings


def price_for_image_model(model: str | None) -> float:
    """单张图的估算成本(¥)。model 为空或未在 BY_MODEL 配置时用默认价。"""
    by_model = settings.IMAGE_COST_CNY_BY_MODEL or {}
    if model and model in by_model:
        return float(by_model[model])
    return float(settings.IMAGE_COST_CNY_DEFAULT)
