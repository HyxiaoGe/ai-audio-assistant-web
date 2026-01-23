"""ASR 调度器 - 免费额度+健康+成本联合决策

提供智能的 ASR 提供商选择算法，综合考虑：
1. 免费额度（优先使用有剩余免费额度的平台）
2. 健康状态（服务是否可用）
3. 成本（单价）
4. 配额余量（用户预算限制）

Author: AI Audio Assistant Team
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.asr_free_quota import FREE_QUOTA_CONFIGS
from app.core.registry import ServiceRegistry

logger = logging.getLogger(__name__)


@dataclass
class ProviderScore:
    """提供商评分"""

    provider: str
    variant: str
    free_quota_score: float  # 0-1 (免费额度剩余比例)
    health_score: float  # 0-1
    cost_score: float  # 0-1 (成本得分，越低越好)
    quota_score: float  # 0-1 (用户配额剩余比例)
    total_score: float  # 综合得分
    remaining_free_seconds: float  # 剩余免费额度（秒）


class ASRScheduler:
    """ASR 调度器

    提供配额+健康+成本的联合决策算法。
    """

    # 权重配置 - 免费额度优先
    DEFAULT_WEIGHTS = {
        "free_quota": 0.40,  # 免费额度优先
        "health": 0.25,
        "cost": 0.20,
        "quota": 0.15,  # 用户预算限制
    }

    # 各提供商每小时成本（元）- 标准版
    # 2025 年参考价格：
    # - 腾讯云：标准版 ¥1.25/小时，极速版 ¥3.10/小时
    # - 阿里云：标准版 ¥2.5/小时，极速版 ¥3.3/小时
    # - 火山引擎：标准版 ¥0.8/小时，流式 ¥1.2/小时
    COST_PER_HOUR = {
        "tencent": 1.25,
        "aliyun": 2.5,
        "volcengine": 0.8,
    }

    # 极速版成本
    COST_PER_HOUR_FAST = {
        "tencent": 3.10,
        "aliyun": 3.3,
        "volcengine": 1.2,
    }

    # 最高成本上限（用于归一化）
    MAX_COST_PER_HOUR = 5.0

    @classmethod
    async def select_best_provider(
        cls,
        session: Session | AsyncSession,
        user_id: Optional[str] = None,
        variant: str = "file",
        preferred_providers: Optional[List[str]] = None,
        weights: Optional[Dict[str, float]] = None,
        estimated_duration: Optional[float] = None,
    ) -> Optional[str]:
        """选择最佳 ASR 提供商

        综合考虑：
        1. 免费额度（优先使用有剩余免费额度的平台）
        2. 健康状态（是否可用）
        3. 成本（单价）
        4. 用户配额限制

        Args:
            session: 数据库会话
            user_id: 用户 ID（可选，用于用户级配额）
            variant: ASR 变体 (file, file_fast)
            preferred_providers: 优先考虑的提供商列表
            weights: 权重配置
            estimated_duration: 预估时长（秒）

        Returns:
            最佳提供商名称，如果都不可用则返回 None
        """
        from app.services.asr_quota_service import (
            get_quota_providers,
            select_available_provider,
        )

        weights = weights or cls.DEFAULT_WEIGHTS
        all_providers = ServiceRegistry.list_services("asr")

        if not all_providers:
            logger.warning("No ASR providers registered")
            return None

        if preferred_providers:
            providers = [p for p in preferred_providers if p in all_providers]
            if not providers:
                providers = all_providers
        else:
            providers = all_providers

        # 1. 获取有配额的提供商（用户预算限制）
        available = await select_available_provider(session, providers, user_id, variant=variant)

        # 如果没有配额记录，所有提供商都可用
        quota_providers = await get_quota_providers(session, providers, user_id, variant=variant)

        if not quota_providers:
            # 没有配额限制，所有提供商都可用
            available = providers
        elif not available:
            # 有配额限制但都用完了，检查是否有未配置配额的提供商
            unlimited = [p for p in providers if p not in quota_providers]
            if unlimited:
                available = unlimited
            else:
                logger.warning("No ASR providers available due to quota exhaustion")
                return None

        if not available:
            return None

        # 2. 计算每个提供商的得分
        scores: List[ProviderScore] = []

        for provider in available:
            # 健康得分
            health_score = await cls._get_health_score(provider)
            if health_score <= 0:
                continue  # 跳过不健康的服务

            # 免费额度得分（新增）
            free_quota_score, remaining_free = await cls._get_free_quota_score(
                session, provider, variant, user_id
            )

            # 成本得分
            cost_score = cls._get_cost_score(provider, variant)

            # 用户配额得分
            quota_score = await cls._get_quota_score(session, provider, user_id, variant)

            # 计算综合得分
            total_score = (
                free_quota_score * weights.get("free_quota", 0.40)
                + health_score * weights.get("health", 0.25)
                + cost_score * weights.get("cost", 0.20)
                + quota_score * weights.get("quota", 0.15)
            )

            scores.append(
                ProviderScore(
                    provider=provider,
                    variant=variant,
                    free_quota_score=free_quota_score,
                    health_score=health_score,
                    cost_score=cost_score,
                    quota_score=quota_score,
                    total_score=total_score,
                    remaining_free_seconds=remaining_free,
                )
            )

        if not scores:
            return None

        # 3. 选择得分最高的
        best = max(scores, key=lambda s: s.total_score)

        logger.info(
            "Selected ASR provider: %s (free_quota=%.2f, health=%.2f, cost=%.2f, "
            "quota=%.2f, total=%.2f, remaining_free=%.0fs)",
            best.provider,
            best.free_quota_score,
            best.health_score,
            best.cost_score,
            best.quota_score,
            best.total_score,
            best.remaining_free_seconds,
        )

        return best.provider

    @classmethod
    async def get_provider_scores(
        cls,
        session: Session | AsyncSession,
        user_id: Optional[str] = None,
        variant: str = "file",
        providers: Optional[List[str]] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> List[ProviderScore]:
        """获取所有提供商的评分（用于调试/API 返回）

        Args:
            session: 数据库会话
            user_id: 用户 ID
            variant: ASR 变体
            providers: 提供商列表（默认所有）
            weights: 权重配置

        Returns:
            评分列表，按总分降序排列
        """
        weights = weights or cls.DEFAULT_WEIGHTS
        all_providers = providers or ServiceRegistry.list_services("asr")
        scores: List[ProviderScore] = []

        for provider in all_providers:
            health_score = await cls._get_health_score(provider)
            free_quota_score, remaining_free = await cls._get_free_quota_score(
                session, provider, variant, user_id
            )
            cost_score = cls._get_cost_score(provider, variant)
            quota_score = await cls._get_quota_score(session, provider, user_id, variant)

            total_score = (
                free_quota_score * weights.get("free_quota", 0.40)
                + health_score * weights.get("health", 0.25)
                + cost_score * weights.get("cost", 0.20)
                + quota_score * weights.get("quota", 0.15)
            )

            scores.append(
                ProviderScore(
                    provider=provider,
                    variant=variant,
                    free_quota_score=free_quota_score,
                    health_score=health_score,
                    cost_score=cost_score,
                    quota_score=quota_score,
                    total_score=total_score,
                    remaining_free_seconds=remaining_free,
                )
            )

        # 按总分降序排列
        scores.sort(key=lambda s: s.total_score, reverse=True)
        return scores

    @classmethod
    async def _get_free_quota_score(
        cls,
        session: Session | AsyncSession,
        provider: str,
        variant: str,
        user_id: Optional[str] = None,
    ) -> tuple[float, float]:
        """获取免费额度得分

        Args:
            session: 数据库会话
            provider: 提供商
            variant: 服务变体
            user_id: 用户ID

        Returns:
            (score, remaining_free_seconds) 元组
            score: 0-1，有免费额度返回 1.0，无则返回 0.0
        """
        from app.services.asr_free_quota_service import AsrFreeQuotaService

        # 获取免费额度配置
        config = FREE_QUOTA_CONFIGS.get((provider, variant))
        if not config or config.free_seconds <= 0:
            return 0.0, 0.0

        # 获取剩余免费额度
        try:
            remaining = await AsrFreeQuotaService.get_remaining_free_quota(
                session, provider, variant, user_id
            )
            if remaining > 0:
                # 有剩余免费额度，得分 = 剩余比例
                score = remaining / config.free_seconds
                return score, remaining
            return 0.0, 0.0
        except Exception as e:
            logger.warning("Failed to get free quota for %s/%s: %s", provider, variant, e)
            return 0.0, 0.0

    @classmethod
    async def _get_health_score(cls, provider: str) -> float:
        """获取健康得分

        TODO: 后续集成 HealthChecker
        """
        # 简化版：假设所有服务都健康
        # 后续可以从 HealthChecker 获取真实健康状态
        try:
            from app.core.health_checker import HealthChecker, HealthStatus

            result = await HealthChecker.check_service("asr", provider)
            if result.status == HealthStatus.HEALTHY:
                return 1.0
            elif result.status == HealthStatus.UNHEALTHY:
                return 0.0
            else:
                # UNKNOWN, CHECKING 等状态假设可用
                return 0.5
        except Exception:
            # HealthChecker 未配置或出错，假设健康
            return 1.0

    @classmethod
    async def _get_quota_score(
        cls,
        session: Session | AsyncSession,
        provider: str,
        user_id: Optional[str],
        variant: str,
    ) -> float:
        """获取配额得分（剩余比例）

        返回 0-1，剩余配额越多得分越高
        """
        from sqlalchemy import or_, select

        from app.models.asr_quota import AsrQuota
        from app.services.asr_quota_service import (
            _active_window_clause,
            _effective_quotas,
            _execute,
            _extract_scalars,
        )

        now = datetime.now(timezone.utc)

        result = await _execute(
            session,
            select(AsrQuota)
            .where(AsrQuota.provider == provider)
            .where(AsrQuota.variant == variant)
            .where(_active_window_clause(now))
            .where(or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == user_id)),
        )
        rows = _extract_scalars(result)

        if not rows:
            # 没有配额记录，无限制
            return 1.0

        key = (provider, variant)
        effective = _effective_quotas(rows, [key], user_id).get(key, [])

        if not effective:
            return 1.0

        # 计算平均剩余比例
        total_quota = sum(q.quota_seconds for q in effective)
        total_used = sum(q.used_seconds for q in effective)

        if total_quota <= 0:
            return 0.0

        remaining_ratio = max(0, (total_quota - total_used) / total_quota)
        return remaining_ratio

    @classmethod
    def _get_cost_score(cls, provider: str, variant: str = "file") -> float:
        """获取成本得分

        返回 0-1，成本越低得分越高

        Args:
            provider: 提供商名称
            variant: 服务变体 (file=标准版, file_fast=极速版)
        """
        if variant == "file_fast":
            cost_per_hour = cls.COST_PER_HOUR_FAST.get(provider, 2.0)
        else:
            cost_per_hour = cls.COST_PER_HOUR.get(provider, 1.0)

        # 归一化：成本越低得分越高
        score = max(0, 1.0 - (cost_per_hour / cls.MAX_COST_PER_HOUR))
        return score

    @classmethod
    def get_all_scores(cls, scores: List[ProviderScore]) -> Dict[str, Dict[str, float]]:
        """获取所有提供商的评分详情（用于调试/监控）"""
        return {
            score.provider: {
                "health": score.health_score,
                "quota": score.quota_score,
                "cost": score.cost_score,
                "total": score.total_score,
            }
            for score in scores
        }
