"""ASR 调度器 - 智能服务选择

提供智能的 ASR 提供商选择算法，综合考虑：
1. 免费额度（优先使用有剩余免费额度的平台）
2. 健康状态（服务是否可用）
3. 成本（单价）
4. 配额余量（用户预算限制）
5. 识别质量（新增）
6. 特殊功能匹配（新增：说话人分离、词级时间戳）

注意：定价配置从数据库 asr_pricing_configs 表读取，不再硬编码。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

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
    quality_score: float  # 0-1 (识别质量)
    features_score: float  # 0-1 (特殊功能匹配)
    total_score: float  # 综合得分
    remaining_free_seconds: float  # 剩余免费额度（秒）


@dataclass
class TaskFeatures:
    """任务特性需求"""

    diarization: bool = False  # 需要说话人分离
    word_level: bool = False  # 需要词级时间戳


class ASRScheduler:
    """ASR 调度器

    提供配额+健康+成本+质量+特性的联合决策算法。
    """

    # 权重配置 - 6 个维度
    DEFAULT_WEIGHTS = {
        "free_quota": 0.30,  # 免费额度优先
        "health": 0.20,  # 健康状态
        "cost": 0.15,  # 成本
        "quota": 0.10,  # 用户预算限制
        "quality": 0.15,  # 识别质量
        "features": 0.10,  # 特殊功能匹配
    }

    # 说话人分离场景权重
    DIARIZATION_WEIGHTS = {
        "free_quota": 0.20,
        "health": 0.15,
        "cost": 0.10,
        "quota": 0.10,
        "quality": 0.15,
        "features": 0.30,  # 提高特性权重
    }

    # 最高成本上限（用于归一化）
    MAX_COST_PER_HOUR = 5.0

    @classmethod
    def get_weights_for_task(
        cls,
        task_features: Optional[TaskFeatures] = None,
        custom_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """根据任务特性获取权重配置"""
        if custom_weights:
            return custom_weights

        if task_features and (task_features.diarization or task_features.word_level):
            return cls.DIARIZATION_WEIGHTS.copy()

        return cls.DEFAULT_WEIGHTS.copy()

    @classmethod
    async def select_best_provider(
        cls,
        session: Session | AsyncSession,
        user_id: Optional[str] = None,
        variant: str = "file",
        preferred_providers: Optional[List[str]] = None,
        weights: Optional[Dict[str, float]] = None,
        task_features: Optional[TaskFeatures] = None,
        estimated_duration: Optional[float] = None,
    ) -> Optional[str]:
        """选择最佳 ASR 提供商

        综合考虑：
        1. 免费额度（优先使用有剩余免费额度的平台）
        2. 健康状态（是否可用）
        3. 成本（单价）
        4. 用户配额限制
        5. 识别质量
        6. 特殊功能匹配

        Args:
            session: 数据库会话
            user_id: 用户 ID（可选，用于用户级配额）
            variant: ASR 变体 (file, file_fast)
            preferred_providers: 优先考虑的提供商列表
            weights: 权重配置（可选，默认根据任务特性自动选择）
            task_features: 任务特性需求
            estimated_duration: 预估时长（秒）

        Returns:
            最佳提供商名称，如果都不可用则返回 None
        """
        from app.services.asr_quota_service import (
            get_quota_providers,
            select_available_provider,
        )

        # 根据任务特性选择权重
        weights = weights or cls.get_weights_for_task(task_features)
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

        # 1. 获取有用户配额的提供商（用户预算限制）
        available = await select_available_provider(session, providers, user_id, variant=variant)
        quota_providers = await get_quota_providers(session, providers, user_id, variant=variant)

        # 2. 检查哪些提供商有平台免费额度剩余
        #    平台免费额度不受用户配额限制，应该优先使用
        providers_with_free_quota: List[str] = []
        for provider in providers:
            _, remaining_free = await cls._get_free_quota_score(session, provider, variant, user_id)
            if remaining_free > 0:
                providers_with_free_quota.append(provider)

        # 3. 合并可用列表：有用户配额的 + 有平台免费额度的
        if not quota_providers:
            # 没有配额限制，所有提供商都可用
            available = providers
        else:
            # 有配额限制，合并：有剩余配额的 + 有平台免费额度的 + 未配置配额的
            unlimited = [p for p in providers if p not in quota_providers]
            available_set = set(available or []) | set(providers_with_free_quota) | set(unlimited)
            available = list(available_set)

        if not available:
            logger.warning("No ASR providers available")
            return None

        # 2. 计算每个提供商的得分
        scores: List[ProviderScore] = []

        for provider in available:
            # 健康得分
            health_score = await cls._get_health_score(provider)
            if health_score <= 0:
                continue  # 跳过不健康的服务

            # 免费额度得分（从数据库读取）
            free_quota_score, remaining_free = await cls._get_free_quota_score(
                session, provider, variant, user_id
            )

            # 成本得分（从数据库读取）
            cost_score = await cls._get_cost_score(session, provider, variant)

            # 用户配额得分
            quota_score = await cls._get_quota_score(session, provider, user_id, variant)

            # 识别质量得分
            quality_score = await cls._get_quality_score(session, provider, variant)

            # 特殊功能匹配得分
            features_score = await cls._get_features_score(
                session, provider, variant, task_features
            )

            # 计算综合得分
            total_score = (
                free_quota_score * weights.get("free_quota", 0.30)
                + health_score * weights.get("health", 0.20)
                + cost_score * weights.get("cost", 0.15)
                + quota_score * weights.get("quota", 0.10)
                + quality_score * weights.get("quality", 0.15)
                + features_score * weights.get("features", 0.10)
            )

            scores.append(
                ProviderScore(
                    provider=provider,
                    variant=variant,
                    free_quota_score=free_quota_score,
                    health_score=health_score,
                    cost_score=cost_score,
                    quota_score=quota_score,
                    quality_score=quality_score,
                    features_score=features_score,
                    total_score=total_score,
                    remaining_free_seconds=remaining_free,
                )
            )

        if not scores:
            return None

        # 3. 选择得分最高的
        best = max(scores, key=lambda s: s.total_score)

        logger.info(
            "Selected ASR provider: %s (free=%.2f, health=%.2f, cost=%.2f, "
            "quota=%.2f, quality=%.2f, features=%.2f, total=%.2f, remaining=%.0fs)",
            best.provider,
            best.free_quota_score,
            best.health_score,
            best.cost_score,
            best.quota_score,
            best.quality_score,
            best.features_score,
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
        task_features: Optional[TaskFeatures] = None,
    ) -> List[ProviderScore]:
        """获取所有提供商的评分（用于调试/API 返回）

        Args:
            session: 数据库会话
            user_id: 用户 ID
            variant: ASR 变体
            providers: 提供商列表（默认所有）
            weights: 权重配置
            task_features: 任务特性需求

        Returns:
            评分列表，按总分降序排列
        """
        weights = weights or cls.get_weights_for_task(task_features)
        all_providers = providers or ServiceRegistry.list_services("asr")
        scores: List[ProviderScore] = []

        for provider in all_providers:
            health_score = await cls._get_health_score(provider)
            free_quota_score, remaining_free = await cls._get_free_quota_score(
                session, provider, variant, user_id
            )
            cost_score = await cls._get_cost_score(session, provider, variant)
            quota_score = await cls._get_quota_score(session, provider, user_id, variant)
            quality_score = await cls._get_quality_score(session, provider, variant)
            features_score = await cls._get_features_score(
                session, provider, variant, task_features
            )

            total_score = (
                free_quota_score * weights.get("free_quota", 0.30)
                + health_score * weights.get("health", 0.20)
                + cost_score * weights.get("cost", 0.15)
                + quota_score * weights.get("quota", 0.10)
                + quality_score * weights.get("quality", 0.15)
                + features_score * weights.get("features", 0.10)
            )

            scores.append(
                ProviderScore(
                    provider=provider,
                    variant=variant,
                    free_quota_score=free_quota_score,
                    health_score=health_score,
                    cost_score=cost_score,
                    quota_score=quota_score,
                    quality_score=quality_score,
                    features_score=features_score,
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
            score: 0-1，有免费额度返回剩余比例，无则返回 0.0
        """
        from app.services.asr_free_quota_service import AsrFreeQuotaService
        from app.services.asr_pricing_service import get_pricing_config

        # 从数据库获取定价配置
        config = await get_pricing_config(session, provider, variant)
        if not config or config.free_quota_seconds <= 0:
            return 0.0, 0.0

        # 获取剩余免费额度
        try:
            remaining = await AsrFreeQuotaService.get_remaining_free_quota(
                session, provider, variant, user_id
            )
            if remaining > 0:
                # 有剩余免费额度，得分 = 剩余比例
                score = remaining / config.free_quota_seconds
                return score, remaining
            return 0.0, 0.0
        except Exception as e:
            logger.warning("Failed to get free quota for %s/%s: %s", provider, variant, e)
            return 0.0, 0.0

    @classmethod
    async def _get_health_score(cls, provider: str) -> float:
        """获取健康得分"""
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

        from app.models.asr_user_quota import AsrUserQuota
        from app.services.asr_quota_service import (
            _active_window_clause,
            _effective_quotas,
            _execute,
            _extract_scalars,
        )

        now = datetime.now(timezone.utc)

        result = await _execute(
            session,
            select(AsrUserQuota)
            .where(AsrUserQuota.provider == provider)
            .where(AsrUserQuota.variant == variant)
            .where(_active_window_clause(now))
            .where(
                or_(AsrUserQuota.owner_user_id.is_(None), AsrUserQuota.owner_user_id == user_id)
            ),
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
    async def _get_cost_score(
        cls,
        session: Session | AsyncSession,
        provider: str,
        variant: str = "file",
    ) -> float:
        """获取成本得分

        返回 0-1，成本越低得分越高

        Args:
            session: 数据库会话
            provider: 提供商名称
            variant: 服务变体 (file=标准版, file_fast=极速版)
        """
        from app.services.asr_pricing_service import get_pricing_config

        # 从数据库获取定价配置
        config = await get_pricing_config(session, provider, variant)
        if not config:
            # 未配置的提供商，使用默认成本
            cost_per_hour = 2.0
        else:
            cost_per_hour = config.cost_per_hour

        # 归一化：成本越低得分越高
        score = max(0, 1.0 - (cost_per_hour / cls.MAX_COST_PER_HOUR))
        return score

    @classmethod
    async def _get_quality_score(
        cls,
        session: Session | AsyncSession,
        provider: str,
        variant: str = "file",
    ) -> float:
        """获取识别质量得分

        返回 0-1，从数据库 asr_pricing_configs.quality_score 读取

        Args:
            session: 数据库会话
            provider: 提供商名称
            variant: 服务变体
        """
        from app.services.asr_pricing_service import get_pricing_config

        config = await get_pricing_config(session, provider, variant)
        if not config:
            return 0.8  # 默认质量分

        return config.quality_score

    @classmethod
    async def _get_features_score(
        cls,
        session: Session | AsyncSession,
        provider: str,
        variant: str = "file",
        task_features: Optional[TaskFeatures] = None,
    ) -> float:
        """获取特殊功能匹配得分

        根据任务需求和提供商能力计算匹配度

        Args:
            session: 数据库会话
            provider: 提供商名称
            variant: 服务变体
            task_features: 任务特性需求

        Returns:
            0-1，匹配度越高得分越高
        """
        from app.services.asr_pricing_service import get_pricing_config

        config = await get_pricing_config(session, provider, variant)
        if not config:
            return 0.5  # 默认中等分

        # 如果没有特殊需求，所有提供商得分相同
        if not task_features:
            return 0.5

        # 计算匹配度
        required_features = 0
        matched_features = 0

        if task_features.diarization:
            required_features += 1
            if config.supports_diarization:
                matched_features += 1

        if task_features.word_level:
            required_features += 1
            if config.supports_word_level:
                matched_features += 1

        if required_features == 0:
            return 0.5  # 无特殊需求

        # 匹配度 = 匹配数 / 需求数
        match_ratio = matched_features / required_features

        # 完全匹配返回 1.0，不匹配返回 0.0
        return match_ratio

    @classmethod
    def get_all_scores(cls, scores: List[ProviderScore]) -> Dict[str, Dict[str, float]]:
        """获取所有提供商的评分详情（用于调试/监控）"""
        return {
            score.provider: {
                "free_quota": score.free_quota_score,
                "health": score.health_score,
                "cost": score.cost_score,
                "quota": score.quota_score,
                "quality": score.quality_score,
                "features": score.features_score,
                "total": score.total_score,
            }
            for score in scores
        }
