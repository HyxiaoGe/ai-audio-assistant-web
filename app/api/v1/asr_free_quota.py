"""ASR 免费额度 API

提供免费额度查询和成本预估功能。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.asr_free_quota import FREE_QUOTA_CONFIGS
from app.core.asr_scheduler import ASRScheduler
from app.core.response import success
from app.schemas.asr_free_quota import (
    CostEstimateRequest,
    CostEstimateResponse,
    FreeQuotaListResponse,
    FreeQuotaStatusResponse,
    ProviderCostEstimate,
    ProviderScoreResponse,
    ProviderScoresResponse,
)
from app.services.asr_free_quota_service import AsrFreeQuotaService

router = APIRouter(prefix="/asr/free-quota", tags=["ASR Free Quota"])


@router.get("", response_model=dict)
async def get_free_quota_status(
    db: AsyncSession = Depends(get_db),
):
    """查询所有免费额度状态

    返回所有有免费额度的 ASR 服务的状态，包括：
    - 总免费额度
    - 已使用量
    - 剩余免费额度
    - 刷新周期
    - 单价
    """
    statuses = await AsrFreeQuotaService.get_all_free_quota_status(db)

    providers = []
    for status in statuses:
        free_hours = status.free_quota_seconds / 3600
        used_hours = status.used_seconds / 3600
        remaining_hours = status.remaining_seconds / 3600
        usage_percent = (
            (status.used_seconds / status.free_quota_seconds * 100)
            if status.free_quota_seconds > 0
            else 0
        )

        providers.append(
            FreeQuotaStatusResponse(
                provider=status.provider,
                variant=status.variant,
                free_quota_seconds=status.free_quota_seconds,
                used_seconds=status.used_seconds,
                remaining_seconds=status.remaining_seconds,
                reset_period=status.reset_period,
                period_start=status.period_start,
                period_end=status.period_end,
                cost_per_hour=status.cost_per_hour,
                free_quota_hours=round(free_hours, 2),
                used_hours=round(used_hours, 2),
                remaining_hours=round(remaining_hours, 2),
                usage_percent=round(usage_percent, 1),
            )
        )

    return success(data=FreeQuotaListResponse(providers=providers))


@router.post("/estimate-cost", response_model=dict)
async def estimate_cost(
    request: CostEstimateRequest,
    db: AsyncSession = Depends(get_db),
):
    """预估成本

    根据预计时长，计算各提供商的成本预估，包括：
    - 免费额度消耗
    - 付费时长
    - 预估成本
    - 推荐的提供商

    Args:
        duration_seconds: 预计时长（秒）
        variant: 服务变体 (file, file_fast)
    """
    estimates = []
    best_provider = None
    best_cost = float("inf")
    best_has_free = False

    for (provider, variant), config in FREE_QUOTA_CONFIGS.items():
        if variant != request.variant:
            continue

        estimate = await AsrFreeQuotaService.estimate_cost(
            db, provider, variant, request.duration_seconds
        )

        estimates.append(
            ProviderCostEstimate(
                provider=provider,
                variant=variant,
                total_duration=estimate["total_duration"],
                free_consumed=estimate["free_consumed"],
                paid_duration=estimate["paid_duration"],
                estimated_cost=round(estimate["estimated_cost"], 4),
                full_cost=round(estimate["full_cost"], 4),
                remaining_free_quota=estimate.get("remaining_free_quota", 0),
                cost_per_hour=estimate.get("cost_per_hour", 0),
            )
        )

        # 选择最佳提供商：优先有免费额度的，其次成本最低的
        has_free = estimate["free_consumed"] > 0
        estimated_cost = estimate["estimated_cost"]

        if has_free and not best_has_free:
            # 有免费额度的优先
            best_provider = provider
            best_cost = estimated_cost
            best_has_free = True
        elif has_free == best_has_free and estimated_cost < best_cost:
            # 相同情况下选成本低的
            best_provider = provider
            best_cost = estimated_cost
            best_has_free = has_free

    # 按成本排序
    estimates.sort(key=lambda x: x.estimated_cost)

    reason = None
    if best_provider:
        if best_has_free:
            reason = f"{best_provider} 有免费额度可用"
        else:
            reason = f"{best_provider} 成本最低"

    return success(
        data=CostEstimateResponse(
            estimates=estimates,
            recommended_provider=best_provider,
            recommendation_reason=reason,
        )
    )


@router.get("/provider-scores", response_model=dict)
async def get_provider_scores(
    variant: str = "file",
    db: AsyncSession = Depends(get_db),
):
    """获取提供商评分

    返回各提供商的综合评分，用于理解调度器的决策逻辑。

    评分维度：
    - free_quota: 免费额度得分 (权重 40%)
    - health: 健康得分 (权重 25%)
    - cost: 成本得分 (权重 20%)
    - quota: 用户配额得分 (权重 15%)

    Args:
        variant: 服务变体 (file, file_fast)
    """
    scores = await ASRScheduler.get_provider_scores(db, variant=variant)

    response_scores = [
        ProviderScoreResponse(
            provider=score.provider,
            variant=score.variant,
            free_quota_score=round(score.free_quota_score, 3),
            health_score=round(score.health_score, 3),
            cost_score=round(score.cost_score, 3),
            quota_score=round(score.quota_score, 3),
            total_score=round(score.total_score, 3),
            remaining_free_seconds=score.remaining_free_seconds,
        )
        for score in scores
    ]

    return success(
        data=ProviderScoresResponse(
            scores=response_scores,
            weights=ASRScheduler.DEFAULT_WEIGHTS,
        )
    )
