# ASR 配额系统 API 文档

## 背景

本次重构将 ASR 配额系统拆分为三个独立的概念：

| 概念 | 数据表 | API 路径 | 说明 |
|------|--------|----------|------|
| 平台定价配置 | `asr_pricing_configs` | `/api/v1/asr/pricing` | 各 ASR 平台的单价和免费额度（**只读**，不可通过 API 修改） |
| 平台免费额度消耗 | `asr_usage_periods` | `/api/v1/asr/free-quota` | 追踪平台免费额度在当前周期的消耗量 |
| 用户配额限制 | `asr_user_quotas` | `/api/v1/asr/quotas` | 限制单个用户或全局的 ASR 使用量上限 |

### 主要变更

1. **新增 `/api/v1/asr/pricing`** - 平台定价配置查询接口（只读）
2. **表重命名** - `asr_quotas` → `asr_user_quotas`（API 路径不变）
3. **定价配置不可修改** - 出于安全考虑，定价和免费额度不提供修改接口

---

## 1. 平台定价配置 API（只读）

### 1.1 获取所有定价配置

```
GET /api/v1/asr/pricing?enabled_only=true
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| enabled_only | boolean | 否 | 是否只返回已启用的配置，默认 false |

**响应**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "items": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "provider": "volcengine",
        "variant": "file",
        "cost_per_hour": 0.80,
        "free_quota_seconds": 72000,
        "free_quota_hours": 20.0,
        "reset_period": "yearly",
        "is_enabled": true,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z"
      },
      {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "provider": "tencent",
        "variant": "file_fast",
        "cost_per_hour": 3.10,
        "free_quota_seconds": 18000,
        "free_quota_hours": 5.0,
        "reset_period": "monthly",
        "is_enabled": true,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z"
      }
    ],
    "total": 6
  },
  "traceId": "abc123"
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| provider | string | 服务商：tencent, aliyun, volcengine |
| variant | string | 服务变体：file（普通），file_fast（极速） |
| cost_per_hour | float | 单价（元/小时） |
| free_quota_seconds | float | 免费额度（秒） |
| free_quota_hours | float | 免费额度（小时），计算字段 |
| reset_period | string | 刷新周期：none（不刷新），monthly（月度），yearly（年度） |
| is_enabled | boolean | 是否启用 |

---

### 1.2 获取指定定价配置

```
GET /api/v1/asr/pricing/{provider}/{variant}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| provider | string | 是 | 服务商：tencent, aliyun, volcengine |
| variant | string | 是 | 服务变体：file, file_fast |

**响应**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "provider": "volcengine",
    "variant": "file",
    "cost_per_hour": 0.80,
    "free_quota_seconds": 72000,
    "free_quota_hours": 20.0,
    "reset_period": "yearly",
    "is_enabled": true,
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  },
  "traceId": "abc123"
}
```

---

## 2. 平台免费额度 API

### 2.1 获取免费额度状态

查询所有有免费额度的 ASR 服务的当前使用状态。

```
GET /api/v1/asr/free-quota
```

**响应**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "providers": [
      {
        "provider": "volcengine",
        "variant": "file",
        "free_quota_seconds": 72000,
        "used_seconds": 3600,
        "remaining_seconds": 68400,
        "reset_period": "yearly",
        "period_start": "2025-01-01T00:00:00Z",
        "period_end": "2025-12-31T23:59:59Z",
        "cost_per_hour": 0.80,
        "free_quota_hours": 20.0,
        "used_hours": 1.0,
        "remaining_hours": 19.0,
        "usage_percent": 5.0
      },
      {
        "provider": "tencent",
        "variant": "file_fast",
        "free_quota_seconds": 18000,
        "used_seconds": 9000,
        "remaining_seconds": 9000,
        "reset_period": "monthly",
        "period_start": "2025-01-01T00:00:00Z",
        "period_end": "2025-01-31T23:59:59Z",
        "cost_per_hour": 3.10,
        "free_quota_hours": 5.0,
        "used_hours": 2.5,
        "remaining_hours": 2.5,
        "usage_percent": 50.0
      }
    ]
  },
  "traceId": "abc123"
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| free_quota_seconds | float | 总免费额度（秒） |
| used_seconds | float | 当前周期已使用量（秒） |
| remaining_seconds | float | 剩余免费额度（秒） |
| reset_period | string | 刷新周期 |
| period_start | datetime | 当前周期开始时间 |
| period_end | datetime | 当前周期结束时间 |
| cost_per_hour | float | 超出免费额度后的单价 |
| usage_percent | float | 使用率（百分比，0-100） |

---

### 2.2 成本预估

根据预计时长，计算各提供商的成本预估。

```
POST /api/v1/asr/free-quota/estimate-cost
Content-Type: application/json

{
  "duration_seconds": 7200,
  "variant": "file"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| duration_seconds | float | 是 | 预计时长（秒），必须大于 0 |
| variant | string | 否 | 服务变体，默认 "file" |

**响应**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "estimates": [
      {
        "provider": "volcengine",
        "variant": "file",
        "total_duration": 7200,
        "free_consumed": 7200,
        "paid_duration": 0,
        "estimated_cost": 0.0,
        "full_cost": 1.60,
        "remaining_free_quota": 68400,
        "cost_per_hour": 0.80
      },
      {
        "provider": "tencent",
        "variant": "file",
        "total_duration": 7200,
        "free_consumed": 0,
        "paid_duration": 7200,
        "estimated_cost": 2.50,
        "full_cost": 2.50,
        "remaining_free_quota": 0,
        "cost_per_hour": 1.25
      }
    ],
    "recommended_provider": "volcengine",
    "recommendation_reason": "volcengine 有免费额度可用"
  },
  "traceId": "abc123"
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| total_duration | float | 总时长（秒） |
| free_consumed | float | 消耗免费额度（秒） |
| paid_duration | float | 需付费时长（秒） |
| estimated_cost | float | 预估成本（元），考虑免费额度后 |
| full_cost | float | 全价成本（元），不考虑免费额度 |
| remaining_free_quota | float | 剩余免费额度（秒） |
| recommended_provider | string | 推荐的提供商 |
| recommendation_reason | string | 推荐原因 |

---

### 2.3 获取提供商评分

获取各提供商的综合评分，用于理解调度器的决策逻辑。

```
GET /api/v1/asr/free-quota/provider-scores?variant=file
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| variant | string | 否 | 服务变体，默认 "file" |

**响应**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "scores": [
      {
        "provider": "volcengine",
        "variant": "file",
        "free_quota_score": 0.950,
        "health_score": 1.000,
        "cost_score": 0.800,
        "quota_score": 1.000,
        "total_score": 0.938,
        "remaining_free_seconds": 68400
      },
      {
        "provider": "tencent",
        "variant": "file",
        "free_quota_score": 0.000,
        "health_score": 0.950,
        "cost_score": 0.600,
        "quota_score": 1.000,
        "total_score": 0.608,
        "remaining_free_seconds": 0
      }
    ],
    "weights": {
      "free_quota": 0.40,
      "health": 0.25,
      "cost": 0.20,
      "quota": 0.15
    }
  },
  "traceId": "abc123"
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| free_quota_score | float | 免费额度得分（0-1），权重 40% |
| health_score | float | 健康得分（0-1），权重 25% |
| cost_score | float | 成本得分（0-1），权重 20% |
| quota_score | float | 用户配额得分（0-1），权重 15% |
| total_score | float | 综合得分（加权计算） |
| remaining_free_seconds | float | 剩余免费额度（秒） |
| weights | object | 各维度权重配置 |

---

## 3. 用户配额限制 API

用户配额用于限制单个用户在某个时间窗口内的 ASR 使用量，与平台定价是独立的概念。

### 3.1 获取当前用户配额

```
GET /api/v1/asr/quotas
```

**响应**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "items": [
      {
        "provider": "volcengine",
        "variant": "file",
        "window_type": "month",
        "window_start": "2025-01-01T00:00:00Z",
        "window_end": "2025-01-31T23:59:59Z",
        "quota_seconds": 36000,
        "used_seconds": 7200,
        "status": "active"
      }
    ]
  },
  "traceId": "abc123"
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| window_type | string | 窗口类型：day（日），month（月），total（总计） |
| window_start | datetime | 配额窗口开始时间 |
| window_end | datetime | 配额窗口结束时间 |
| quota_seconds | float | 配额上限（秒） |
| used_seconds | float | 已使用量（秒） |
| status | string | 状态：active（正常），exhausted（已用尽） |

---

### 3.2 获取全局配额（管理员）

获取全局配额配置（适用于所有用户）。

```
GET /api/v1/asr/quotas/global
```

**权限**：需要管理员权限

**响应**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "items": [
      {
        "provider": "volcengine",
        "variant": "file",
        "window_type": "month",
        "window_start": "2025-01-01T00:00:00Z",
        "window_end": "2025-01-31T23:59:59Z",
        "quota_seconds": 360000,
        "used_seconds": 72000,
        "status": "active"
      }
    ]
  },
  "traceId": "abc123"
}
```

---

### 3.3 刷新用户配额

创建或更新当前用户的配额限制。

```
POST /api/v1/asr/quotas/refresh
Content-Type: application/json

{
  "provider": "volcengine",
  "variant": "file",
  "window_type": "month",
  "quota_hours": 10,
  "reset": true
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| provider | string | 是 | 服务商 |
| variant | string | 否 | 服务变体，默认 "file" |
| window_type | string | 是 | 窗口类型：day, month, total |
| quota_seconds | float | 二选一 | 配额上限（秒） |
| quota_hours | float | 二选一 | 配额上限（小时） |
| window_start | datetime | total 时必填 | 自定义窗口开始时间 |
| window_end | datetime | total 时必填 | 自定义窗口结束时间 |
| used_seconds | float | 否 | 指定已使用量 |
| reset | boolean | 否 | 是否重置已使用量，默认 true |

**响应**

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "item": {
      "provider": "volcengine",
      "variant": "file",
      "window_type": "month",
      "window_start": "2025-01-01T00:00:00Z",
      "window_end": "2025-01-31T23:59:59Z",
      "quota_seconds": 36000,
      "used_seconds": 0,
      "status": "active"
    }
  },
  "traceId": "abc123"
}
```

---

### 3.4 刷新全局配额（管理员）

创建或更新全局配额限制。

```
POST /api/v1/asr/quotas/refresh-global
Content-Type: application/json

{
  "provider": "volcengine",
  "variant": "file",
  "window_type": "month",
  "quota_hours": 100,
  "reset": true
}
```

**权限**：需要管理员权限

**响应格式**：同 3.3

---

## 错误码

| 错误码 | 说明 |
|--------|------|
| 40000 | 参数错误 |
| 40100 | 未提供认证 Token |
| 40101 | Token 无效 |
| 40102 | Token 已过期 |
| 40300 | 权限不足 |
| 40401 | 资源未找到 |
| 40910 | ASR 配额超限 |
| 40911 | 所有 ASR 配额均已超限 |
