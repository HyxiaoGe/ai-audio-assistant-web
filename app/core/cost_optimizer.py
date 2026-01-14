"""成本优化器

基于估算成本与性能信息选择最优服务，并支持成本追踪与报告。
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.health_checker import HealthChecker
from app.core.registry import ServiceMetadata, ServiceRegistry

logger = logging.getLogger(__name__)


class CostStrategy(str, Enum):
    """成本优化策略"""

    LOWEST_COST = "lowest_cost"
    COST_PERFORMANCE_BALANCE = "cost_performance_balance"
    BUDGET_CONSTRAINED = "budget_constrained"
    COST_CEILING = "cost_ceiling"


@dataclass
class CostOptimizerConfig:
    """成本优化器配置"""

    strategy: CostStrategy = CostStrategy.LOWEST_COST
    cost_weight: float = 0.7
    performance_weight: float = 0.3
    daily_budget: float = 100.0
    monthly_budget: float = 3000.0
    cost_ceiling_ratio: float = 1.2
    enable_cost_tracking: bool = True
    enable_health_filter: bool = True
    enable_redis_persistence: bool = True  # P2-2: 启用 Redis 持久化


@dataclass
class ServiceCostInfo:
    """服务成本信息"""

    service_name: str
    estimated_cost: float
    performance_score: float
    combined_score: float
    metadata: Dict[str, Any]


class CostOptimizer:
    """成本优化器"""

    def __init__(self, config: CostOptimizerConfig):
        self.config = config
        if not config.enable_cost_tracking:
            self.tracker = None
        elif config.enable_redis_persistence:
            self.tracker = cost_tracker
        else:
            self.tracker = CostTracker(use_redis=False)

    def select_service(
        self,
        service_type: str,
        request_params: Dict[str, Any],
        candidate_services: Optional[List[str]] = None,
    ) -> Optional[str]:
        """选择成本最优的服务"""
        available_services = (
            candidate_services
            if candidate_services is not None
            else self._get_available_services(service_type)
        )
        if not available_services:
            return None

        cost_infos = [
            self._calculate_cost_info(service_type, service_name, request_params)
            for service_name in available_services
        ]

        selected = self._apply_strategy(cost_infos)
        if not selected:
            return None

        if self.tracker:
            self.tracker.record_usage(
                service_type,
                selected.service_name,
                request_params,
                selected.estimated_cost,
            )

        return selected.service_name

    def _get_available_services(self, service_type: str) -> List[str]:
        if self.config.enable_health_filter:
            return HealthChecker.get_healthy_services(service_type)
        return ServiceRegistry.list_services(service_type)

    def _calculate_cost_info(
        self,
        service_type: str,
        service_name: str,
        request_params: Dict[str, Any],
    ) -> ServiceCostInfo:
        service = ServiceRegistry.get(service_type, service_name)
        metadata = ServiceRegistry.get_metadata(service_type, service_name)
        estimated_cost = self._estimate_cost(service, request_params)
        performance_score = self._calculate_performance_score(metadata)
        combined_score = self._calculate_combined_score(estimated_cost, performance_score)

        return ServiceCostInfo(
            service_name=service_name,
            estimated_cost=estimated_cost,
            performance_score=performance_score,
            combined_score=combined_score,
            metadata=dict(metadata.__dict__),
        )

    def _estimate_cost(self, service: Any, request_params: Dict[str, Any]) -> float:
        if not hasattr(service, "estimate_cost"):
            return 0.0

        if "input_tokens" in request_params or "output_tokens" in request_params:
            return service.estimate_cost(
                int(request_params.get("input_tokens", 0)),
                int(request_params.get("output_tokens", 0)),
            )

        if "duration_seconds" in request_params:
            return service.estimate_cost(int(request_params.get("duration_seconds", 0)))

        if "duration_hours" in request_params:
            hours = float(request_params.get("duration_hours", 0.0))
            return service.estimate_cost(int(hours * 3600))

        if "storage_gb" in request_params or "requests" in request_params:
            return service.estimate_cost(
                float(request_params.get("storage_gb", 0.0)),
                int(request_params.get("requests", 0)),
            )

        return 0.0

    def estimate_request_cost(
        self,
        service_type: str,
        service_name: str,
        request_params: Dict[str, Any],
    ) -> float:
        service = ServiceRegistry.get(service_type, service_name)
        return self._estimate_cost(service, request_params)

    def _calculate_performance_score(self, metadata: ServiceMetadata) -> float:
        score = 0.0
        score += 100.0 / max(metadata.priority, 1)
        if metadata.rate_limit > 0:
            score += metadata.rate_limit / 1000.0
        return score

    def _calculate_combined_score(self, cost: float, performance: float) -> float:
        if self.config.strategy == CostStrategy.LOWEST_COST:
            return cost

        if self.config.strategy == CostStrategy.COST_PERFORMANCE_BALANCE:
            performance_term = 1 / max(performance, 0.1)
            return (
                cost * self.config.cost_weight + performance_term * self.config.performance_weight
            )

        return cost

    def _apply_strategy(self, cost_infos: List[ServiceCostInfo]) -> Optional[ServiceCostInfo]:
        if not cost_infos:
            return None

        if self.config.strategy == CostStrategy.LOWEST_COST:
            return min(cost_infos, key=lambda info: info.estimated_cost)

        if self.config.strategy == CostStrategy.COST_PERFORMANCE_BALANCE:
            return min(cost_infos, key=lambda info: info.combined_score)

        if self.config.strategy == CostStrategy.BUDGET_CONSTRAINED:
            within_budget = [info for info in cost_infos if self._check_budget(info.estimated_cost)]
            if not within_budget:
                return None
            return max(within_budget, key=lambda info: info.performance_score)

        if self.config.strategy == CostStrategy.COST_CEILING:
            min_cost = min(info.estimated_cost for info in cost_infos)
            ceiling = min_cost * self.config.cost_ceiling_ratio
            within_ceiling = [info for info in cost_infos if info.estimated_cost <= ceiling]
            if not within_ceiling:
                return None
            return max(within_ceiling, key=lambda info: info.performance_score)

        return min(cost_infos, key=lambda info: info.estimated_cost)

    def _check_budget(self, estimated_cost: float) -> bool:
        if not self.tracker:
            return True

        today_used = self.tracker.get_daily_cost(datetime.now().date())
        return (today_used + estimated_cost) <= self.config.daily_budget

    def get_cost_ranking(
        self,
        service_type: str,
        request_params: Dict[str, Any],
        candidate_services: Optional[List[str]] = None,
    ) -> List[ServiceCostInfo]:
        available_services = (
            candidate_services
            if candidate_services is not None
            else self._get_available_services(service_type)
        )
        cost_infos = [
            self._calculate_cost_info(service_type, name, request_params)
            for name in available_services
        ]
        return sorted(cost_infos, key=lambda info: info.estimated_cost)


@dataclass
class UsageRecord:
    """使用记录"""

    timestamp: datetime
    service_type: str
    service_name: str
    request_params: Dict[str, Any]
    estimated_cost: float
    actual_cost: Optional[float] = None


class CostTracker:
    """成本追踪器（支持 Redis 持久化）

    P2-2 优化：
    - 默认使用 Redis 持久化，数据不会因重启丢失
    - 支持回退到内存模式（use_redis=False）
    - Redis 数据结构：
      * sorted set: cost:records:{service_type}:{service_name}
      * hash: cost:daily:{date}
    """

    def __init__(self, use_redis: bool = True) -> None:
        self._use_redis = use_redis
        self._lock = threading.Lock()
        self._records: List[UsageRecord] = []
        self._daily_cache: Dict[date, float] = {}

        # 内存模式（回退方案）
        if not use_redis:
            self._redis_client = None
            logger.info("CostTracker initialized in memory mode")
            return

        # Redis 模式（默认）
        try:
            from worker.redis_client import get_sync_redis_client

            self._redis_client = get_sync_redis_client()
            logger.info("CostTracker initialized with Redis persistence")
        except Exception as exc:
            logger.warning(
                f"Failed to initialize Redis for CostTracker, falling back to memory mode: {exc}"
            )
            self._use_redis = False
            self._records = []
            self._daily_cache = {}
            self._redis_client = None

    def record_usage(
        self,
        service_type: str,
        service_name: str,
        request_params: Dict[str, Any],
        estimated_cost: float,
    ) -> None:
        """记录使用情况

        Redis 模式：存储到 sorted set（按时间戳排序）和 hash（每日汇总）
        内存模式：存储到列表
        """
        record = UsageRecord(
            timestamp=datetime.now(),
            service_type=service_type,
            service_name=service_name,
            request_params=request_params,
            estimated_cost=estimated_cost,
        )

        with self._lock:
            if self._use_redis and self._redis_client:
                self._record_to_redis(record)
            else:
                self._records.append(record)
                today = record.timestamp.date()
                self._daily_cache[today] = self._daily_cache.get(today, 0.0) + estimated_cost

    def _record_to_redis(self, record: UsageRecord) -> None:
        """将记录存储到 Redis（内部方法）"""
        try:
            # 生成唯一 key
            record_key = f"cost:records:{record.service_type}:{record.service_name}"
            daily_key = f"cost:daily:{record.timestamp.date().isoformat()}"

            # 序列化记录（排除 timestamp，使用 score 代替）
            record_dict = asdict(record)
            record_dict["timestamp"] = record.timestamp.isoformat()
            record_json = json.dumps(record_dict)

            # 使用 sorted set 存储（score 为时间戳，便于时间范围查询）
            timestamp_score = record.timestamp.timestamp()
            self._redis_client.zadd(record_key, {record_json: timestamp_score})

            # 更新每日汇总（使用 hash 的 hincrby）
            field = f"{record.service_type}:{record.service_name}"
            self._redis_client.hincrbyfloat(daily_key, field, record.estimated_cost)

            # 设置 TTL（保留 90 天）
            self._redis_client.expire(record_key, 90 * 24 * 3600)
            self._redis_client.expire(daily_key, 90 * 24 * 3600)

        except Exception as exc:
            logger.error(f"Failed to record usage to Redis: {exc}", exc_info=True)
            self._fallback_to_memory(record)

    def _fallback_to_memory(self, record: UsageRecord) -> None:
        if self._use_redis:
            logger.warning("Falling back to in-memory cost tracking")
        self._use_redis = False
        self._redis_client = None
        self._records.append(record)
        today = record.timestamp.date()
        self._daily_cache[today] = self._daily_cache.get(today, 0.0) + record.estimated_cost

    def get_records_in_range(
        self,
        start: datetime,
        end: datetime,
        service_type: Optional[str] = None,
        service_name: Optional[str] = None,
    ) -> List[UsageRecord]:
        """获取指定时间范围内的使用记录。"""
        with self._lock:
            if self._use_redis and self._redis_client:
                return self._get_records_in_range_from_redis(start, end, service_type, service_name)

            return [
                record
                for record in self._records
                if start <= record.timestamp <= end
                and (not service_type or record.service_type == service_type)
                and (not service_name or record.service_name == service_name)
            ]

    def _get_records_in_range_from_redis(
        self,
        start: datetime,
        end: datetime,
        service_type: Optional[str],
        service_name: Optional[str],
    ) -> List[UsageRecord]:
        try:
            start_ts = start.timestamp()
            end_ts = end.timestamp()

            if service_type and service_name:
                pattern = f"cost:records:{service_type}:{service_name}"
            elif service_type:
                pattern = f"cost:records:{service_type}:*"
            else:
                pattern = "cost:records:*"

            records: List[UsageRecord] = []
            for key in self._redis_client.scan_iter(match=pattern, count=100):
                raw_records = self._redis_client.zrangebyscore(key, start_ts, end_ts)
                for raw in raw_records:
                    record_dict = json.loads(raw)
                    record_dict["timestamp"] = datetime.fromisoformat(record_dict["timestamp"])
                    records.append(UsageRecord(**record_dict))

            return records
        except Exception as exc:
            logger.error(f"Failed to get records from Redis: {exc}", exc_info=True)
            self._use_redis = False
            return []

    def get_daily_summary(
        self,
        start_date: date,
        end_date: date,
    ) -> Dict[date, Dict[str, float]]:
        """获取每日成本汇总（按 service_type:service_name 聚合）。"""
        with self._lock:
            if self._use_redis and self._redis_client:
                return self._get_daily_summary_from_redis(start_date, end_date)

            summary: Dict[date, Dict[str, float]] = {}
            for record in self._records:
                record_date = record.timestamp.date()
                if record_date < start_date or record_date > end_date:
                    continue
                summary.setdefault(record_date, {})
                key = f"{record.service_type}:{record.service_name}"
                summary[record_date][key] = (
                    summary[record_date].get(key, 0.0) + record.estimated_cost
                )
            return summary

    def _get_daily_summary_from_redis(
        self,
        start_date: date,
        end_date: date,
    ) -> Dict[date, Dict[str, float]]:
        result: Dict[date, Dict[str, float]] = {}
        current = start_date

        while current <= end_date:
            daily_key = f"cost:daily:{current.isoformat()}"
            try:
                daily_data = self._redis_client.hgetall(daily_key)
            except Exception as exc:
                logger.error(f"Failed to read daily cost from Redis: {exc}", exc_info=True)
                self._use_redis = False
                break

            if daily_data:
                result[current] = {
                    key.decode(): float(value.decode()) for key, value in daily_data.items()
                }

            current += timedelta(days=1)

        return result

    def get_daily_cost(self, target_date: date) -> float:
        """获取指定日期的总成本

        Redis 模式：从 cost:daily:{date} hash 读取
        内存模式：从缓存或遍历记录计算
        """
        with self._lock:
            if self._use_redis and self._redis_client:
                return self._get_daily_cost_from_redis(target_date)

            # 内存模式
            if target_date in self._daily_cache:
                return self._daily_cache[target_date]

            total = sum(
                record.estimated_cost
                for record in self._records
                if record.timestamp.date() == target_date
            )
            self._daily_cache[target_date] = total
            return total

    def _get_daily_cost_from_redis(self, target_date: date) -> float:
        """从 Redis 获取每日成本（内部方法）"""
        try:
            daily_key = f"cost:daily:{target_date.isoformat()}"
            values = self._redis_client.hvals(daily_key)
            return sum(float(v) for v in values) if values else 0.0
        except Exception as exc:
            logger.error(f"Failed to get daily cost from Redis: {exc}", exc_info=True)
            self._use_redis = False
            return 0.0

    def get_monthly_cost(self, year: int, month: int) -> float:
        """获取指定月份的总成本

        Redis 模式：遍历该月每一天的 cost:daily:{date} hash
        内存模式：遍历记录计算
        """
        with self._lock:
            if self._use_redis and self._redis_client:
                return self._get_monthly_cost_from_redis(year, month)

            # 内存模式
            return sum(
                record.estimated_cost
                for record in self._records
                if record.timestamp.year == year and record.timestamp.month == month
            )

    def _get_monthly_cost_from_redis(self, year: int, month: int) -> float:
        """从 Redis 获取月度成本（内部方法）"""
        try:
            # 遍历该月每一天
            from calendar import monthrange

            _, days_in_month = monthrange(year, month)
            total = 0.0

            for day in range(1, days_in_month + 1):
                target_date = date(year, month, day)
                total += self._get_daily_cost_from_redis(target_date)

            return total
        except Exception as exc:
            logger.error(f"Failed to get monthly cost from Redis: {exc}", exc_info=True)
            return 0.0

    def get_service_breakdown(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Dict[str, float]]:
        """获取服务成本明细

        Redis 模式：遍历日期范围内的每日汇总
        内存模式：遍历记录计算
        """
        with self._lock:
            if self._use_redis and self._redis_client:
                return self._get_service_breakdown_from_redis(start_date, end_date)

            # 内存模式
            breakdown: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

            for record in self._records:
                record_date = record.timestamp.date()
                if start_date and record_date < start_date:
                    continue
                if end_date and record_date > end_date:
                    continue
                breakdown[record.service_type][record.service_name] += record.estimated_cost

            return {svc_type: dict(values) for svc_type, values in breakdown.items()}

    def _get_service_breakdown_from_redis(
        self,
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> Dict[str, Dict[str, float]]:
        """从 Redis 获取服务成本明细（内部方法）"""
        try:
            breakdown: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

            # 确定日期范围
            if not start_date:
                start_date = date.today() - timedelta(days=30)
            if not end_date:
                end_date = date.today()

            # 遍历日期范围
            current = start_date
            while current <= end_date:
                daily_key = f"cost:daily:{current.isoformat()}"
                daily_data = self._redis_client.hgetall(daily_key)

                # 解析 hash 数据：{service_type:service_name: cost}
                for field, cost_str in daily_data.items():
                    if ":" in field:
                        service_type, service_name = field.split(":", 1)
                        breakdown[service_type][service_name] += float(cost_str)

                current += timedelta(days=1)

            return {svc_type: dict(values) for svc_type, values in breakdown.items()}

        except Exception as exc:
            logger.error(f"Failed to get service breakdown from Redis: {exc}", exc_info=True)
            return {}

    def generate_report(self, start_date: date, end_date: date) -> "CostReport":
        """生成成本报告

        Redis/内存模式：使用已实现的 get_daily_cost 和 get_service_breakdown
        """
        # 计算总成本
        total_cost = 0.0
        daily_costs: Dict[date, float] = {}
        current = start_date

        while current <= end_date:
            daily_cost = self.get_daily_cost(current)
            daily_costs[current] = daily_cost
            total_cost += daily_cost
            current += timedelta(days=1)

        # 获取服务明细
        breakdown = self.get_service_breakdown(start_date, end_date)

        return CostReport(
            start_date=start_date,
            end_date=end_date,
            total_cost=total_cost,
            service_breakdown=breakdown,
            daily_costs=daily_costs,
        )


cost_tracker = CostTracker(use_redis=True)


@dataclass
class CostReport:
    """成本报告"""

    start_date: date
    end_date: date
    total_cost: float
    service_breakdown: Dict[str, Dict[str, float]]
    daily_costs: Dict[date, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "total_cost": self.total_cost,
            "service_breakdown": self.service_breakdown,
            "daily_costs": {day.isoformat(): cost for day, cost in self.daily_costs.items()},
        }
