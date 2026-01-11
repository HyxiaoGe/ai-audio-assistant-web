# Stats API 开发规范（修订版）

## 功能概述

实现统计面板 API，提供成本统计和任务统计两大模块，帮助用户了解系统使用情况和成本消耗。

**优先级**：P0（高优先级）
**预估工作量**：2-3 小时

## 目标用户与范围限制

### 当前版本（V1）支持的功能

- ✅ **成本统计**：全局成本数据（所有用户的汇总，CostTracker 无 user_id 维度）
- ✅ **任务统计**：按用户统计（每个用户只能看自己的任务数据）
- ✅ **成本趋势**：仅支持日粒度（day）
- ✅ **任务趋势**：支持多种粒度（hour/day/week/month）

### 当前版本（V1）的限制

- ❌ **按用户统计成本**：CostTracker 的 UsageRecord 不包含 user_id/task_id，只能看全局成本
- ❌ **最贵任务排名**：同上，需要 V2 新增成本持久化表
- ❌ **成本趋势多粒度**：CostTracker 的 Redis 结构针对日粒度优化，其他粒度性能差
- ⚠️ **内存模式数据丢失**：CostTracker 回退到内存模式时（Redis 不可用），重启后成本统计清空，前端可能看到空数据
- ⚠️ **成本时间范围限制**：time_range=all 固定查询 90 天，依赖 Redis TTL 配置（如调整 TTL 需同步修改代码）

### ⚠️ V1 数据持久化风险（CRITICAL）

**成本数据只存储在 Redis 中，存在严重的数据丢失风险：**

1. **Redis 重启风险**：
   - 如果 Redis 未配置持久化（RDB/AOF），重启后所有成本数据丢失
   - 当前实现无数据库备份，丢失后无法恢复

2. **运维风险**：
   - Redis 迁移、故障、配置重置可能导致历史数据永久丢失
   - 成本数据属于财务数据，丢失会影响业务决策和成本核算

3. **数据保留限制**：
   - 90 天 TTL 限制，超期数据自动过期
   - 无法做长期（季度/年度）成本分析和趋势预测

**生产环境部署要求（必须满足）：**

```bash
# redis.conf 必须配置
save 900 1              # RDB: 15分钟内有1次写入就保存快照
save 300 10             # RDB: 5分钟内有10次写入就保存快照
save 60 10000           # RDB: 1分钟内有10000次写入就保存快照

appendonly yes          # AOF: 开启追加日志持久化
appendfsync everysec    # AOF: 每秒同步到磁盘（性能与安全的平衡）
```

**强烈建议：**
- V2 版本应立即实现数据库双写（Redis + PostgreSQL）
- 将 `service_usage` 表从"后续优化"提升为"必须实现"
- 在 V1 部署前向用户明确告知数据持久化风险

**V2 详细规划见文档后续章节"V2 规划（强烈建议尽快实现）"。**

---

## API 设计

### 基础路径

```
/api/v1/stats
```

### 端点列表

#### 1. 成本统计概览

```http
GET /api/v1/stats/costs/overview
```

**Query Parameters:**
- `start_date` (可选): 开始日期，ISO 8601 格式，如 `2024-01-01`
- `end_date` (可选): 结束日期，ISO 8601 格式，如 `2024-01-31`
- `time_range` (可选): 预设时间范围，枚举值：`today`, `week`, `month`, `all`
  - 如果同时提供 `start_date/end_date` 和 `time_range`，优先使用 `time_range`

**权限**：
- 需要登录（`Depends(get_current_user)`）
- V1 版本返回全局数据（所有用户的成本汇总，不区分用户）

**Response Schema:**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "time_range": {
      "start": "2024-01-01T00:00:00Z",
      "end": "2024-01-31T23:59:59Z"
    },
    "total_cost": 125.50,
    "currency": "CNY",
    "breakdown_by_service_type": [
      {
        "service_type": "asr",
        "cost": 45.20,
        "percentage": 36.0,
        "call_count": 150
      },
      {
        "service_type": "llm",
        "cost": 75.30,
        "percentage": 60.0,
        "call_count": 320
      },
      {
        "service_type": "storage",
        "cost": 5.00,
        "percentage": 4.0,
        "call_count": 500
      }
    ],
    "breakdown_by_provider": [
      {
        "service_type": "llm",
        "provider": "deepseek",
        "cost": 30.50,
        "percentage": 40.5,
        "call_count": 120
      },
      {
        "service_type": "llm",
        "provider": "qwen",
        "cost": 25.80,
        "percentage": 34.3,
        "call_count": 100
      },
      {
        "service_type": "llm",
        "provider": "doubao",
        "cost": 19.00,
        "percentage": 25.2,
        "call_count": 100
      },
      {
        "service_type": "asr",
        "provider": "tencent",
        "cost": 28.20,
        "percentage": 62.4,
        "call_count": 90
      },
      {
        "service_type": "asr",
        "provider": "aliyun",
        "cost": 17.00,
        "percentage": 37.6,
        "call_count": 60
      }
    ]
  },
  "traceId": "xxx"
}
```

**数据获取方式:**

从 `app/core/cost_optimizer.py` 的 `CostTracker` 读取：

```python
from app.core.cost_optimizer import cost_tracker

# CostTracker 使用 Redis 存储成本记录
# 数据结构：
# - sorted set: cost:records:{service_type}:{service_name}
#   - score: timestamp (可用于时间范围查询)
#   - value: JSON(UsageRecord)
# - hash: cost:daily:{date}
#   - field: {service_type}:{service_name}
#   - value: cost (float)

# 获取时间范围内的记录
records = cost_tracker.get_records_in_range(start, end)  # 需实现此方法
# 或使用 Redis 直接查询
# ZRANGEBYSCORE cost:records:{type}:{name} {start_ts} {end_ts}
```

**实现建议**：

V1 版本需要在 CostTracker 中新增方法：
```python
def get_records_in_range(
    self,
    start: datetime,
    end: datetime,
    service_type: Optional[str] = None
) -> List[UsageRecord]:
    """获取时间范围内的使用记录"""
    # 遍历所有 cost:records:* keys
    # 使用 ZRANGEBYSCORE 按时间过滤
    pass

def get_daily_summary(
    self,
    start_date: date,
    end_date: date
) -> Dict[date, Dict[str, float]]:
    """获取每日汇总（更高效）"""
    # 遍历 cost:daily:{date} keys
    # 聚合指定日期范围的数据
    pass
```

---

#### 2. 成本趋势

```http
GET /api/v1/stats/costs/trend
```

**Query Parameters:**
- `start_date` (可选): 开始日期
- `end_date` (可选): 结束日期
- `time_range` (可选): `today`, `week`, `month`, `all`
- `granularity` (可选): 粒度，**V1 仅支持 `day`**，默认 `day`

**Response Schema:**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "granularity": "day",
    "data_points": [
      {
        "timestamp": "2024-01-01T00:00:00Z",
        "total_cost": 8.50,
        "breakdown": {
          "asr": 3.20,
          "llm": 5.00,
          "storage": 0.30
        }
      },
      {
        "timestamp": "2024-01-02T00:00:00Z",
        "total_cost": 12.30,
        "breakdown": {
          "asr": 4.50,
          "llm": 7.50,
          "storage": 0.30
        }
      }
    ]
  },
  "traceId": "xxx"
}
```

**数据获取:**

使用 CostTracker 的 `get_daily_summary()` 方法，直接从 Redis hash 读取日汇总数据：

```python
daily_data = cost_tracker.get_daily_summary(start_date, end_date)
# 返回: {date: {service_type:provider: cost}}
```

**V1 限制**：
- 仅支持 `granularity=day`
- 如果前端传入 `hour/week/month`，返回 400 错误（ErrorCode.PARAMETER_ERROR）

---

#### 3. 任务统计概览

```http
GET /api/v1/stats/tasks/overview
```

**Query Parameters:**
- `start_date` (可选): 开始日期
- `end_date` (可选): 结束日期
- `time_range` (可选): `today`, `week`, `month`, `all`

**Response Schema:**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "time_range": {
      "start": "2024-01-01T00:00:00Z",
      "end": "2024-01-31T23:59:59Z"
    },
    "total_tasks": 250,
    "status_distribution": {
      "pending": 10,
      "processing": 5,
      "completed": 200,
      "failed": 30
    },
    "success_rate": 80.0,
    "failure_rate": 12.0,
    "avg_processing_time_seconds": 125.5,
    "median_processing_time_seconds": 98.0,
    "processing_time_by_stage": {
      "resolve_youtube": 5.2,
      "download": 25.3,
      "transcode": 18.5,
      "upload_storage": 12.0,
      "transcribe": 85.3,
      "summarize": 25.0
    },
    "total_audio_duration_seconds": 125000,
    "total_audio_duration_formatted": "34h 43m 20s"
  },
  "traceId": "xxx"
}
```

**字段说明:**

- `status_distribution`: 基于 `Task.status` 字段统计
  - **原样返回数据库中的实际状态分布**（什么状态存在就返回什么）
  - 示例中的 pending/processing/completed/failed 仅为常见值
  - 实际可能包含其他状态（如 queued, resolving 等，取决于系统实现）
  - 注意：skipped 是 TaskStage 的状态，不是 Task 的
  - 来源：`app/models/task.py` 的 status 字段

- `processing_time_by_stage`: 基于 `TaskStage` 表的 `started_at` 和 `completed_at` 计算
  - stage_type 枚举值：resolve_youtube, download, transcode, upload_storage, transcribe, summarize
  - 来源：`app/core/task_stages.py` 的 StageType 枚举

- `total_audio_duration_seconds`: 来自 `Task.duration_seconds` 字段（不是 audio_duration）

**SQL 查询逻辑:**

```python
from sqlalchemy import func, select, case
from app.models.task import Task
from app.models.task_stage import TaskStage

# 1. 任务总数和状态分布
stmt = select(
    func.count(Task.id).label('total'),
    Task.status,
    func.count(Task.id).label('count')
).where(
    Task.user_id == user.id,
    Task.created_at >= start_date,
    Task.created_at <= end_date,
    Task.deleted_at.is_(None)
).group_by(Task.status)

# 2. 平均处理时长（从 task_stages 聚合）
# 每个任务的处理时长 = 最晚 completed_at - 最早 started_at
stmt = select(
    Task.id,
    func.min(TaskStage.started_at).label('first_start'),
    func.max(TaskStage.completed_at).label('last_complete')
).join(
    TaskStage, Task.id == TaskStage.task_id
).where(
    Task.user_id == user.id,
    Task.created_at >= start_date,
    Task.created_at <= end_date,
    Task.deleted_at.is_(None),
    TaskStage.is_active == True
).group_by(Task.id)

# 然后计算平均值
processing_times = [
    (last - first).total_seconds()
    for first, last in results
    if first and last
]
avg_time = sum(processing_times) / len(processing_times) if processing_times else 0

# 3. 各阶段平均耗时
stmt = select(
    TaskStage.stage_type,
    func.avg(
        func.extract('epoch', TaskStage.completed_at - TaskStage.started_at)
    ).label('avg_seconds')
).where(
    TaskStage.task_id.in_(
        select(Task.id).where(
            Task.user_id == user.id,
            Task.created_at >= start_date,
            Task.created_at <= end_date,
            Task.deleted_at.is_(None)
        )
    ),
    TaskStage.status == 'completed',
    TaskStage.is_active == True
).group_by(TaskStage.stage_type)

# 4. 音频总时长
stmt = select(
    func.sum(Task.duration_seconds).label('total_duration')
).where(
    Task.user_id == user.id,
    Task.created_at >= start_date,
    Task.created_at <= end_date,
    Task.deleted_at.is_(None)
)
```

---

#### 4. 任务趋势

```http
GET /api/v1/stats/tasks/trend
```

**Query Parameters:**
- `start_date` (可选)
- `end_date` (可选)
- `time_range` (可选)
- `granularity` (可选): `hour`, `day`, `week`, `month`，默认 `day`

**Response Schema:**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "granularity": "day",
    "data_points": [
      {
        "timestamp": "2024-01-01T00:00:00Z",
        "total_tasks": 15,
        "pending": 2,
        "processing": 1,
        "completed": 12,
        "failed": 0,
        "success_rate": 100.0
      },
      {
        "timestamp": "2024-01-02T00:00:00Z",
        "total_tasks": 20,
        "pending": 3,
        "processing": 2,
        "completed": 14,
        "failed": 1,
        "success_rate": 93.3
      }
    ]
  },
  "traceId": "xxx"
}
```

**SQL 查询逻辑:**

```python
# PostgreSQL 使用 date_trunc 按粒度聚合
stmt = select(
    func.date_trunc(granularity, Task.created_at).label('time_bucket'),
    func.count(Task.id).label('total'),
    func.sum(case((Task.status == 'pending', 1), else_=0)).label('pending'),
    func.sum(case((Task.status == 'processing', 1), else_=0)).label('processing'),
    func.sum(case((Task.status == 'completed', 1), else_=0)).label('completed'),
    func.sum(case((Task.status == 'failed', 1), else_=0)).label('failed'),
).where(
    Task.user_id == user.id,
    Task.created_at >= start_date,
    Task.created_at <= end_date,
    Task.deleted_at.is_(None)
).group_by('time_bucket').order_by('time_bucket')
```

---

## 实现指南

### 1. 文件结构

```
app/api/v1/stats.py              # 新增：统计接口路由
app/services/stats_service.py   # 新增：统计业务逻辑
app/schemas/stats.py             # 新增：统计响应 Schema
app/core/cost_optimizer.py       # 修改：新增查询方法
```

### 2. 路由注册

在 `app/api/v1/router.py` 中添加：

```python
from app.api.v1 import stats

api_router.include_router(stats.router, prefix="/stats", tags=["stats"])
```

### 3. CostTracker 扩展

在 `app/core/cost_optimizer.py` 的 `CostTracker` 类中新增方法：

```python
def get_records_in_range(
    self,
    start: datetime,
    end: datetime,
    service_type: Optional[str] = None,
    service_name: Optional[str] = None
) -> List[UsageRecord]:
    """获取时间范围内的使用记录

    Args:
        start: 开始时间
        end: 结束时间
        service_type: 服务类型过滤（可选）
        service_name: 服务名称过滤（可选）

    Returns:
        UsageRecord 列表
    """
    if not self._use_redis or not self._redis_client:
        # 内存模式
        return [
            r for r in self._records
            if start <= r.timestamp <= end
            and (not service_type or r.service_type == service_type)
            and (not service_name or r.service_name == service_name)
        ]

    # Redis 模式
    records = []
    start_ts = start.timestamp()
    end_ts = end.timestamp()

    # 使用 scan_iter 避免 keys() 阻塞（重要：生产环境数据量大时 keys() 会卡住 Redis）
    if service_type and service_name:
        pattern = f"cost:records:{service_type}:{service_name}"
    elif service_type:
        pattern = f"cost:records:{service_type}:*"
    else:
        pattern = "cost:records:*"

    for key in self._redis_client.scan_iter(match=pattern, count=100):
        # 使用 ZRANGEBYSCORE 按时间范围查询
        raw_records = self._redis_client.zrangebyscore(key, start_ts, end_ts)
        for raw in raw_records:
            record_dict = json.loads(raw)
            record_dict['timestamp'] = datetime.fromisoformat(record_dict['timestamp'])
            records.append(UsageRecord(**record_dict))

    return records

def get_daily_summary(
    self,
    start_date: date,
    end_date: date
) -> Dict[date, Dict[str, float]]:
    """获取每日成本汇总

    Args:
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        {date: {service_type:service_name: cost}}
    """
    if not self._use_redis or not self._redis_client:
        # 内存模式：需要从 _records 重新聚合，确保返回结构与 Redis 一致
        result: Dict[date, Dict[str, float]] = {}
        for record in self._records:
            d = record.timestamp.date()
            if start_date <= d <= end_date:
                if d not in result:
                    result[d] = {}
                key = f"{record.service_type}:{record.service_name}"
                result[d][key] = result[d].get(key, 0) + record.estimated_cost
        return result

    # Redis 模式
    result = {}
    current = start_date

    while current <= end_date:
        daily_key = f"cost:daily:{current.isoformat()}"
        data = self._redis_client.hgetall(daily_key)

        if data:
            # data 格式: {b'asr:tencent': b'12.5', ...}
            result[current] = {
                k.decode(): float(v.decode())
                for k, v in data.items()
            }

        current += timedelta(days=1)

    return result
```

### 4. Service 层实现

`app/services/stats_service.py`：

```python
from datetime import datetime, timedelta, date
from typing import Optional, Literal, Dict, List
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.task import Task
from app.models.task_stage import TaskStage
from app.core.cost_optimizer import cost_tracker
from app.config import settings

class StatsService:
    def __init__(self, db: AsyncSession, user: User):
        self.db = db
        self.user = user

    async def _parse_time_range(
        self,
        time_range: Optional[Literal["today", "week", "month", "all"]],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
        for_cost: bool = False  # 新增参数：是否用于成本统计
    ) -> tuple[datetime, datetime]:
        """解析时间范围

        Args:
            for_cost: True 表示用于成本统计（全局），False 表示用于任务统计（按用户）
        """
        now = datetime.utcnow()

        if time_range == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif time_range == "week":
            start = now - timedelta(days=7)
            end = now
        elif time_range == "month":
            start = now - timedelta(days=30)
            end = now
        elif time_range == "all":
            if for_cost:
                # 成本统计：Redis TTL 90 天，最多查 90 天前
                start = now - timedelta(days=90)
            else:
                # 任务统计：查询用户最早任务时间
                stmt = select(func.min(Task.created_at)).where(
                    Task.user_id == self.user.id,
                    Task.deleted_at.is_(None)
                )
                result = await self.db.execute(stmt)
                earliest = result.scalar_one_or_none()
                start = earliest or (now - timedelta(days=30))
            end = now
        else:
            start = start_date or (now - timedelta(days=30))
            end = end_date or now

        return start, end

    async def get_cost_overview(
        self,
        time_range: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> dict:
        """获取成本统计概览（V1: 全局数据，不区分用户）"""
        start, end = await self._parse_time_range(time_range, start_date, end_date, for_cost=True)

        # 从 CostTracker 获取记录
        # 注意：cost_tracker 是同步方法（Redis I/O），可能阻塞事件循环
        # 生产环境建议用 asyncio.to_thread() 或线程池包装
        records = cost_tracker.get_records_in_range(start, end)

        # 聚合统计
        total_cost = sum(r.estimated_cost for r in records)

        # 按服务类型分组
        by_type: Dict[str, Dict] = {}
        for r in records:
            if r.service_type not in by_type:
                by_type[r.service_type] = {'cost': 0, 'count': 0}
            by_type[r.service_type]['cost'] += r.estimated_cost
            by_type[r.service_type]['count'] += 1

        # 按提供商分组
        by_provider: Dict[tuple, Dict] = {}
        for r in records:
            key = (r.service_type, r.service_name)
            if key not in by_provider:
                by_provider[key] = {'cost': 0, 'count': 0}
            by_provider[key]['cost'] += r.estimated_cost
            by_provider[key]['count'] += 1

        # 构造响应
        return {
            "time_range": {"start": start, "end": end},
            "total_cost": round(total_cost, 2),
            "currency": getattr(settings, 'STATS_CURRENCY', 'CNY'),
            "breakdown_by_service_type": [
                {
                    "service_type": st,
                    "cost": round(data['cost'], 2),
                    "percentage": round(data['cost'] / total_cost * 100, 1) if total_cost > 0 else 0,
                    "call_count": data['count']
                }
                for st, data in by_type.items()
            ],
            "breakdown_by_provider": [
                {
                    "service_type": key[0],
                    "provider": key[1],
                    "cost": round(data['cost'], 2),
                    "percentage": round(data['cost'] / total_cost * 100, 1) if total_cost > 0 else 0,
                    "call_count": data['count']
                }
                for key, data in by_provider.items()
            ]
        }

    async def get_cost_trend(
        self,
        granularity: str = "day",
        time_range: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> dict:
        """获取成本趋势（V1: 仅支持 day 粒度）"""
        if granularity != "day":
            from app.core.exceptions import BusinessError
            from app.i18n.codes import ErrorCode
            raise BusinessError(
                ErrorCode.PARAMETER_ERROR,
                reason="V1 version only supports granularity='day'"
            )

        start, end = await self._parse_time_range(time_range, start_date, end_date, for_cost=True)

        # 获取每日汇总
        # 注意：cost_tracker 是同步方法（Redis I/O），可能阻塞事件循环
        # 生产环境建议用 asyncio.to_thread() 或线程池包装
        daily_data = cost_tracker.get_daily_summary(
            start.date(),
            end.date()
        )

        # 构造数据点
        data_points = []
        current = start.date()
        while current <= end.date():
            day_data = daily_data.get(current, {})

            # 按服务类型聚合
            breakdown = {}
            for key, cost in day_data.items():
                service_type = key.split(':')[0]
                breakdown[service_type] = breakdown.get(service_type, 0) + cost

            data_points.append({
                "timestamp": datetime.combine(current, datetime.min.time()),
                "total_cost": round(sum(day_data.values()), 2),
                "breakdown": {k: round(v, 2) for k, v in breakdown.items()}
            })

            current += timedelta(days=1)

        return {
            "granularity": "day",
            "data_points": data_points
        }

    async def get_task_overview(
        self,
        time_range: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> dict:
        """获取任务统计概览"""
        start, end = await self._parse_time_range(time_range, start_date, end_date)

        # 查询任务状态分布
        stmt = select(
            Task.status,
            func.count(Task.id).label('count')
        ).where(
            Task.user_id == self.user.id,
            Task.created_at >= start,
            Task.created_at <= end,
            Task.deleted_at.is_(None)
        ).group_by(Task.status)

        result = await self.db.execute(stmt)
        status_rows = result.all()

        status_dist = {row.status: row.count for row in status_rows}
        total_tasks = sum(status_dist.values())

        # 计算成功率
        completed = status_dist.get('completed', 0)
        failed = status_dist.get('failed', 0)
        success_rate = (completed / total_tasks * 100) if total_tasks > 0 else 0
        failure_rate = (failed / total_tasks * 100) if total_tasks > 0 else 0

        # 查询处理时长（从 task_stages 聚合）
        stmt = select(
            Task.id,
            func.min(TaskStage.started_at).label('first_start'),
            func.max(TaskStage.completed_at).label('last_complete')
        ).join(
            TaskStage, Task.id == TaskStage.task_id
        ).where(
            Task.user_id == self.user.id,
            Task.created_at >= start,
            Task.created_at <= end,
            Task.deleted_at.is_(None),
            TaskStage.is_active == True
        ).group_by(Task.id)

        result = await self.db.execute(stmt)
        time_rows = result.all()

        processing_times = [
            (row.last_complete - row.first_start).total_seconds()
            for row in time_rows
            if row.first_start and row.last_complete
        ]

        avg_time = sum(processing_times) / len(processing_times) if processing_times else 0
        median_time = sorted(processing_times)[len(processing_times) // 2] if processing_times else 0

        # 查询各阶段平均耗时
        stmt = select(
            TaskStage.stage_type,
            func.avg(
                func.extract('epoch', TaskStage.completed_at - TaskStage.started_at)
            ).label('avg_seconds')
        ).where(
            TaskStage.task_id.in_(
                select(Task.id).where(
                    Task.user_id == self.user.id,
                    Task.created_at >= start,
                    Task.created_at <= end,
                    Task.deleted_at.is_(None)
                )
            ),
            TaskStage.status == 'completed',
            TaskStage.is_active == True
        ).group_by(TaskStage.stage_type)

        result = await self.db.execute(stmt)
        stage_rows = result.all()

        stage_times = {row.stage_type: round(row.avg_seconds, 1) for row in stage_rows}

        # 查询音频总时长
        stmt = select(
            func.sum(Task.duration_seconds).label('total_duration')
        ).where(
            Task.user_id == self.user.id,
            Task.created_at >= start,
            Task.created_at <= end,
            Task.deleted_at.is_(None)
        )

        result = await self.db.execute(stmt)
        total_duration = result.scalar_one_or_none() or 0

        # 格式化时长
        hours = total_duration // 3600
        minutes = (total_duration % 3600) // 60
        seconds = total_duration % 60
        duration_formatted = f"{hours}h {minutes}m {seconds}s"

        return {
            "time_range": {"start": start, "end": end},
            "total_tasks": total_tasks,
            "status_distribution": status_dist,
            "success_rate": round(success_rate, 1),
            "failure_rate": round(failure_rate, 1),
            "avg_processing_time_seconds": round(avg_time, 1),
            "median_processing_time_seconds": round(median_time, 1),
            "processing_time_by_stage": stage_times,
            "total_audio_duration_seconds": total_duration,
            "total_audio_duration_formatted": duration_formatted
        }

    async def get_task_trend(
        self,
        granularity: str = "day",
        time_range: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> dict:
        """获取任务趋势"""
        start, end = await self._parse_time_range(time_range, start_date, end_date)

        stmt = select(
            func.date_trunc(granularity, Task.created_at).label('time_bucket'),
            func.count(Task.id).label('total'),
            func.sum(case((Task.status == 'pending', 1), else_=0)).label('pending'),
            func.sum(case((Task.status == 'processing', 1), else_=0)).label('processing'),
            func.sum(case((Task.status == 'completed', 1), else_=0)).label('completed'),
            func.sum(case((Task.status == 'failed', 1), else_=0)).label('failed'),
        ).where(
            Task.user_id == self.user.id,
            Task.created_at >= start,
            Task.created_at <= end,
            Task.deleted_at.is_(None)
        ).group_by('time_bucket').order_by('time_bucket')

        result = await self.db.execute(stmt)
        rows = result.all()

        data_points = []
        for row in rows:
            total = row.total
            completed = row.completed
            success_rate = (completed / total * 100) if total > 0 else 0

            data_points.append({
                "timestamp": row.time_bucket,
                "total_tasks": total,
                "pending": row.pending,
                "processing": row.processing,
                "completed": completed,
                "failed": row.failed,
                "success_rate": round(success_rate, 1)
            })

        return {
            "granularity": granularity,
            "data_points": data_points
        }
```

### 5. Schema 定义

`app/schemas/stats.py`：

```python
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict
from datetime import datetime

class TimeRange(BaseModel):
    start: datetime
    end: datetime

class ServiceCostBreakdown(BaseModel):
    service_type: Literal["asr", "llm", "storage"]
    cost: float
    percentage: float
    call_count: int

class ProviderCostBreakdown(BaseModel):
    service_type: Literal["asr", "llm", "storage"]
    provider: str
    cost: float
    percentage: float
    call_count: int

class CostOverviewResponse(BaseModel):
    time_range: TimeRange
    total_cost: float
    currency: str
    breakdown_by_service_type: List[ServiceCostBreakdown]
    breakdown_by_provider: List[ProviderCostBreakdown]

class CostDataPoint(BaseModel):
    timestamp: datetime
    total_cost: float
    breakdown: Dict[str, float]

class CostTrendResponse(BaseModel):
    granularity: Literal["day"]  # V1 仅支持 day
    data_points: List[CostDataPoint]

class TaskOverviewResponse(BaseModel):
    time_range: TimeRange
    total_tasks: int
    status_distribution: Dict[str, int]
    success_rate: float
    failure_rate: float
    avg_processing_time_seconds: float
    median_processing_time_seconds: float
    processing_time_by_stage: Dict[str, float]
    total_audio_duration_seconds: float
    total_audio_duration_formatted: str

class TaskDataPoint(BaseModel):
    timestamp: datetime
    total_tasks: int
    pending: int
    processing: int
    completed: int
    failed: int
    success_rate: float

class TaskTrendResponse(BaseModel):
    granularity: Literal["hour", "day", "week", "month"]
    data_points: List[TaskDataPoint]
```

### 6. API 路由实现

`app/api/v1/stats.py`：

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import Optional, Literal

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.services.stats_service import StatsService
from app.core.response import success

router = APIRouter()

@router.get("/costs/overview")
async def get_cost_overview(
    time_range: Optional[Literal["today", "week", "month", "all"]] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取成本统计概览（V1: 全局数据）"""
    service = StatsService(db, user)
    data = await service.get_cost_overview(time_range, start_date, end_date)
    return success(data=data)

@router.get("/costs/trend")
async def get_cost_trend(
    granularity: Literal["day"] = Query("day"),  # V1 仅支持 day
    time_range: Optional[Literal["today", "week", "month", "all"]] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取成本趋势（V1: 仅支持日粒度）"""
    service = StatsService(db, user)
    data = await service.get_cost_trend(granularity, time_range, start_date, end_date)
    return success(data=data)

@router.get("/tasks/overview")
async def get_task_overview(
    time_range: Optional[Literal["today", "week", "month", "all"]] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取任务统计概览"""
    service = StatsService(db, user)
    data = await service.get_task_overview(time_range, start_date, end_date)
    return success(data=data)

@router.get("/tasks/trend")
async def get_task_trend(
    granularity: Literal["hour", "day", "week", "month"] = Query("day"),
    time_range: Optional[Literal["today", "week", "month", "all"]] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取任务趋势"""
    service = StatsService(db, user)
    data = await service.get_task_trend(granularity, time_range, start_date, end_date)
    return success(data=data)
```

---

## 实现注意事项

### 1. V1 版本的限制说明

**成本统计的限制**：
- ❌ 无法按用户/任务维度统计：CostTracker 的 UsageRecord 不包含 user_id/task_id
- ❌ 无法提供"最贵任务排名"功能
- ✅ 可以提供全局成本统计和趋势
- ✅ 建议在响应中添加提示："V1 版本显示全局数据，V2 将支持按用户统计"

**成本趋势的限制**：
- ❌ V1 仅支持 day 粒度（Redis 结构针对日粒度优化）
- ❌ hour/week/month 粒度需要扫描 sorted set 并聚合，性能差
- ✅ 如果前端传入非 day 粒度，返回 400 错误（ErrorCode.PARAMETER_ERROR）

### 2. 配置项添加

在 `app/config.py` 中添加：

```python
class Settings(BaseSettings):
    # ... 其他配置 ...

    # Stats 配置
    STATS_CURRENCY: str = "CNY"  # 默认货币单位
```

### 3. 生产环境 Redis 配置（必须）

**⚠️ 重要**：由于 V1 成本数据只存储在 Redis 中，必须确保 Redis 持久化配置正确，否则重启会导致所有成本数据丢失。

**redis.conf 必需配置**：

```bash
# RDB 持久化（快照模式）
save 900 1              # 15分钟内有1次写入就保存
save 300 10             # 5分钟内有10次写入就保存
save 60 10000           # 1分钟内有10000次写入就保存

# AOF 持久化（日志模式，推荐）
appendonly yes                    # 开启 AOF
appendfsync everysec              # 每秒同步（性能与安全的平衡）
auto-aof-rewrite-percentage 100   # AOF 文件增长100%时重写
auto-aof-rewrite-min-size 64mb    # AOF 最小64MB才触发重写

# 持久化优化
dir /var/lib/redis                # 持久化文件目录
dbfilename dump.rdb               # RDB 文件名
appendfilename "appendonly.aof"   # AOF 文件名
```

**部署检查清单**：
- [ ] 确认 `redis.conf` 已配置 RDB 或 AOF（建议两者都开）
- [ ] 确认持久化目录有足够磁盘空间和写入权限
- [ ] 测试 Redis 重启后数据是否保留
- [ ] 配置定期备份 `/var/lib/redis/dump.rdb` 和 `appendonly.aof`
- [ ] 监控告警：Redis 持久化失败应立即通知

**Docker 部署示例**：

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes --appendfsync everysec
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"

volumes:
  redis_data:  # 使用命名卷持久化数据
```

### 4. 时间处理

- 所有时间使用 UTC
- `time_range=all` 的行为：
  - **成本统计**：固定 90 天前（对应 Redis TTL）
  - **任务统计**：查询用户最早任务记录
- 前端负责转换为用户本地时区

### 5. 性能优化

**数据库优化**：
```sql
-- 已有索引（确认是否存在）
CREATE INDEX idx_task_user_created ON tasks(user_id, created_at) WHERE deleted_at IS NULL;
CREATE INDEX idx_task_status ON tasks(status) WHERE deleted_at IS NULL;
CREATE INDEX idx_task_stages_task ON task_stages(task_id);
CREATE INDEX idx_task_stages_status ON task_stages(status);
```

**缓存策略**：
- 成本趋势可以缓存 5-10 分钟（Redis）
- 任务统计可以缓存 1-2 分钟

### 6. 错误处理

- 使用 `BusinessError` 抛出业务异常
- 日期格式错误：ErrorCode.PARAMETER_ERROR (40000)
- 数据查询失败：ErrorCode.SYSTEM_ERROR (50000)
- Granularity 不支持：ErrorCode.PARAMETER_ERROR

### 7. 国际化

成本统计文案需要支持中英文：
- 在 `app/i18n/zh.json` 和 `app/i18n/en.json` 中添加相关错误码

### 8. 测试要求

- `tests/test_stats_service.py`: 测试 StatsService 逻辑
- `tests/test_stats_api.py`: 测试 API 端点
- Mock CostTracker 和数据库查询

---

## V2 规划（强烈建议尽快实现）

**优先级说明**：V2 不是"可选优化"，而是**生产环境必须实现的数据持久化方案**。V1 仅适用于测试/演示环境。

### 核心改进：数据库双写架构（P0 优先级）

**问题**：V1 成本数据只存 Redis，存在数据丢失风险
**解决**：实现 Redis（快速查询）+ PostgreSQL（持久化）双写架构

#### 1. 新增 `service_usage` 表（必须实现）

**用途**：
- 持久化成本数据，防止 Redis 故障导致数据丢失
- 支持按用户/任务维度统计成本
- 支持长期（年度）成本分析和审计

**表结构**：

```sql
CREATE TABLE service_usage (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    task_id UUID REFERENCES tasks(id),
    service_type VARCHAR(50) NOT NULL,  -- asr/llm/storage
    provider VARCHAR(50) NOT NULL,      -- tencent/qwen/doubao等
    cost DECIMAL(10, 4) NOT NULL,       -- 实际成本（元）
    tokens INT,                         -- LLM token数（可选）
    duration_seconds INT,               -- ASR 时长（可选）
    request_params JSONB,               -- 请求参数（调试用）
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_service_usage_user ON service_usage(user_id, created_at);
CREATE INDEX idx_service_usage_task ON service_usage(task_id);
CREATE INDEX idx_service_usage_created ON service_usage(created_at);
CREATE INDEX idx_service_usage_type ON service_usage(service_type, provider);
```

#### 2. CostTracker 双写实现

**关键设计决策**：
- **同步/异步兼容**：CostTracker 在 Celery Worker（同步）和 FastAPI（异步）两种环境下调用，需要分别处理
- **Session 并发安全**：不存储 Session 实例，使用 SessionFactory 每次创建新会话
- **user_id 处理策略**：DB 写入要求 user_id（NOT NULL 约束）；CostOptimizer 可缺省 user_id，此时仅写 Redis，跳过 DB
- **双写失败策略**：Redis 失败降级，DB 失败记录日志和指标，避免静默丢数据

**修改 `app/core/cost_optimizer.py`**：

```python
from typing import Callable, Optional
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

class CostTracker:
    """成本追踪器（支持 Redis + PostgreSQL 双写）

    设计说明：
    - 支持同步（Celery）和异步（FastAPI）两种调用环境
    - 使用 SessionFactory 而非存储 Session 实例，避免并发冲突
    - user_id 处理：DB 写入要求 user_id；CostOptimizer 可缺省，仅写 Redis
    - 双写失败时记录日志和指标，避免静默丢数据
    """

    def __init__(
        self,
        use_redis: bool = True,
        sync_session_factory: Optional[Callable[[], Session]] = None,
        async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        require_db_persistence: bool = True,  # 新增：生产环境强制 DB 持久化
    ):
        """初始化成本追踪器

        Args:
            use_redis: 是否使用 Redis 存储
            sync_session_factory: 同步数据库会话工厂（Celery 用）
            async_session_factory: 异步数据库会话工厂（FastAPI 用）
            require_db_persistence: 是否要求数据库持久化（生产环境必须 True）

        Raises:
            ValueError: 如果 require_db_persistence=True 但未提供 session_factory
        """
        self._use_redis = use_redis
        self._sync_session_factory = sync_session_factory
        self._async_session_factory = async_session_factory
        self._lock = threading.Lock()
        self._records: List[UsageRecord] = []
        self._daily_cache: Dict[date, float] = {}

        # 统计指标（用于监控双写失败，使用滑动窗口避免累计值告警）
        from collections import deque
        from datetime import datetime
        self._redis_write_failures: deque = deque(maxlen=1000)  # 保留最近 1000 次失败时间戳
        self._db_write_failures: deque = deque(maxlen=1000)
        self._total_writes = 0  # 总写入次数（用于计算成功率）

        # ⚠️ 生产环境数据持久化检查
        if require_db_persistence:
            if not sync_session_factory and not async_session_factory:
                logger.critical(
                    "CRITICAL: CostTracker initialized with require_db_persistence=True "
                    "but no session_factory provided! Cost data will NOT be persisted to database!"
                )
                raise ValueError(
                    "require_db_persistence=True requires sync_session_factory or async_session_factory"
                )
            logger.info(
                f"CostTracker initialized with DB persistence enabled "
                f"(sync={bool(sync_session_factory)}, async={bool(async_session_factory)})"
            )

        # Redis 初始化（现有代码）
        if not use_redis:
            self._redis_client = None
            logger.info("CostTracker initialized in memory mode")
            return

        try:
            from worker.redis_client import get_sync_redis_client
            self._redis_client = get_sync_redis_client()
            logger.info("CostTracker initialized with Redis persistence")
        except Exception as exc:
            logger.warning(f"Failed to initialize Redis: {exc}")
            self._use_redis = False
            self._redis_client = None

    def record_usage(
        self,
        service_type: str,
        service_name: str,
        request_params: Dict[str, Any],
        estimated_cost: float,
        user_id: Optional[str] = None,  # ⚠️ V2 必传，V1 可选（兼容过渡）
        task_id: Optional[str] = None,
    ) -> None:
        """同步记录使用情况（用于 Celery Worker）

        双写逻辑：
        1. 写入 Redis（快速查询，90天TTL）
        2. 写入 PostgreSQL（持久化，永久保存，仅当 user_id 不为空时）

        Args:
            user_id: 用户ID
                - V2 双写模式：必传（否则 DB 写入会跳过并记录警告）
                - V1 仅 Redis 模式：可选（仅用于成本估算，无实际用户关联）
            task_id: 任务ID（可选）

        注意：
            - CostOptimizer.select_service() 在服务选择时调用，此时无 user_id
            - 缺少 user_id 时，只写 Redis，跳过 DB 写入（避免 NOT NULL 错误）
            - 建议：V2 环境下，所有业务代码调用都应传入 user_id
        """
        record = UsageRecord(
            timestamp=datetime.now(),
            service_type=service_type,
            service_name=service_name,
            request_params=request_params,
            estimated_cost=estimated_cost,
        )

        self._total_writes += 1  # 统计总写入次数

        # 1. 写入 Redis
        redis_success = False
        with self._lock:
            if self._use_redis and self._redis_client:
                try:
                    self._record_to_redis(record)
                    redis_success = True
                except Exception as exc:
                    self._redis_write_failures.append(datetime.now())  # 记录失败时间戳
                    logger.error(
                        f"Redis write failed (recent failures: {len(self._redis_write_failures)}): {exc}",
                        extra={"service_type": service_type, "provider": service_name}
                    )
                    # Redis 失败不影响 DB 写入，继续
            else:
                # 内存模式降级
                self._records.append(record)
                today = record.timestamp.date()
                self._daily_cache[today] = self._daily_cache.get(today, 0.0) + estimated_cost

        # 2. 写入数据库（同步模式）
        if self._sync_session_factory:
            # ⚠️ 检查 user_id（NOT NULL 约束）
            if not user_id:
                logger.warning(
                    f"CostTracker.record_usage() called without user_id, skipping DB write. "
                    f"This is acceptable for CostOptimizer.select_service() but should be avoided in business code.",
                    extra={"service_type": service_type, "provider": service_name, "cost": estimated_cost}
                )
            else:
                try:
                    self._record_to_database_sync(record, user_id, task_id)
                except Exception as exc:
                    self._db_write_failures.append(datetime.now())  # 记录失败时间戳
                    logger.error(
                        f"DB write failed (recent failures: {len(self._db_write_failures)}): {exc}",
                        extra={
                            "service_type": service_type,
                            "provider": service_name,
                            "user_id": user_id,
                            "task_id": task_id,
                            "redis_success": redis_success,
                        }
                    )
                    # DB 写入失败，但 Redis 可能已成功，记录告警
                    if not redis_success:
                        logger.critical(
                            "CRITICAL: Both Redis and DB write failed, data loss occurred!",
                            extra={"cost": estimated_cost}
                        )

    async def record_usage_async(
        self,
        service_type: str,
        service_name: str,
        request_params: Dict[str, Any],
        estimated_cost: float,
        user_id: Optional[str] = None,  # ⚠️ V2 建议必传
        task_id: Optional[str] = None,
    ) -> None:
        """异步记录使用情况（用于 FastAPI）

        ⚠️ 警告：当前实现使用同步 Redis 客户端 + threading.Lock，
        在异步环境下会阻塞事件循环！

        解决方案（二选一）：
        1. 使用 asyncio.to_thread() 包装同步 Redis 操作
        2. 替换为异步 Redis 客户端（如 aioredis/redis.asyncio）

        注意：当前 CostOptimizer 在 SmartFactory 里调用时是同步的，
        所以实际应用场景主要是 record_usage()。
        这个方法为未来异步重构预留。
        """
        import asyncio

        record = UsageRecord(
            timestamp=datetime.now(),
            service_type=service_type,
            service_name=service_name,
            request_params=request_params,
            estimated_cost=estimated_cost,
        )

        self._total_writes += 1

        # 1. 写入 Redis（⚠️ 同步操作，会阻塞事件循环）
        # 生产环境建议用 asyncio.to_thread() 包装
        redis_success = False
        try:
            # 方案 1：包装同步操作到线程（推荐）
            def _write_redis_sync():
                with self._lock:
                    if self._use_redis and self._redis_client:
                        self._record_to_redis(record)
                        return True
                return False

            redis_success = await asyncio.to_thread(_write_redis_sync)
        except Exception as exc:
            self._redis_write_failures.append(datetime.now())
            logger.error(f"Redis write failed (recent failures: {len(self._redis_write_failures)}): {exc}")

        # 2. 写入数据库（异步）
        if self._async_session_factory:
            if not user_id:
                logger.warning(
                    f"CostTracker.record_usage_async() called without user_id, skipping DB write.",
                    extra={"service_type": service_type, "provider": service_name}
                )
            else:
                try:
                    await self._record_to_database_async(record, user_id, task_id)
                except Exception as exc:
                    self._db_write_failures.append(datetime.now())
                    logger.error(
                        f"DB write failed (recent failures: {len(self._db_write_failures)}): {exc}",
                        extra={"redis_success": redis_success}
                    )
                    if not redis_success:
                        logger.critical("CRITICAL: Both Redis and DB write failed!")

    def _record_to_database_sync(
        self,
        record: UsageRecord,
        user_id: str,
        task_id: Optional[str]
    ) -> None:
        """同步写入数据库（Celery 用）"""
        from app.models.service_usage import ServiceUsage

        # 每次创建新会话，避免并发冲突
        session = self._sync_session_factory()
        try:
            usage = ServiceUsage(
                user_id=user_id,
                task_id=task_id,
                service_type=record.service_type,
                provider=record.service_name,
                cost=record.estimated_cost,
                request_params=record.request_params,
                created_at=record.timestamp
            )
            session.add(usage)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise
        finally:
            session.close()

    async def _record_to_database_async(
        self,
        record: UsageRecord,
        user_id: str,
        task_id: Optional[str]
    ) -> None:
        """异步写入数据库（FastAPI 用）"""
        from app.models.service_usage import ServiceUsage

        # 每次创建新会话，避免并发冲突
        async with self._async_session_factory() as session:
            try:
                usage = ServiceUsage(
                    user_id=user_id,
                    task_id=task_id,
                    service_type=record.service_type,
                    provider=record.service_name,
                    cost=record.estimated_cost,
                    request_params=record.request_params,
                    created_at=record.timestamp
                )
                session.add(usage)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                raise

    def get_write_failure_stats(self, window_minutes: int = 5) -> Dict[str, Any]:
        """获取双写失败统计（用于监控告警）

        Args:
            window_minutes: 统计窗口（分钟），默认 5 分钟

        Returns:
            {
                "redis_failures_recent": 最近 N 分钟内的 Redis 失败次数,
                "db_failures_recent": 最近 N 分钟内的 DB 失败次数,
                "redis_failures_total": 总 Redis 失败次数（最近 1000 次）,
                "db_failures_total": 总 DB 失败次数（最近 1000 次）,
                "total_writes": 总写入次数,
                "success_rate": 成功率（%）,
                "window_minutes": 统计窗口大小
            }
        """
        from datetime import timedelta

        now = datetime.now()
        window_start = now - timedelta(minutes=window_minutes)

        # 统计窗口内的失败次数
        redis_recent = sum(1 for ts in self._redis_write_failures if ts >= window_start)
        db_recent = sum(1 for ts in self._db_write_failures if ts >= window_start)

        total_failures = len(self._redis_write_failures) + len(self._db_write_failures)
        success_rate = ((self._total_writes - total_failures) / self._total_writes * 100) if self._total_writes > 0 else 100.0

        return {
            "redis_failures_recent": redis_recent,
            "db_failures_recent": db_recent,
            "redis_failures_total": len(self._redis_write_failures),
            "db_failures_total": len(self._db_write_failures),
            "total_writes": self._total_writes,
            "success_rate": round(success_rate, 2),
            "window_minutes": window_minutes,
        }
```

**调用点修改要求（重要）**：

V2 双写模式下，**业务代码**调用 `cost_tracker.record_usage()` 时必须传入 `user_id` 和 `task_id`：

```python
# ❌ 错误：缺少 user_id（仅 Redis 写入，跳过 DB）
cost_tracker.record_usage("llm", "qwen", params, 0.05)

# ✅ 正确：业务代码必须传 user_id 和 task_id
cost_tracker.record_usage(
    "llm",
    "qwen",
    params,
    0.05,
    user_id=task.user_id,  # 从 Task 对象获取
    task_id=task.id
)
```

**主要调用点清单**：

1. **`app/core/cost_optimizer.py` - CostOptimizer.select_service()** (特殊)
   - 场景：服务选择时估算成本，此时无 user/task 上下文
   - 处理：不传 user_id，仅写 Redis，跳过 DB（可接受）
   - 无需修改

2. **`app/services/llm/base.py` - LLM 服务调用后** (必须修改)
   - 场景：Worker 处理任务时调用 LLM
   - 处理：必须传 user_id 和 task_id
   - 修改示例：
     ```python
     # 在 Worker 上下文中，从 task 对象获取
     cost_tracker.record_usage(
         "llm", selected_service, params, cost,
         user_id=task.user_id,
         task_id=task.id
     )
     ```

3. **`app/services/asr/base.py` - ASR 服务调用后** (必须修改)
   - 同上

4. **`app/services/storage/base.py` - 存储服务调用后** (必须修改)
   - 同上

5. **`worker/tasks/process_audio.py` - Worker 任务处理时** (必须修改)
   - 场景：任务编排层调用各个服务
   - 处理：必须传 user_id 和 task_id

#### 3. CostTracker 初始化配置

**Worker 环境（Celery）**：

在 `worker/celery_app.py` 或应用启动时初始化：

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.cost_optimizer import CostTracker, CostOptimizer, CostOptimizerConfig
from app.config import settings

# 创建同步数据库引擎（Celery 用）
sync_engine = create_engine(
    settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"),  # 使用同步驱动
    pool_size=5,
    max_overflow=10
)
SyncSessionFactory = sessionmaker(bind=sync_engine)

# 初始化全局 cost_tracker（带数据库双写）
cost_tracker = CostTracker(
    use_redis=True,
    sync_session_factory=SyncSessionFactory  # 传入工厂，不传实例
)

# CostOptimizer 使用 cost_tracker
cost_optimizer_config = CostOptimizerConfig(
    enable_cost_tracking=True,
    enable_redis_persistence=True
)
cost_optimizer = CostOptimizer(cost_optimizer_config)
cost_optimizer.tracker = cost_tracker  # 覆盖默认 tracker
```

**FastAPI 环境（可选，未来异步重构用）**：

在 `app/main.py` 或应用启动时：

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.cost_optimizer import CostTracker

# 创建异步数据库引擎
async_engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,
    max_overflow=10
)
AsyncSessionFactory = async_sessionmaker(
    async_engine,
    expire_on_commit=False
)

# 初始化 cost_tracker（异步版本）
cost_tracker_async = CostTracker(
    use_redis=True,
    async_session_factory=AsyncSessionFactory
)
```

**⚠️ 重要**：
- Worker 和 FastAPI 分别使用不同的 CostTracker 实例
- Worker 使用同步 Session（`psycopg2`）
- FastAPI 使用异步 Session（`asyncpg`）
- 不要在两者之间共享 Session 或 SessionFactory

#### 4. 双写失败监控与告警

**监控指标暴露**：

在 `app/api/v1/health.py` 或独立监控端点暴露指标：

```python
@router.get("/metrics/cost_tracker")
async def get_cost_tracker_metrics(window_minutes: int = Query(5, ge=1, le=60)):
    """暴露 CostTracker 双写失败统计（用于 Prometheus/监控）

    Args:
        window_minutes: 统计窗口（分钟），默认 5 分钟
    """
    stats = cost_tracker.get_write_failure_stats(window_minutes=window_minutes)
    return success(data=stats)

# 返回示例：
# {
#   "redis_failures_recent": 2,      # 最近 5 分钟内失败 2 次
#   "db_failures_recent": 0,         # 最近 5 分钟内失败 0 次
#   "redis_failures_total": 15,      # 总共失败 15 次（最近 1000 次中）
#   "db_failures_total": 3,          # 总共失败 3 次（最近 1000 次中）
#   "total_writes": 10000,           # 总写入次数
#   "success_rate": 99.82,           # 成功率 99.82%
#   "window_minutes": 5              # 统计窗口 5 分钟
# }
```

**告警规则（Prometheus AlertManager）**：

使用 `redis_failures_recent` 和 `db_failures_recent` 字段（窗口内失败次数），避免累计值一直报警：

```yaml
groups:
  - name: cost_tracker_alerts
    rules:
      # Redis 写入失败告警（窗口内）
      - alert: CostTrackerRedisWriteFailures
        expr: cost_tracker_redis_failures_recent > 10
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "CostTracker Redis 写入失败超过 10 次"
          description: "最近 5 分钟内 Redis 写入失败 {{ $value }} 次，可能影响成本统计实时性"

      # 数据库写入失败告警（窗口内，严重）
      - alert: CostTrackerDBWriteFailures
        expr: cost_tracker_db_failures_recent > 5
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "CostTracker DB 写入失败超过 5 次"
          description: "最近 5 分钟内数据库写入失败 {{ $value }} 次，可能导致成本数据丢失"

      # 双写全失败告警（窗口内，紧急）
      - alert: CostTrackerBothWriteFailures
        expr: (cost_tracker_redis_failures_recent > 0) AND (cost_tracker_db_failures_recent > 0)
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "CostTracker 双写同时失败"
          description: "最近 5 分钟内 Redis 失败 {{ $labels.redis_failures_recent }} 次，DB 失败 {{ $labels.db_failures_recent }} 次，正在丢失成本数据！"

      # 成功率告警（可选）
      - alert: CostTrackerLowSuccessRate
        expr: cost_tracker_success_rate < 95.0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "CostTracker 成功率低于 95%"
          description: "当前成功率 {{ $value }}%，可能存在 Redis 或数据库问题"
```

**关键改进**：
- ✅ 使用 `_recent` 字段（窗口内数据），避免累计值一直告警
- ✅ `for: 2m` 持续时间，避免瞬时抖动
- ✅ 新增成功率告警，全局监控写入健康度

**日志聚合（ELK/Loki）**：

搜索关键词进行告警：
- `CRITICAL: Both Redis and DB write failed` - 双写全失败
- `Redis write failed` - Redis 写入失败
- `DB write failed` - 数据库写入失败
- `CostTracker.record_usage() called without user_id` - 缺少 user_id 警告（预期在 CostOptimizer 中出现）

#### 5. StatsService 查询策略

**短期数据（90天内）**：优先查 Redis（快）
**长期数据（>90天）**：查 PostgreSQL
**用户/任务维度**：只能查 PostgreSQL

```python
async def get_cost_overview(...):
    if (end - start).days <= 90 and not need_user_dimension:
        # 查 Redis（快）
        records = cost_tracker.get_records_in_range(start, end)
    else:
        # 查数据库（支持长期+按用户）
        stmt = select(ServiceUsage).where(
            ServiceUsage.created_at >= start,
            ServiceUsage.created_at <= end,
            ServiceUsage.user_id == user.id  # V2 支持
        )
        result = await db.execute(stmt)
        records = result.scalars().all()
```

#### 6. ServiceUsage 模型定义

**新增 `app/models/service_usage.py`**：

```python
from sqlalchemy import Column, String, Integer, DECIMAL, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import BaseModel

class ServiceUsage(BaseModel):
    """服务使用记录（成本追踪）

    注意：user_id 和 task_id 使用 UUID(as_uuid=False) 保持与现有模型一致
    （User.id 和 Task.id 都是字符串 UUID，不是 Python UUID 对象）
    """

    __tablename__ = "service_usage"

    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(UUID(as_uuid=False), ForeignKey("tasks.id"), nullable=True, index=True)
    service_type = Column(String(50), nullable=False)  # asr/llm/storage
    provider = Column(String(50), nullable=False)      # tencent/qwen/doubao等
    cost = Column(DECIMAL(10, 4), nullable=False)      # 实际成本（元）
    tokens = Column(Integer, nullable=True)            # LLM token数
    duration_seconds = Column(Integer, nullable=True)  # ASR 时长
    request_params = Column(JSONB, nullable=True)      # 请求参数（调试用）

    # 关系
    user = relationship("User", back_populates="service_usages")
    task = relationship("Task", back_populates="service_usages")

    # 索引（在表定义之外）
    __table_args__ = (
        Index("idx_service_usage_user_created", "user_id", "created_at"),
        Index("idx_service_usage_type_provider", "service_type", "provider"),
    )
```

**修改 `app/models/user.py`**：

```python
class User(BaseModel):
    # ... 现有字段 ...

    # 新增关系
    service_usages = relationship("ServiceUsage", back_populates="user", cascade="all, delete-orphan")
```

**修改 `app/models/task.py`**：

```python
class Task(BaseModel):
    # ... 现有字段 ...

    # 新增关系
    service_usages = relationship("ServiceUsage", back_populates="task", cascade="all, delete-orphan")
```

### 其他新增功能

#### 7. 按用户/任务维度统计成本
   - 基于 `service_usage` 表实现
   - API 增加 `?user_id=xxx` 参数（管理员可查所有用户）

#### 8. 支持更多时间粒度
   - hour: 小时粒度（数据库 `date_trunc('hour', created_at)`）
   - week/month: 聚合查询

#### 9. 最贵任务排名
   - `SELECT task_id, SUM(cost) FROM service_usage GROUP BY task_id ORDER BY SUM(cost) DESC LIMIT 10`

#### 10. 成本预测与告警
   - 基于历史趋势预测未来成本
   - 成本异常告警（日消耗超过平均值 2 倍）

### 数据库迁移

**Alembic 迁移脚本**：

```bash
# 生成迁移文件
alembic revision --autogenerate -m "add service_usage table for cost tracking"

# 执行迁移
alembic upgrade head
```

迁移文件内容参考上文"表结构"部分的 SQL。

---

## API 测试示例

```bash
# 测试成本概览（本周）
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/api/v1/stats/costs/overview?time_range=week"

# 测试任务统计（自定义时间范围）
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/api/v1/stats/tasks/overview?start_date=2024-01-01&end_date=2024-01-31"

# 测试趋势（按天）
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/api/v1/stats/tasks/trend?granularity=day&time_range=month"

# 测试不支持的粒度（应返回错误）
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/api/v1/stats/costs/trend?granularity=hour"
```

---

## 前端集成建议

### 图表推荐

1. **成本概览**
   - 饼图：各服务类型成本占比
   - 柱状图：各服务商成本排名
   - 卡片：总成本

2. **成本趋势**
   - 折线图：时间 vs 成本
   - 堆叠面积图：各服务类型成本趋势

3. **任务统计**
   - 环形图：任务状态分布
   - 数字卡片：总任务数、成功率、平均时长

4. **任务趋势**
   - 折线图：任务数量趋势
   - 双轴图：任务数量 + 成功率

### V1 限制提示

前端应显示提示信息：
- 成本统计页面："当前显示全局成本数据，后续版本将支持按用户统计"
- 成本趋势页面："当前仅支持按天查看趋势"

---

## 完成标准

### V1 完成标准（测试/演示环境）

- [ ] 4 个 API 端点全部实现
- [ ] CostTracker 新增查询方法（`get_records_in_range`, `get_daily_summary`）
- [ ] 返回数据符合 Schema 定义
- [ ] 代码通过 pre-commit 检查（Black, isort, Flake8, mypy）
- [ ] API 文档在 `/docs` 中正确显示
- [ ] 使用统一的响应格式 (`success()` helper)
- [ ] 使用 `BusinessError` 处理异常
- [ ] 添加适当的日志记录
- [ ] V1 限制已在代码注释中说明
- [ ] 配置项 STATS_CURRENCY 已添加
- [ ] **Redis 持久化配置已验证**（RDB + AOF）

### V2 完成标准（生产环境必须）

- [ ] `service_usage` 表已创建（Alembic 迁移）
- [ ] `ServiceUsage` 模型已定义
- [ ] `User` 和 `Task` 模型关系已添加
- [ ] CostTracker 已改为双写架构：
  - [ ] `__init__` 支持 `sync_session_factory`、`async_session_factory` 和 `require_db_persistence`
  - [ ] `record_usage()` 实现同步双写（Redis + PostgreSQL）
  - [ ] `record_usage_async()` 实现异步双写（使用 `asyncio.to_thread` 包装同步 Redis）
  - [ ] `user_id` 参数改为可选（兼容 CostOptimizer），缺失时跳过 DB 写入并记录警告
  - [ ] 双写失败记录日志和时间戳（deque，窗口 1000）
  - [ ] `get_write_failure_stats()` 支持窗口统计，返回 `_recent` 和 `_total` 字段
- [ ] Worker 初始化配置（同步 Session）
  - [ ] `require_db_persistence=True`（生产环境强制 DB 持久化）
  - [ ] 缺少 session_factory 时抛出 ValueError
- [ ] 业务代码调用点已传入 `user_id` 和 `task_id`：
  - [ ] `app/core/cost_optimizer.py` - CostOptimizer.select_service()：无需修改（允许缺少 user_id）
  - [ ] `app/services/llm/base.py`：必须传 user_id 和 task_id
  - [ ] `app/services/asr/base.py`：必须传 user_id 和 task_id
  - [ ] `app/services/storage/base.py`：必须传 user_id 和 task_id
  - [ ] `worker/tasks/process_audio.py`：必须传 user_id 和 task_id
- [ ] 监控指标端点已实现（`/metrics/cost_tracker`）
- [ ] 告警规则已配置（Prometheus/日志聚合）
- [ ] StatsService 支持查询数据库（长期/按用户）
- [ ] 测试通过：
  - [ ] 双写成功场景
  - [ ] Redis 失败但 DB 成功
  - [ ] DB 失败但 Redis 成功
  - [ ] 双写全失败（记录 CRITICAL 日志）

---

## 参考资料

- 项目架构：`CLAUDE.md`
- 响应格式：`app/core/response.py`
- 异常处理：`app/core/exceptions.py`
- 错误码：`app/i18n/codes.py`
- CostTracker：`app/core/cost_optimizer.py`
- TaskStage：`app/models/task_stage.py`, `app/core/task_stages.py`
- 现有 API 示例：`app/api/v1/tasks.py`, `app/api/v1/summaries.py`
