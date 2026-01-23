# ASR 配额系统 API 文档

## 背景

本次重构将 ASR 配额系统进行了调整：

| 概念 | 数据表 | API | 说明 |
|------|--------|-----|------|
| 平台定价配置 | `asr_pricing_configs` | 无（内部使用） | 各 ASR 平台的单价和免费额度，不对外暴露 |
| 平台免费额度消耗 | `asr_usage_periods` | 无（内部使用） | 追踪平台免费额度消耗，不对外暴露 |
| 用户配额限制 | `asr_user_quotas` | `/api/v1/asr/quotas` | 限制用户的 ASR 使用量上限 |

### 设计原则

1. **平台定价信息不对外暴露** - 单价、免费额度等运营敏感信息只在后端内部使用
2. **用户只关心自己的配额** - 用户只需要知道自己还能用多少，不需要知道平台成本
3. **配额限制不可自行修改** - 用户配额的刷新接口仅供管理员使用

### 主要变更

1. **移除** `/api/v1/asr/pricing` - 定价配置不对外暴露
2. **移除** `/api/v1/asr/free-quota` - 免费额度消耗不对外暴露
3. **保留** `/api/v1/asr/quotas` - 用户配额查询（刷新接口需管理员权限）

---

## 用户配额限制 API

用户配额用于限制单个用户在某个时间窗口内的 ASR 使用量。

### 1. 获取当前用户配额

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
| provider | string | 服务商：tencent, aliyun, volcengine |
| variant | string | 服务变体：file, file_fast |
| window_type | string | 窗口类型：day（日），month（月），total（总计） |
| window_start | datetime | 配额窗口开始时间 |
| window_end | datetime | 配额窗口结束时间 |
| quota_seconds | float | 配额上限（秒） |
| used_seconds | float | 已使用量（秒） |
| status | string | 状态：active（正常），exhausted（已用尽） |

---

### 2. 获取全局配额（管理员）

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

### 3. 刷新用户配额（管理员）

创建或更新指定用户的配额限制。

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

**权限**：需要管理员权限

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

### 4. 刷新全局配额（管理员）

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

**响应格式**：同上

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
