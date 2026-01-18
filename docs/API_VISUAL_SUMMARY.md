# 可视化摘要 API 对接文档

## 功能概述

可视化摘要功能（v1.3+）允许将音频转写内容生成为结构化的可视化图表，包括：
- **思维导图 (mindmap)**: 层次化展示核心概念和关系
- **时间轴 (timeline)**: 按时间顺序展示事件流程
- **流程图 (flowchart)**: 展示步骤和决策流程

所有可视化内容使用 **Mermaid** 语法生成，可在前端使用 Mermaid.js 直接渲染。

---

## 背景说明

### 1. 生成流程

```
用户请求 → 后端异步任务 → LLM 生成 Mermaid 代码 → 保存到数据库 → 返回结果
```

- **异步处理**: 生成任务通过 Celery 异步执行，通常耗时 10-30 秒
- **数据存储**: 生成的 Mermaid 代码存储在 `summaries` 表的 `visual_content` 字段
- **图片渲染**: 服务端图片渲染功能当前不可用，建议前端使用 Mermaid.js 客户端渲染

### 2. 适用场景

| 可视化类型 | 推荐内容类型 | 说明 |
|----------|------------|------|
| mindmap | lecture, podcast, video | 展示知识结构、核心概念关系 |
| timeline | meeting, lecture | 按时间顺序展示事件、议程 |
| flowchart | meeting, video | 展示流程、决策路径 |

### 3. 前置条件

- 任务必须已完成转写（status = "completed"）
- 任务至少有一个文本摘要（否则可能生成质量较低）

---

## API 端点

### 1. 生成可视化摘要

**POST** `/api/v1/summaries/{task_id}/visual`

生成指定类型的可视化摘要。

#### 请求参数

**Path Parameters:**
- `task_id` (string, required): 任务 ID (UUID)

**Request Body:**
```json
{
  "visual_type": "mindmap",
  "content_style": null,
  "generate_image": false,
  "image_format": "png",
  "provider": "deepseek",
  "model_id": null
}
```

| 字段 | 类型 | 必需 | 说明 |
|-----|------|------|------|
| visual_type | string | ✅ | 可视化类型：mindmap / timeline / flowchart |
| content_style | string | ❌ | 内容风格：meeting / lecture / podcast / video / general<br>为 null 时自动检测 |
| generate_image | boolean | ❌ | 是否生成图片（默认 false，推荐使用前端渲染） |
| image_format | string | ❌ | 图片格式：png / svg（当 generate_image=true 时有效） |
| provider | string | ❌ | LLM 提供商：deepseek / qwen / moonshot / doubao<br>为 null 时使用系统默认 |
| model_id | string | ❌ | 具体模型 ID，为 null 时使用 provider 的默认模型 |

#### 响应示例

**成功响应 (202 Accepted):**
```json
{
  "code": 0,
  "message": "可视化摘要生成任务已提交",
  "data": {
    "task_id": "95b12da9-c24e-41ff-82bd-737941594a4e",
    "visual_type": "mindmap",
    "status": "processing"
  },
  "traceId": "abc123..."
}
```

**错误响应 (400 Bad Request):**
```json
{
  "code": 40001,
  "message": "任务尚未完成转写，无法生成可视化摘要",
  "data": null,
  "traceId": "abc123..."
}
```

**错误响应 (404 Not Found):**
```json
{
  "code": 40401,
  "message": "任务不存在或无权访问",
  "data": null,
  "traceId": "abc123..."
}
```

---

### 2. 获取可视化摘要

**GET** `/api/v1/summaries/{task_id}/visual/{visual_type}`

获取指定类型的可视化摘要内容。

#### 请求参数

**Path Parameters:**
- `task_id` (string, required): 任务 ID (UUID)
- `visual_type` (string, required): 可视化类型 (mindmap / timeline / flowchart)

#### 响应示例

**成功响应 (200 OK):**
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "62ff5e85-3ed6-44f7-8286-46cd2779943d",
    "task_id": "95b12da9-c24e-41ff-82bd-737941594a4e",
    "summary_type": "visual_mindmap",
    "visual_type": "mindmap",
    "format": "mermaid",
    "content": "mindmap\n  root((英语如何成为全球通用语))\n    引言：从泰坦尼克号菜单说起\n      1912年：法语代表上流社会\n      100年后：英语成为默认选项\n      核心问题：为何是英语？\n    语言的经济学本质：交易技术\n      通用语降低交易成本\n        避免翻译损耗与误解\n        提高协作效率\n      数据佐证：共同语言提升44%贸易流量\n      世界呼唤通用协议以省去"沟通税"\n    英语崛起的三大历史阶段\n      第一阶段：物理网络铺设 (17-19世纪)\n        英国殖民与贸易扩张\n        英语成为全球商业"代码"\n          标准化贸易体系\n          语法简单，门槛低\n      第二阶段：制度与权力锁定 (1914-1945)\n        一战：军事协作的催化剂\n          同盟国语言混乱效率低\n          协约国共享英语操作系统\n        美国参战：硬通货带来刚需\n          物资、贷款与英语绑定\n        巴黎和会 (1919)：法语让位于英语\n      第三阶段：数字时代的网络效应 (1980-至今)\n        互联网基础协议用英语编写\n        全球开发者社区以英语为默认语言\n        AI 训练数据 92% 为英文内容\n    英语霸权的网络效应\n      先发优势形成路径依赖\n        已掌握英语的人口基数大\n        新用户倾向加入主流网络\n      反馈循环强化垄断地位\n        更多资源投入英语内容创作\n        非英语内容边缘化\n    未来展望与思考\n      AI 翻译能否打破英语垄断？\n        技术进步 vs 网络效应\n        即时翻译降低语言壁垒\n      新通用语的可能性\n        需要更强大的网络效应\n        或全新的协作范式\n      关键启示：语言的价值在于网络规模",
    "image_url": null,
    "model_used": "deepseek-chat",
    "created_at": "2026-01-18T08:14:24.180000",
    "updated_at": "2026-01-18T08:14:24.180000"
  },
  "traceId": "abc123..."
}
```

**错误响应 (404 Not Found):**
```json
{
  "code": 40402,
  "message": "未找到该类型的可视化摘要",
  "data": null,
  "traceId": "abc123..."
}
```

---

### 3. 获取任务的所有摘要

**GET** `/api/v1/summaries/{task_id}`

获取任务的所有摘要（包括文本摘要和可视化摘要）。

#### 请求参数

**Path Parameters:**
- `task_id` (string, required): 任务 ID (UUID)

#### 响应示例

**成功响应 (200 OK):**
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "task_id": "95b12da9-c24e-41ff-82bd-737941594a4e",
    "total": 4,
    "items": [
      {
        "id": "summary-1",
        "summary_type": "overview",
        "content": "# 会议概览\n\n## 会议速览\n...",
        "visual_format": null,
        "image_url": null
      },
      {
        "id": "summary-2",
        "summary_type": "key_points",
        "content": "# 会议关键要点\n\n## 【决策与共识】\n...",
        "visual_format": null,
        "image_url": null
      },
      {
        "id": "summary-3",
        "summary_type": "action_items",
        "content": "# 待办事项与行动计划\n\n## 【待办事项】\n...",
        "visual_format": null,
        "image_url": null
      },
      {
        "id": "62ff5e85-3ed6-44f7-8286-46cd2779943d",
        "summary_type": "visual_mindmap",
        "content": "mindmap\n  root((英语如何成为全球通用语))\n    引言：从泰坦尼克号菜单说起\n...",
        "visual_format": "mermaid",
        "image_url": null
      }
    ]
  },
  "traceId": "abc123..."
}
```

---

## 数据模型

### Summary 字段说明

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | string (UUID) | 摘要 ID |
| task_id | string (UUID) | 关联的任务 ID |
| summary_type | string | 摘要类型：<br>- 文本: overview / key_points / action_items<br>- 可视化: visual_mindmap / visual_timeline / visual_flowchart |
| content | string | 摘要内容（文本摘要为 Markdown，可视化摘要为空） |
| visual_format | string | 可视化格式（仅可视化摘要）：mermaid |
| visual_content | string | Mermaid 语法代码（仅在 GET visual/{type} 时返回为 content 字段） |
| image_key | string | 图片存储路径（当前为 null） |
| image_url | string | 图片访问 URL（当前为 null） |
| model_used | string | 使用的 LLM 模型，如 "deepseek-chat" |
| created_at | datetime | 创建时间 (ISO 8601) |
| updated_at | datetime | 更新时间 (ISO 8601) |

---

## Mermaid 渲染指南

### 前端集成 Mermaid.js

可视化摘要使用 Mermaid 语法，需要前端渲染。

#### 1. 安装依赖
```bash
npm install mermaid
# 或
yarn add mermaid
```

#### 2. 基本用法

**原生 HTML:**
```html
<!DOCTYPE html>
<html>
<head>
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
    mermaid.initialize({ startOnLoad: true });
  </script>
</head>
<body>
  <pre class="mermaid">
mindmap
  root((主题))
    分支1
    分支2
  </pre>
</body>
</html>
```

**React 示例:**
```jsx
import { useEffect, useRef } from 'react';
import mermaid from 'mermaid';

function MermaidDiagram({ content }) {
  const ref = useRef(null);

  useEffect(() => {
    mermaid.initialize({ startOnLoad: false });
    if (ref.current) {
      mermaid.contentLoaded();
    }
  }, [content]);

  return <pre className="mermaid" ref={ref}>{content}</pre>;
}
```

**Vue 示例:**
```vue
<template>
  <pre class="mermaid" ref="mermaidRef">{{ content }}</pre>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue';
import mermaid from 'mermaid';

const props = defineProps(['content']);
const mermaidRef = ref(null);

onMounted(() => {
  mermaid.initialize({ startOnLoad: true });
});

watch(() => props.content, () => {
  mermaid.contentLoaded();
});
</script>
```

#### 3. 配置选项

```javascript
mermaid.initialize({
  startOnLoad: true,
  theme: 'neutral',  // default, dark, forest, neutral
  themeVariables: {
    primaryColor: '#ff0000',
    primaryTextColor: '#000000',
  },
  securityLevel: 'loose',
  fontFamily: 'Arial, sans-serif',
});
```

---

## 错误码参考

| 错误码 | 说明 | 处理建议 |
|-------|------|---------|
| 40001 | 参数错误 | 检查请求参数格式 |
| 40101 | 未认证 | 需要登录或刷新 token |
| 40301 | 无权限 | 任务不属于当前用户 |
| 40401 | 任务不存在 | 检查 task_id 是否正确 |
| 40402 | 可视化摘要不存在 | 需要先调用生成接口 |
| 50001 | 服务器内部错误 | 联系技术支持 |
| 51001 | LLM 服务错误 | LLM API 调用失败，稍后重试 |

---

## 最佳实践

### 1. 生成流程推荐

```
1. 检查任务状态是否为 "completed"
2. 调用 POST /visual 接口提交生成任务
3. 显示加载状态（"生成中..."）
4. 轮询或等待 15-30 秒后调用 GET /visual/{type} 获取结果
5. 使用 Mermaid.js 渲染显示
```

### 2. 性能优化

- **缓存结果**: 可视化摘要内容不会变化，可在前端缓存
- **按需生成**: 只在用户点击"查看思维导图"时才调用生成接口
- **异步渲染**: Mermaid 渲染可能较慢，建议在 Web Worker 中处理

### 3. 用户体验建议

- **生成前提示**: 告知用户生成需要 10-30 秒
- **提供预览**: 在生成完成前显示示例图或占位符
- **支持重新生成**: 允许用户使用不同的 LLM provider 重新生成
- **导出功能**: 提供下载 SVG/PNG 或复制 Mermaid 代码功能

### 4. 错误处理

```javascript
try {
  const response = await fetch(`/api/v1/summaries/${taskId}/visual`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ visual_type: 'mindmap' })
  });

  const result = await response.json();

  if (result.code !== 0) {
    // 处理业务错误
    switch (result.code) {
      case 40001:
        alert('任务尚未完成，请稍后再试');
        break;
      case 40402:
        alert('可视化摘要不存在，正在生成中...');
        break;
      default:
        alert(result.message);
    }
  }
} catch (error) {
  // 处理网络错误
  console.error('请求失败:', error);
}
```

---

## 技术限制

### 当前版本 (v1.3)

✅ **已支持:**
- Mermaid 语法生成（mindmap, timeline, flowchart）
- 多 LLM provider 支持（DeepSeek, Qwen, Moonshot, Doubao）
- 内容风格自动检测
- 异步生成任务

❌ **暂不支持:**
- 服务端图片渲染（ARM Mac Docker 环境限制）
- 图片直接下载（需要前端使用 Mermaid.js 导出）
- 实时流式生成
- 自定义 Mermaid 主题配置

### 兼容性

- **后端**: Python 3.11+, FastAPI, PostgreSQL 16+
- **前端**: 需支持 ES6+, 建议使用 Mermaid.js 10.0+
- **浏览器**: Chrome 90+, Firefox 88+, Safari 14+, Edge 90+

---

## FAQ

**Q: 可视化摘要生成失败怎么办？**
A: 检查任务是否已完成转写，查看错误码，如果是 LLM 服务错误（51001），可以尝试更换 provider 重新生成。

**Q: 为什么不提供图片 URL？**
A: 当前版本服务端图片渲染在 Docker 环境有兼容性问题，推荐使用前端 Mermaid.js 渲染，性能更好且支持交互。

**Q: 支持自定义可视化样式吗？**
A: 当前 Mermaid 代码使用默认样式，前端可以通过 Mermaid.js 的 `themeVariables` 配置自定义颜色和字体。

**Q: 一个任务可以生成多个可视化摘要吗？**
A: 可以，每种类型（mindmap/timeline/flowchart）可以单独生成，互不影响。

**Q: 可视化摘要支持多语言吗？**
A: 当前仅支持中文（zh-CN），未来版本将支持英文（en-US）。

---

## 更新日志

### v1.3.0 (2026-01-18)
- ✨ 新增可视化摘要功能
- ✨ 支持思维导图 (mindmap)、时间轴 (timeline)、流程图 (flowchart)
- ✨ 集成 DeepSeek, Qwen, Moonshot, Doubao LLM
- 🐛 修复 Transcript speaker_id 字段错误
- 🐛 修复 model_params 配置读取问题

### v1.2.0 (2026-01-17)
- ✨ 新增 RAG 功能
- ✨ 新增多模型对比功能

---

## 联系方式

如有技术问题或功能建议，请联系开发团队或提交 Issue。
