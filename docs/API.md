# AI 音视频内容助手 - API 契约

> 版本: 2.0 | 最后更新: 2024-12-19
> 
> ⚠️ 本文档是前后端**接口契约**，双方必须严格遵守
> 
> 📍 放置位置：后端仓库，前端只读引用

---

## 1. 通用约定

### 1.1 基础信息

| 项目 | 值 |
|------|-----|
| Base URL | `/api/v1` |
| 协议 | HTTPS (生产) / HTTP (开发) |
| 认证 | Bearer Token (JWT) |
| 内容类型 | `application/json` |

### 1.2 认证头

```
Authorization: Bearer <jwt_token>
```

JWT 由前端 Next.js 签发，后端验证。

### 1.3 语言头

```
Accept-Language: zh
```

| 值 | 语言 |
|----|------|
| `zh` | 中文（默认） |
| `en` | 英文 |

后端根据此头返回对应语言的 `message`。

---

## 2. 统一响应格式

### 2.1 响应结构

**所有 API 响应统一使用以下格式**：

```json
{
  "code": 0,
  "message": "成功",
  "data": {},
  "traceId": "a1b2c3d4e5f6"
}
```

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `code` | integer | ✅ | 业务状态码（不是 HTTP 状态码） |
| `message` | string | ✅ | 人类可读的消息（已国际化） |
| `data` | object/array/null | ✅ | 业务数据，失败时可为 null |
| `traceId` | string | ✅ | 链路追踪 ID，用于排查问题 |

### 2.2 成功响应示例

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "产品周会",
    "status": "completed"
  },
  "traceId": "req_abc123def456"
}
```

### 2.3 错误响应示例

```json
{
  "code": 40401,
  "message": "任务不存在",
  "data": null,
  "traceId": "req_abc123def456"
}
```

### 2.4 带参数的错误消息

```json
{
  "code": 40010,
  "message": "不支持的文件格式，仅支持：mp3, mp4, wav, m4a",
  "data": {
    "allowed": ["mp3", "mp4", "wav", "m4a"],
    "received": "pdf"
  },
  "traceId": "req_abc123def456"
}
```

---

## 3. 错误码体系

### 3.1 号段划分

| 号段 | 类别 | 说明 |
|------|------|------|
| `0` | 成功 | 请求成功 |
| `40000-40099` | 参数错误 | 请求参数校验失败 |
| `40100-40199` | 认证错误 | Token 相关问题 |
| `40300-40399` | 权限错误 | 无权访问 |
| `40400-40499` | 资源不存在 | 请求的资源不存在 |
| `40900-40999` | 业务冲突 | 业务规则冲突 |
| `50000-50099` | 系统异常 | 服务端内部错误 |
| `51000-51999` | 第三方异常 | 外部服务调用失败 |

### 3.2 完整错误码清单

#### 成功

| 错误码 | 中文消息 | 英文消息 |
|--------|----------|----------|
| 0 | 成功 | Success |

#### 40000-40099: 参数错误

| 错误码 | 中文消息 | 英文消息 |
|--------|----------|----------|
| 40000 | 参数无效：{detail} | Invalid parameter: {detail} |
| 40001 | 缺少必填参数：{field} | Missing required parameter: {field} |
| 40002 | 参数类型错误：{field} 应为 {expected} | Parameter type error: {field} should be {expected} |
| 40010 | 不支持的文件格式，仅支持：{allowed} | Unsupported file format, allowed: {allowed} |
| 40011 | 文件过大，最大允许 {max_size} | File too large, maximum allowed: {max_size} |
| 40012 | 无效的 URL 格式 | Invalid URL format |
| 40013 | 不支持的 YouTube 链接格式 | Unsupported YouTube URL format |

#### 40100-40199: 认证错误

| 错误码 | 中文消息 | 英文消息 |
|--------|----------|----------|
| 40100 | 未提供认证令牌 | Authentication token not provided |
| 40101 | 无效的认证令牌 | Invalid authentication token |
| 40102 | 认证令牌已过期，请重新登录 | Authentication token expired, please login again |

#### 40300-40399: 权限错误

| 错误码 | 中文消息 | 英文消息 |
|--------|----------|----------|
| 40300 | 没有权限执行此操作 | Permission denied |
| 40301 | 无权访问该资源 | No access to this resource |

#### 40400-40499: 资源不存在

| 错误码 | 中文消息 | 英文消息 |
|--------|----------|----------|
| 40400 | 用户不存在 | User not found |
| 40401 | 任务不存在 | Task not found |
| 40402 | 转写记录不存在 | Transcript not found |
| 40403 | 摘要不存在 | Summary not found |

#### 40900-40999: 业务冲突

| 错误码 | 中文消息 | 英文消息 |
|--------|----------|----------|
| 40900 | 相同内容的任务已存在 | Task with same content already exists |
| 40901 | 任务正在处理中，请勿重复提交 | Task is being processed, please do not resubmit |
| 40902 | 任务已完成，无法重新处理 | Task already completed, cannot reprocess |

#### 50000-50099: 系统异常

| 错误码 | 中文消息 | 英文消息 |
|--------|----------|----------|
| 50000 | 系统内部错误，请稍后重试 | Internal server error, please try again later |
| 50001 | 数据库服务异常 | Database service error |
| 50002 | 缓存服务异常 | Cache service error |
| 50003 | 文件处理异常 | File processing error |

#### 51000-51999: 第三方服务异常

| 错误码 | 中文消息 | 英文消息 |
|--------|----------|----------|
| 51000 | 语音识别服务暂时不可用 | Speech recognition service temporarily unavailable |
| 51001 | 语音识别服务超时 | Speech recognition service timeout |
| 51002 | 语音识别失败：{reason} | Speech recognition failed: {reason} |
| 51100 | AI 摘要服务暂时不可用 | AI summary service temporarily unavailable |
| 51101 | AI 摘要服务超时 | AI summary service timeout |
| 51102 | AI 摘要生成失败：{reason} | AI summary generation failed: {reason} |
| 51200 | 文件存储服务异常 | File storage service error |
| 51201 | 文件上传失败 | File upload failed |
| 51300 | YouTube 视频下载失败：{reason} | YouTube video download failed: {reason} |
| 51301 | YouTube 视频不可用或已被删除 | YouTube video unavailable or deleted |

---

## 4. 统计（Stats）

说明：
- 服务使用统计与任务统计均按当前用户维度过滤。
- 时间参数为 RFC3339；无时区时按 UTC 处理。
- `services/trend` 已下线：趋势口径对用户价值低，且易被偶发波动误导（暂不提供）。
- LLM 使用统计按任务的 `updated_at` 过滤（保证摘要重生成也能统计）。

### 4.1 服务使用概览

GET `/api/v1/stats/services/overview`

Query：
- `time_range` 可选：`today` | `week` | `month` | `all`
- `start_date` 可选：自定义起始时间（RFC3339）
- `end_date` 可选：自定义结束时间（RFC3339）

规则：
- `time_range` 优先生效；未传时默认查询近 30 天。

响应 `data`：
```json
{
  "time_range": { "start": "2026-01-01T00:00:00Z", "end": "2026-01-31T23:59:59Z" },
  "usage_by_service_type": [
    {
      "service_type": "asr",
      "provider": null,
      "call_count": 10,
      "success_count": 9,
      "failure_count": 1,
      "pending_count": 0,
      "processing_count": 0,
      "success_rate": 90.0,
      "failure_rate": 10.0,
      "avg_stage_seconds": 18.2,
      "median_stage_seconds": 16.8,
      "total_audio_duration_seconds": 1234.0
    }
  ],
  "usage_by_provider": [
    {
      "service_type": "asr",
      "provider": "tencent",
      "call_count": 6,
      "success_count": 5,
      "failure_count": 1,
      "pending_count": 0,
      "processing_count": 0,
      "success_rate": 83.3,
      "failure_rate": 16.7,
      "avg_stage_seconds": 19.1,
      "median_stage_seconds": 17.4,
      "total_audio_duration_seconds": 820.0
    }
  ],
  "asr_usage_by_provider": [
    {
      "service_type": "asr",
      "provider": "tencent",
      "call_count": 6,
      "success_count": 5,
      "failure_count": 1,
      "pending_count": 0,
      "processing_count": 0,
      "success_rate": 83.3,
      "failure_rate": 16.7,
      "avg_stage_seconds": 19.1,
      "median_stage_seconds": 17.4,
      "total_audio_duration_seconds": 820.0
    }
  ],
  "llm_usage_by_provider": [
    {
      "service_type": "llm",
      "provider": "doubao",
      "call_count": 6,
      "success_count": 5,
      "failure_count": 1,
      "pending_count": 0,
      "processing_count": 0,
      "success_rate": 83.3,
      "failure_rate": 16.7,
      "avg_stage_seconds": 6.2,
      "median_stage_seconds": 5.8,
      "total_audio_duration_seconds": 0.0
    }
  ]
}
```

### 4.2 任务概览

GET `/api/v1/stats/tasks/overview`

Query：
- `time_range` 可选：`today` | `week` | `month` | `all`
- `start_date` 可选：自定义起始时间（RFC3339）
- `end_date` 可选：自定义结束时间（RFC3339）

响应 `data`：
```json
{
  "time_range": { "start": "2026-01-01T00:00:00Z", "end": "2026-01-31T23:59:59Z" },
  "total_tasks": 20,
  "status_distribution": { "pending": 1, "processing": 2, "completed": 16, "failed": 1 },
  "success_rate": 80.0,
  "failure_rate": 5.0,
  "avg_processing_time_seconds": 35.2,
  "median_processing_time_seconds": 31.0,
  "processing_time_by_stage": {
    "resolve_youtube": 1.2,
    "download": 3.4,
    "transcode": 5.6,
    "upload_storage": 2.1,
    "transcribe": 20.5,
    "summarize": 4.2
  },
  "total_audio_duration_seconds": 3600,
  "total_audio_duration_formatted": "1h 0m 0s"
}
```

### 4.3 任务趋势（已下线）

说明：趋势口径对用户价值低，且易被偶发波动误导，暂不提供。

## 4. 国际化实现

### 4.1 后端目录结构

```
backend/app/
├── i18n/
│   ├── __init__.py
│   ├── codes.py         # 错误码常量
│   ├── zh.json          # 中文消息
│   └── en.json          # 英文消息
└── core/
    ├── i18n.py          # 国际化工具类
    ├── response.py      # 统一响应封装
    └── exceptions.py    # 业务异常定义
```

### 4.2 前端调用方式

```typescript
// 设置语言头
const api = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Accept-Language': locale // 'zh' | 'en'
  }
});

// 直接使用后端返回的 message（已国际化）
api.interceptors.response.use(
  (response) => {
    const { code, message, data, traceId } = response.data;
    if (code !== 0) {
      toast.error(message);
      console.error(`[${traceId}] Error ${code}: ${message}`);
      return Promise.reject({ code, message, traceId });
    }
    return data;
  }
);
```

### 4.3 规则

| 规则 | 说明 |
|------|------|
| 消息来源 | 统一由后端返回，前端不维护错误码映射 |
| 语言检测 | 后端读取 `Accept-Language` 头 |
| 默认语言 | 未识别或不支持时，默认返回中文 |
| 消息模板 | 支持 `{param}` 占位符，后端动态填充 |

---

## 5. 上传接口

### 5.1 获取预签名 URL

检查秒传，若需上传则返回预签名 URL。

**请求**：
```
POST /api/v1/upload/presign
```

**请求头**：
```
Authorization: Bearer <token>
Accept-Language: zh
```

**请求体**：
```json
{
  "filename": "meeting_2024.mp3",
  "content_type": "audio/mpeg",
  "size_bytes": 52428800,
  "content_hash": "a1b2c3d4e5f6..."
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| filename | string | ✅ | 原始文件名 |
| content_type | string | ✅ | MIME 类型 |
| size_bytes | integer | ✅ | 文件大小（字节） |
| content_hash | string | ✅ | SHA256 哈希值 |

**响应 - 秒传命中**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "exists": true,
    "task_id": "550e8400-e29b-41d4-a716-446655440000"
  },
  "traceId": "req_abc123"
}
```

**响应 - 需要上传**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "exists": false,
    "upload_url": "https://minio.example.com/bucket/uploads/2024/12/xxx.mp3?X-Amz-Signature=...",
    "file_key": "uploads/2024/12/xxx.mp3",
    "expires_in": 300
  },
  "traceId": "req_abc123"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| exists | boolean | 是否已存在（秒传） |
| task_id | string | 已存在任务的 ID（仅 exists=true 时） |
| upload_url | string | S3 预签名 PUT URL（仅 exists=false 时） |
| file_key | string | S3 Object Key（仅 exists=false 时） |
| expires_in | integer | URL 有效期（秒）（仅 exists=false 时） |

**错误响应**：
```json
{
  "code": 40010,
  "message": "不支持的文件格式，仅支持：mp3, mp4, wav, m4a, webm",
  "data": null,
  "traceId": "req_abc123"
}
```

---

## 6. 任务接口

### 6.1 创建任务

**请求**：
```
POST /api/v1/tasks
```

**请求体 - 上传文件**：
```json
{
  "title": "产品周会 2024-12-17",
  "source_type": "upload",
  "file_key": "uploads/2024/12/xxx.mp3",
  "content_hash": "a1b2c3d4e5f6...",
  "options": {
    "language": "auto",
    "enable_speaker_diarization": true,
    "summary_style": "meeting",
    "asr_variant": "file"
  }
}
```

**请求体 - YouTube**：
```json
{
  "title": "技术分享视频",
  "source_type": "youtube",
  "source_url": "https://youtube.com/watch?v=xxx",
  "options": {
    "language": "auto",
    "enable_speaker_diarization": false,
    "summary_style": "learning",
    "asr_variant": "file"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | string | ❌ | 任务标题（可自动生成） |
| source_type | string | ✅ | `upload` 或 `youtube` |
| file_key | string | 条件 | source_type=upload 时必填 |
| source_url | string | 条件 | source_type=youtube 时必填 |
| content_hash | string | ❌ | 文件哈希（用于幂等） |
| options.language | string | ❌ | `auto`/`zh`/`en`，默认 auto |
| options.enable_speaker_diarization | boolean | ❌ | 说话人分离，默认 true |
| options.summary_style | string | ❌ | `meeting`/`learning`/`interview`，默认 meeting |
| options.asr_variant | string | ❌ | ASR 业务类型，默认 `file` |

**说明**：
- `options.enable_speaker_diarization` 控制是否输出 `speaker_id`，默认 true。

**响应**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "pending",
    "progress": 0,
    "created_at": "2024-12-17T08:30:00Z"
  },
  "traceId": "req_abc123"
}
```

---

### 6.2 获取任务列表

**请求**：
```
GET /api/v1/tasks?page=1&page_size=20&status=all
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| page | integer | ❌ | 页码，默认 1 |
| page_size | integer | ❌ | 每页数量，默认 20，最大 100 |
| status | string | ❌ | 过滤状态：`all`/`pending`/`processing`/`completed`/`failed` |

**响应**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "items": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "title": "产品周会",
        "source_type": "upload",
        "status": "completed",
        "progress": 100,
        "duration_seconds": 2730,
        "created_at": "2024-12-17T08:30:00Z",
        "updated_at": "2024-12-17T09:15:00Z"
      }
    ],
    "total": 42,
    "page": 1,
    "page_size": 20
  },
  "traceId": "req_abc123"
}
```

---

### 6.3 获取任务详情

**请求**：
```
GET /api/v1/tasks/:id
```

**响应**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "产品周会",
    "source_type": "upload",
    "source_key": "uploads/2024/12/xxx.mp3",
    "audio_url": "https://bucket.cos.region.myqcloud.com/uploads/2024/12/xxx.mp3",
    "status": "completed",
    "progress": 100,
    "stage": "处理完成",
    "duration_seconds": 2730,
    "language": "zh",
    "created_at": "2024-12-17T08:30:00Z",
    "updated_at": "2024-12-17T09:15:00Z",
    "error_message": null
  },
  "traceId": "req_abc123"
}
```

---

### 6.4 获取转写内容

**请求**：
```
GET /api/v1/tasks/:id/transcript?page=1&page_size=50
```

**说明**：
- `words` 为可选词级时间戳；目前仅腾讯云在 `ResTextFormat>=1` 时返回，其它厂商通常为 `null`。
- 如需词级时间戳，请在腾讯 ASR 配置中将 `res_text_format` 设为 1/2/3（配置项 `TENCENT_ASR_RES_TEXT_FORMAT`）。
- `words[].start_time/end_time` 为**绝对时间（秒）**，可以直接用于词级高亮。
- `words` 可能包含标点或片段级的合并词；不保证与 `content` 一一对应，前端需容错。
- 当 `words` 为空时，回退为段级高亮（使用 `start_time/end_time`）。

**响应**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "items": [
      {
        "id": "tr_001",
        "speaker_id": "spk_0",
        "speaker_label": "张三",
        "content": "大家好，今天我们来讨论一下 Q4 的产品规划",
        "start_time": 0.0,
        "end_time": 4.5,
        "confidence": 0.95,
        "words": [
          { "word": "大家好", "start_time": 0.0, "end_time": 0.6, "confidence": 0.94 },
          { "word": "今天", "start_time": 0.6, "end_time": 1.1, "confidence": 0.95 }
        ],
        "is_edited": false
      },
      {
        "id": "tr_002",
        "speaker_id": "spk_1",
        "speaker_label": null,
        "content": "好的，我先汇报一下上周的进展",
        "start_time": 4.5,
        "end_time": 8.2,
        "confidence": 0.92,
        "words": null,
        "is_edited": false
      }
    ],
    "total": 156,
    "page": 1,
    "page_size": 50,
    "speakers": [
      { "id": "spk_0", "label": "张三" },
      { "id": "spk_1", "label": null }
    ]
  },
  "traceId": "req_abc123"
}
```

---

### 6.5 获取摘要

**请求**：
```
GET /api/v1/tasks/:id/summary
```

**响应**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "overview": {
      "content": "本次会议讨论了 Q4 产品规划，重点确定了用户增长目标和新功能优先级...",
      "model_used": "doubao-pro-32k",
      "created_at": "2024-12-17T09:15:00Z"
    },
    "key_points": {
      "content": "1. 用户增长目标调整为 50 万 MAU（05:20）\n2. 新功能优先级：A > B > C（12:30）\n3. 技术债务处理计划（25:00）",
      "model_used": "doubao-pro-32k",
      "created_at": "2024-12-17T09:15:00Z"
    },
    "action_items": {
      "content": "- [ ] 完成 PRD @张三 12/20\n- [ ] 技术评审 @李四 12/22\n- [ ] 设计稿交付 @王五 12/25",
      "model_used": "doubao-pro-32k",
      "created_at": "2024-12-17T09:15:00Z"
    }
  },
  "traceId": "req_abc123"
}
```

---

### 6.6 删除任务

**请求**：
```
DELETE /api/v1/tasks/:id
```

**响应**：
```json
{
  "code": 0,
  "message": "成功",
  "data": null,
  "traceId": "req_abc123"
}
```

---

### 6.7 ASR 额度查询与刷新

#### 6.7.1 查询额度

**说明**：
- 返回当前用户的“有效额度”（用户配置优先生效；未配置时回落到全局配置）。
- 系统会预置全局服务商列表（默认 `quota_seconds=0` 且 `status=exhausted`），前端可直接渲染。
- 同一 provider+variant 可同时存在多条额度（如 day + total），需全部可用该 provider+variant 才可用。
- variant 约定（本项目通用命名）：`file`（录音文件识别）、`file_fast`（极速/加速版录音识别）、`stream_async`（语音流异步识别）、`stream_realtime`（实时语音识别）。
- 未显式指定 `options.asr_variant` 时，录音文件转写优先使用 `file_fast` 额度，耗尽后回落到 `file`。

**前端渲染规则**：
- 先按 `provider + variant` 分组渲染（例如 tencent/file、aliyun/file）。
- 单个额度项显示：`剩余额度 = quota_seconds - used_seconds`（可显示小时：`remaining / 3600`）。
- provider 是否可用：该 provider+variant 下所有额度项均满足 `status=active` 且 `used_seconds < quota_seconds`。
- `window_type=total` 且 `window_start/window_end` 存在时，显示有效期；没有则显示“永久”。

**请求**：
```
GET /api/v1/asr/quotas
```

**字段说明（items[]）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| provider | string | 服务商标识（例如：tencent/aliyun/volcengine） |
| variant | string | 业务类型（默认 file；如 file/stream_async/stream_realtime） |
| window_type | string | 窗口类型：day / month / total |
| window_start | datetime | 窗口开始时间（UTC） |
| window_end | datetime | 窗口结束时间（UTC） |
| quota_seconds | number | 额度总秒数 |
| used_seconds | number | 已消耗秒数 |
| status | string | active 或 exhausted |

**响应**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "items": [
      {
        "provider": "aliyun",
        "variant": "file",
        "window_type": "month",
        "window_start": "2026-01-01T00:00:00Z",
        "window_end": "2026-01-31T23:59:59.999999Z",
        "quota_seconds": 10800,
        "used_seconds": 3600,
        "status": "active"
      }
    ]
  },
  "traceId": "req_abc123"
}
```

#### 6.7.2 刷新额度

**说明**：刷新当前用户额度。

**请求**：
```
POST /api/v1/asr/quotas/refresh
```

**请求体**：
```json
{
  "provider": "aliyun",
  "variant": "file",
  "window_type": "month",
  "quota_hours": 3,
  "reset": true
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| provider | string | 服务商标识 |
| variant | string | 业务类型（默认 file） |
| window_type | string | day / month / total（total 为一次性额度） |
| quota_seconds | number | 新额度总秒数（与 quota_hours 二选一） |
| quota_hours | number | 新额度总小时数（与 quota_seconds 二选一） |
| window_start | datetime | 额度生效起始时间（仅 total 可用） |
| window_end | datetime | 额度过期时间（仅 total 可用） |
| used_seconds | number | 已消耗额度（可选，剩余额度 = quota_seconds - used_seconds） |
| reset | boolean | 是否重置已用额度（充值后用 true） |

**示例：阿里云免费版（2 小时/日 + 到期时间）**：
```json
POST /api/v1/asr/quotas/refresh
{
  "provider": "aliyun",
  "variant": "file",
  "window_type": "day",
  "quota_hours": 2,
  "reset": true
}
```

```json
POST /api/v1/asr/quotas/refresh
{
  "provider": "aliyun",
  "variant": "file",
  "window_type": "total",
  "quota_hours": 1000000,
  "window_start": "2026-01-01T00:00:00Z",
  "window_end": "2026-04-01T00:00:00Z",
  "reset": true
}
```

**响应**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "item": {
      "provider": "aliyun",
      "variant": "file",
      "window_type": "month",
      "window_start": "2026-01-01T00:00:00Z",
      "window_end": "2026-01-31T23:59:59.999999Z",
      "quota_seconds": 10800,
      "used_seconds": 0,
      "status": "active"
    }
  },
  "traceId": "req_abc123"
}
```

---

#### 6.7.3 查询全局额度（管理员）

**请求**：
```
GET /api/v1/asr/quotas/global
```

**说明**：仅管理员可用（`ADMIN_EMAILS`），返回全局额度配置列表。

**响应**：同 6.7.1

---

#### 6.7.4 刷新全局额度（管理员）

**请求**：
```
POST /api/v1/asr/quotas/refresh-global
```

**说明**：仅管理员可用（`ADMIN_EMAILS`），刷新全局额度配置。

**请求体/响应**：同 6.7.2

---

## 7. 用户接口

### 7.1 获取当前用户

**请求**：
```
GET /api/v1/users/me
```

**响应**：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "user_001",
    "email": "user@example.com",
    "name": "张三",
    "image_url": "https://...",
    "locale": "zh",
    "timezone": "Asia/Shanghai",
    "created_at": "2024-12-01T00:00:00Z"
  },
  "traceId": "req_abc123"
}
```

---

## 8. WebSocket 接口

### 8.1 任务进度订阅

**连接**：
```
WS /ws/tasks/:id
```

**消息格式**：

WebSocket 消息也遵循统一格式：

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "type": "progress",
    "status": "transcribing",
    "stage": "ASR转写中",
    "progress": 45
  },
  "traceId": "req_abc123"
}
```

**进度更新**（type: progress）：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "type": "progress",
    "status": "transcribing",
    "stage": "ASR转写中",
    "progress": 45
  },
  "traceId": "req_abc123"
}
```

**完成通知**（type: completed）：
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "type": "completed",
    "status": "completed",
    "progress": 100,
    "result": {
      "duration_seconds": 2730,
      "transcript_count": 156,
      "summary_types": ["overview", "key_points", "action_items"]
    }
  },
  "traceId": "req_abc123"
}
```

**错误通知**（type: error）：
```json
{
  "code": 51000,
  "message": "语音识别服务暂时不可用",
  "data": {
    "type": "error",
    "status": "failed"
  },
  "traceId": "req_abc123"
}
```

---

## 9. 数据类型定义

### 9.1 枚举值

**source_type**：
| 值 | 说明 |
|----|------|
| `upload` | 本地上传 |
| `youtube` | YouTube 链接 |

**status**：
| 值 | 说明 |
|----|------|
| `pending` | 等待处理 |
| `extracting` | 音频提取中 |
| `transcribing` | ASR 转写中 |
| `summarizing` | 摘要生成中 |
| `completed` | 处理完成 |
| `failed` | 处理失败 |

**summary_style**：
| 值 | 说明 |
|----|------|
| `meeting` | 会议纪要风格 |
| `learning` | 学习笔记风格 |
| `interview` | 访谈整理风格 |

**language**：
| 值 | 说明 |
|----|------|
| `auto` | 自动检测 |
| `zh` | 中文 |
| `en` | 英文 |

### 9.2 业务限制

| 限制项 | 值 |
|--------|-----|
| 文件最大大小 | 500 MB |
| 支持的音频格式 | mp3, mp4, wav, m4a, webm |
| 音频最大时长 | 2 小时 |
| 标题最大长度 | 500 字符 |
| 分页最大数量 | 100 |
| 预签名 URL 有效期 | 5 分钟 |

---

## 10. HTTP 状态码使用

| HTTP 状态码 | 使用场景 |
|-------------|----------|
| 200 | 所有业务响应（成功和业务错误） |
| 401 | 未提供 Token（中间件拦截） |
| 404 | 路由不存在 |
| 500 | 未捕获的系统异常 |

**说明**：业务错误统一返回 HTTP 200，通过响应体中的 `code` 区分。这样前端可以统一处理响应，通过 `code === 0` 判断成功与否。
