# 前端对接文档 - API 契约

> 本文档提供后端 API 的完整契约规范，供前端开发使用

## 1. 基础信息

### 后端服务地址
```
开发环境: http://localhost:8000/api/v1
```

### 服务状态
✅ 后端已启动并运行
- FastAPI 服务: http://localhost:8000
- API 文档: http://localhost:8000/docs
- 数据库: PostgreSQL
- 缓存: Redis
- 存储: MinIO

### CORS 配置
已配置允许以下源访问：
- `http://localhost:3000`
- `http://127.0.0.1:3000`

---

## 2. 认证机制

### JWT 验证
后端**验证**前端签发的 JWT Token，不负责签发。

#### 请求头格式
```http
Authorization: Bearer <jwt_token>
```

#### JWT Payload 要求
```json
{
  "sub": "user_id_uuid",  // 必须：用户 ID (UUID 格式)
  "exp": 1234567890,      // 必须：过期时间戳
  "iat": 1234567890       // 可选：签发时间戳
}
```

#### JWT 配置（需与后端一致）
```env
JWT_SECRET=9NwhcmWIAS1kl8zt0jNU4TYcBgw5y0LG/jhESox3H+I=
JWT_ALGORITHM=HS256
```

#### 测试用户
后端已创建测试用户，前端直接使用即可：

**测试用户信息**:
- **User ID**: `550e8400-e29b-41d4-a716-446655440000`
- **Email**: `test@example.com`
- **Name**: `Test User`
- **Status**: `active`（可正常使用）
- **有效期**: 永久有效（除非手动删除）

**前端如何使用**:

1. **签发 JWT Token 时**，使用此 User ID 作为 `sub` 字段：
   ```json
   {
     "sub": "550e8400-e29b-41d4-a716-446655440000",
     "exp": 1735372800
   }
   ```

2. **调用需要认证的 API 时**，在请求头中携带此 Token：
   ```http
   Authorization: Bearer <your_jwt_token>
   ```

3. **所有任务都会关联到此用户**，可以正常进行增删改查操作。

**示例（Node.js）**:
```javascript
const jwt = require('jsonwebtoken');

// 签发 Token
const token = jwt.sign(
  {
    sub: '550e8400-e29b-41d4-a716-446655440000',  // 使用测试用户 ID
    exp: Math.floor(Date.now() / 1000) + 3600,   // 1 小时后过期
  },
  '9NwhcmWIAS1kl8zt0jNU4TYcBgw5y0LG/jhESox3H+I=',  // JWT Secret
  { algorithm: 'HS256' }
);

// 使用 Token 调用 API
fetch('http://localhost:8000/api/v1/tasks', {
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  }
});
```

**注意事项**:
- 开发环境使用此测试用户即可
- 生产环境需要实现真实的用户认证流程
- 测试用户数据可以随时清空（删除相关任务）

---

## 3. 统一响应格式

### 所有 API 响应格式（HTTP 200）

```typescript
interface ApiResponse<T = any> {
  code: number;        // 业务状态码（0 = 成功）
  message: string;     // 已国际化的消息
  data: T | null;      // 业务数据
  traceId: string;     // 请求追踪 ID
}
```

### 成功响应示例
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "pending"
  },
  "traceId": "a1b2c3d4"
}
```

### 错误响应示例
```json
{
  "code": 40401,
  "message": "任务不存在",
  "data": null,
  "traceId": "a1b2c3d4"
}
```

### 国际化
通过 `Accept-Language` 请求头控制错误消息语言：
- `zh`: 中文（默认）
- `en`: 英文

### 错误码范围

| 范围 | 说明 | 常见错误 |
|------|------|----------|
| 0 | 成功 | - |
| 40000-40099 | 参数错误 | 40010: 不支持的文件格式<br>40011: 文件大小超过限制 |
| 40100-40199 | 认证错误 | 40100: Token 未提供<br>40101: Token 无效<br>40102: Token 过期 |
| 40300-40399 | 权限错误 | 40301: 无权访问此资源 |
| 40400-40499 | 资源不存在 | 40400: 用户不存在<br>40401: 任务不存在 |
| 40900-40999 | 业务冲突 | 40900: 任务已存在（秒传） |
| 50000-50099 | 系统错误 | 50000: 内部服务器错误 |
| 51000-51999 | 第三方服务 | 51000: ASR 服务不可用<br>51100: LLM 服务不可用 |

---

## 4. API 端点

### 4.1 健康检查

```http
GET /api/v1/health
```

**认证**: 不需要

**响应**:
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "status": "ok"
  },
  "traceId": "..."
}
```

---

### 4.2 文件上传预签名

```http
POST /api/v1/upload/presign
Authorization: Bearer <token>
Content-Type: application/json
```

**认证**: 需要

**请求体**:
```json
{
  "filename": "meeting.mp3",
  "content_type": "audio/mpeg",
  "size_bytes": 10485760,
  "content_hash": "sha256_hash_of_file"
}
```

**字段说明**:
- `filename`: 必须，文件名
- `content_type`: 必须，MIME 类型
- `size_bytes`: 必须，文件大小（字节）
- `content_hash`: 必须，文件 SHA256 hash（用于秒传检测）

**响应（新文件）**:
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "exists": false,
    "upload_url": "http://192.168.1.4:9000/...",
    "file_key": "uploads/2024/12/abc_meeting.mp3",
    "expires_in": 300
  },
  "traceId": "..."
}
```

**响应（文件已存在，秒传）**:
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "exists": true,
    "task_id": "550e8400-e29b-41d4-a716-446655440000"
  },
  "traceId": "..."
}
```

**错误码**:
- `40010`: 不支持的文件格式
- `40011`: 文件大小超过限制（最大 500MB）

**支持的文件格式**: mp3, mp4, wav, m4a, webm

**注意事项**:
- 预签名 URL 有效期 5 分钟
- 使用 **PUT** 方法上传文件到 `upload_url`
- 上传时设置正确的 `Content-Type` 请求头

---

### 4.3 创建任务

```http
POST /api/v1/tasks
Authorization: Bearer <token>
Content-Type: application/json
```

**认证**: 需要

**请求体**:
```json
{
  "title": "产品周会",
  "source_type": "upload",
  "file_key": "uploads/2024/12/abc_meeting.mp3",
  "content_hash": "sha256_hash",
  "options": {
    "language": "auto",
    "enable_speaker_diarization": true,
    "summary_style": "meeting"
  }
}
```

**字段说明**:
- `title`: 可选，任务标题
- `source_type`: 必须，`upload` 或 `youtube`
- `file_key`: upload 类型时必须，从预签名接口获取
- `source_url`: youtube 类型时必须，YouTube URL
- `content_hash`: 可选，用于秒传检测
- `options.language`: 可选，`auto`（自动检测）、`zh`、`en`，默认 `auto`
- `options.enable_speaker_diarization`: 可选，是否启用说话人分离，默认 `true`
- `options.summary_style`: 可选，摘要风格：`meeting`、`learning`、`interview`，默认 `meeting`

**响应**:
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "pending",
    "progress": 0,
    "created_at": "2024-12-27T12:00:00Z"
  },
  "traceId": "..."
}
```

---

### 4.4 获取任务列表

```http
GET /api/v1/tasks?page=1&page_size=20&status=all
Authorization: Bearer <token>
```

**认证**: 需要

**查询参数**:
- `page`: 可选，页码，默认 1
- `page_size`: 可选，每页数量，默认 20，最大 100
- `status`: 可选，过滤状态：`all`、`pending`、`processing`、`completed`、`failed`，默认 `all`

**响应**:
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
        "duration_seconds": 3600,
        "created_at": "2024-12-27T12:00:00Z",
        "updated_at": "2024-12-27T12:30:00Z"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 20
  },
  "traceId": "..."
}
```

**任务状态值**:
- `pending`: 等待处理
- `extracting`: 音频提取中（0-20%）
- `transcribing`: 转写中（20-70%）
- `summarizing`: 摘要生成中（70-99%）
- `completed`: 已完成（100%）
- `failed`: 失败

---

### 4.5 获取任务详情

```http
GET /api/v1/tasks/{task_id}
Authorization: Bearer <token>
```

**认证**: 需要

**路径参数**:
- `task_id`: 任务 ID（UUID 格式）

**响应**:
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "产品周会",
    "source_type": "upload",
    "source_key": "uploads/2024/12/abc_meeting.mp3",
    "status": "completed",
    "progress": 100,
    "stage": "处理完成",
    "duration_seconds": 3600,
    "language": "zh",
    "created_at": "2024-12-27T12:00:00Z",
    "updated_at": "2024-12-27T12:30:00Z",
    "error_message": null
  },
  "traceId": "..."
}
```

**错误码**:
- `40401`: 任务不存在
- `40301`: 无权访问此任务（不属于当前用户）

---

### 4.6 删除任务

```http
DELETE /api/v1/tasks/{task_id}
Authorization: Bearer <token>
```

**认证**: 需要

**路径参数**:
- `task_id`: 任务 ID（UUID 格式）

**响应**:
```json
{
  "code": 0,
  "message": "成功",
  "data": null,
  "traceId": "..."
}
```

**说明**: 软删除，数据不会真正删除，只是标记为已删除状态

**错误码**:
- `40401`: 任务不存在
- `40301`: 无权删除此任务

---

### 4.7 获取转写结果

```http
GET /api/v1/transcripts/{task_id}
Authorization: Bearer <token>
```

**认证**: 需要

**路径参数**:
- `task_id`: 任务 ID（UUID 格式）

**响应**:
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "total": 150,
    "items": [
      {
        "id": "660e8400-e29b-41d4-a716-446655440001",
        "speaker_id": "spk_1",
        "speaker_label": "张三",
        "content": "大家好，今天我们讨论项目进展。",
        "start_time": 0.0,
        "end_time": 3.5,
        "confidence": 0.95,
        "sequence": 0,
        "is_edited": false,
        "original_content": null,
        "created_at": "2024-12-27T12:15:00Z",
        "updated_at": "2024-12-27T12:15:00Z"
      }
    ]
  },
  "traceId": "..."
}
```

**字段说明**:
- `speaker_id`: ASR 返回的说话人 ID
- `speaker_label`: 用户自定义的说话人名称（可编辑）
- `content`: 转写文本内容
- `start_time`: 开始时间（秒）
- `end_time`: 结束时间（秒）
- `confidence`: ASR 置信度（0-1）
- `sequence`: 句子顺序
- `is_edited`: 是否被用户编辑过
- `original_content`: 如果被编辑过，这里是原始内容

**错误码**:
- `40401`: 任务不存在
- `40301`: 无权访问此任务

---

### 4.8 获取摘要结果

```http
GET /api/v1/summaries/{task_id}
Authorization: Bearer <token>
```

**认证**: 需要

**路径参数**:
- `task_id`: 任务 ID（UUID 格式）

**响应**:
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "total": 3,
    "items": [
      {
        "id": "770e8400-e29b-41d4-a716-446655440001",
        "summary_type": "overview",
        "version": 1,
        "is_active": true,
        "content": "本次会议主要讨论了项目进展、技术难点和下一步计划...",
        "model_used": "doubao-1.5-pro-32k-250115",
        "prompt_version": "v1.0",
        "token_count": 1500,
        "created_at": "2024-12-27T12:25:00Z"
      }
    ]
  },
  "traceId": "..."
}
```

**摘要类型**:
- `overview`: 全局概览
- `key_points`: 关键要点
- `action_items`: 行动项

**错误码**:
- `40401`: 任务不存在
- `40301`: 无权访问此任务

---

### 4.9 WebSocket 实时进度

```
ws://localhost:8000/api/v1/ws/tasks/{task_id}
```

**认证**: 需要

**连接要求**:
- 在连接头中传递 `Authorization: Bearer <token>`
- 可选：`Accept-Language: zh` 或 `en`

**路径参数**:
- `task_id`: 任务 ID（UUID 格式）

**服务器推送消息格式**:

1. **进度更新消息**（任务处理中）:
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "type": "progress",
    "status": "transcribing",
    "stage": "正在转写音频...",
    "progress": 45,
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "request_id": "req_abc123"
  },
  "traceId": "req_abc123"
}
```

2. **完成消息**（任务完成）:
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "type": "completed",
    "status": "completed",
    "stage": "处理完成",
    "progress": 100,
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "request_id": "req_abc123"
  },
  "traceId": "req_abc123"
}
```

3. **错误消息**（任务失败）:
```json
{
  "code": 51000,
  "message": "ASR 服务异常",
  "data": {
    "type": "error",
    "status": "failed",
    "task_id": "550e8400-e29b-41d4-a716-446655440000"
  },
  "traceId": "req_abc123"
}
```

**字段说明**:
- `data.type`: 消息类型，可选值：`progress`（进度更新）、`completed`（任务完成）、`error`（任务失败）
- `data.status`: 任务状态，参见任务状态值列表
- `data.stage`: 当前阶段描述（中文）
- `data.progress`: 进度百分比 0-100
- `code`: 业务状态码，成功为 0，失败为错误码

**连接关闭码**:
- `1008`: 认证失败或无权访问任务

---

## 5. TypeScript 类型定义

```typescript
// ========== 通用类型 ==========

interface ApiResponse<T = any> {
  code: number;
  message: string;
  data: T | null;
  traceId: string;
}

interface PageResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// ========== 上传相关 ==========

interface UploadPresignRequest {
  filename: string;
  content_type: string;
  size_bytes: number;
  content_hash: string;
}

type UploadPresignResponse =
  | {
      exists: false;
      upload_url: string;
      file_key: string;
      expires_in: number;
    }
  | {
      exists: true;
      task_id: string;
    };

// ========== 任务相关 ==========

interface TaskOptions {
  language?: 'auto' | 'zh' | 'en';
  enable_speaker_diarization?: boolean;
  summary_style?: 'meeting' | 'learning' | 'interview';
}

interface TaskCreateRequest {
  title?: string;
  source_type: 'upload' | 'youtube';
  file_key?: string;
  source_url?: string;
  content_hash?: string;
  options?: TaskOptions;
}

interface TaskCreateResponse {
  id: string;
  status: string;
  progress: number;
  created_at: string;
}

type TaskStatus =
  | 'pending'
  | 'extracting'
  | 'transcribing'
  | 'summarizing'
  | 'completed'
  | 'failed';

interface TaskListItem {
  id: string;
  title: string | null;
  source_type: string;
  status: TaskStatus;
  progress: number;
  duration_seconds: number | null;
  created_at: string;
  updated_at: string;
}

interface TaskDetail {
  id: string;
  title: string | null;
  source_type: string;
  source_key: string | null;
  status: TaskStatus;
  progress: number;
  stage: string | null;
  duration_seconds: number | null;
  language: string | null;
  created_at: string;
  updated_at: string;
  error_message: string | null;
}

// ========== 转写相关 ==========

interface TranscriptItem {
  id: string;
  speaker_id: string | null;
  speaker_label: string | null;
  content: string;
  start_time: number;
  end_time: number;
  confidence: number | null;
  sequence: number;
  is_edited: boolean;
  original_content: string | null;
  created_at: string;
  updated_at: string;
}

interface TranscriptListResponse {
  task_id: string;
  total: number;
  items: TranscriptItem[];
}

// ========== 摘要相关 ==========

type SummaryType = 'overview' | 'key_points' | 'action_items';

interface SummaryItem {
  id: string;
  summary_type: SummaryType;
  version: number;
  is_active: boolean;
  content: string;
  model_used: string | null;
  prompt_version: string | null;
  token_count: number | null;
  created_at: string;
}

interface SummaryListResponse {
  task_id: string;
  total: number;
  items: SummaryItem[];
}

// ========== WebSocket 消息 ==========

type WebSocketMessageType = 'progress' | 'completed' | 'error';

interface WebSocketProgressMessage {
  code: number;
  message: string;
  data: {
    type: WebSocketMessageType;
    status: TaskStatus;
    stage: string;
    progress: number;
    task_id: string;
    request_id: string;
  };
  traceId: string;
}

interface WebSocketErrorMessage {
  code: number;
  message: string;
  data: {
    type: 'error';
    status: 'failed';
    task_id: string;
  };
  traceId: string;
}

type WebSocketMessage = WebSocketProgressMessage | WebSocketErrorMessage;
```

---

## 6. 文件上传流程

### 流程说明

1. **前端计算文件 SHA256 hash**
   - 用于秒传检测和文件完整性校验

2. **调用预签名接口**
   ```
   POST /api/v1/upload/presign
   ```
   - 提供文件名、大小、hash 等信息

3. **根据响应处理**
   - 如果 `exists: true`：文件已存在（秒传），直接使用返回的 `task_id`
   - 如果 `exists: false`：文件不存在，继续上传流程

4. **上传文件到 MinIO**
   - 使用 **PUT** 方法
   - 上传到返回的 `upload_url`
   - 设置正确的 `Content-Type` 请求头
   - 注意：5 分钟内完成上传

5. **创建任务**
   ```
   POST /api/v1/tasks
   ```
   - 使用步骤 3 返回的 `file_key`

6. **监听任务进度**
   ```
   WebSocket: ws://localhost:8000/api/v1/ws/tasks/{task_id}
   ```
   - 接收实时状态更新
   - 更新前端进度显示

### 流程图

```
用户选择文件
    ↓
计算 SHA256
    ↓
POST /upload/presign
    ↓
    ├─→ exists: true ─→ 直接使用 task_id
    │
    └─→ exists: false
            ↓
        PUT 文件到 upload_url
            ↓
        POST /tasks (创建任务)
            ↓
        WebSocket 监听进度
```

---

## 7. 常见问题

### Q1: 为什么返回 40400（用户不存在）？
**A**: JWT 中的 `sub` 字段对应的用户在数据库中不存在。联调时请使用测试用户 ID：`550e8400-e29b-41d4-a716-446655440000`

### Q2: 预签名 URL 上传失败？
**A**:
- 确认使用 **PUT** 方法（不是 POST）
- 确认 URL 未过期（5 分钟有效期）
- 确认设置了正确的 `Content-Type` 请求头
- 确认 MinIO 地址可访问（http://192.168.1.4:9000）

### Q3: WebSocket 连接失败？
**A**:
- 确认 URL 使用 `ws://`（不是 `wss://`）
- 确认在连接头中传递了 `Authorization: Bearer <token>`
- 确认任务属于当前用户

### Q4: 如何处理国际化？
**A**:
- 发送请求时设置 `Accept-Language: zh` 或 `en`
- 后端会根据此头返回对应语言的错误消息
- 前端直接显示响应中的 `message` 字段

### Q5: 任务进度如何更新？
**A**:
- 通过 WebSocket 接收实时进度推送
- 或者定期轮询 `GET /api/v1/tasks/{task_id}` 接口
- 推荐使用 WebSocket 方式

### Q6: 错误如何处理？
**A**:
- HTTP 状态码始终为 200
- 通过响应中的 `code` 字段判断业务是否成功
- `code === 0` 表示成功
- `code !== 0` 表示失败，显示 `message` 给用户
- 使用 `traceId` 用于问题排查

---

## 8. 联调检查清单

- [ ] 能访问 http://localhost:8000/api/v1/health
- [ ] JWT 签发配置正确（secret、algorithm）
- [ ] 能使用测试用户 ID 成功调用需要认证的接口
- [ ] 文件上传流程完整（预签名 → 上传 → 创建任务）
- [ ] WebSocket 能接收实时进度
- [ ] 错误码能正确处理和显示
- [ ] 国际化切换正常

---

## 9. 调试工具

### API 文档
访问 http://localhost:8000/docs 使用 Swagger UI 测试 API

### 查看日志
- FastAPI 日志：终端 1
- Celery Worker 日志：终端 2
- 响应中的 `traceId` 可用于定位问题

---

祝联调顺利！🚀
