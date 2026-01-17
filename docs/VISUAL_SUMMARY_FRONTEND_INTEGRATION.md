# Visual Summary Frontend Integration Guide

## 文档概述

本文档面向前端开发人员，详细说明如何对接 **可视化摘要生成功能（Visual Summary v1.3）**。

**功能简介**:
在原有文本摘要的基础上，新增了可视化图表生成能力，支持将音视频转录内容自动生成为：
- 📊 **思维导图 (Mindmap)**: 层级化的概念关系图
- 📅 **时间轴 (Timeline)**: 时间序列或进程图
- 🔀 **流程图 (Flowchart)**: 决策流程和步骤图

---

## 目录

1. [背景与架构](#1-背景与架构)
2. [API 接口详解](#2-api-接口详解)
3. [前端集成方案](#3-前端集成方案)
4. [Mermaid.js 使用指南](#4-mermaidjs-使用指南)
5. [完整示例代码](#5-完整示例代码)
6. [错误处理](#6-错误处理)
7. [性能优化](#7-性能优化)
8. [测试与调试](#8-测试与调试)

---

## 1. 背景与架构

### 1.1 产品背景

**现有功能**:
用户上传音视频后，系统生成 3 种文本摘要：
- `overview`: 内容概述
- `key_points`: 关键要点
- `action_items`: 待办事项

**新增功能 (v1.3)**:
在文本摘要基础上，新增可视化摘要，以图表形式直观展示内容结构：
- **适用场景**: 讲座课程（思维导图）、会议记录（时间轴/流程图）、播客访谈（思维导图）
- **生成方式**: 后端使用 LLM 分析转录文本，生成 Mermaid 语法，并可选渲染为 PNG/SVG 图片

### 1.2 技术架构

```
┌─────────────┐
│  前端 UI    │
└──────┬──────┘
       │ POST /summaries/{task_id}/visual
       │ (请求生成可视化摘要)
       ▼
┌─────────────────┐
│  FastAPI 后端   │ ──► Celery 异步任务队列
└─────────────────┘
       │
       ▼
┌─────────────────┐
│  Worker 进程    │
│  1. 获取转录    │
│  2. LLM 生成    │──► 生成 Mermaid 语法
│  3. Mermaid     │──► 渲染为 PNG/SVG (可选)
│     渲染        │──► 上传到存储服务
└─────────────────┘
       │
       ▼
┌─────────────────┐
│  前端查询结果   │
│  GET /summaries │
│  /{task_id}     │
│  /visual/       │
│  {visual_type}  │
└─────────────────┘
       │
       ▼
┌─────────────────────────────┐
│  两种渲染方式:               │
│  1. 使用 Mermaid.js         │
│     客户端实时渲染           │
│  2. 显示后端生成的 PNG/SVG  │
└─────────────────────────────┘
```

### 1.3 数据流程

```
用户操作 → API 请求 → 任务排队 → Worker 处理 → 存储结果 → 前端查询展示
   ↓          ↓          ↓           ↓            ↓           ↓
[点击生成] [POST]   [Celery]   [LLM+渲染]   [数据库+存储] [GET+渲染]
```

**处理时间**: 5-12 秒（异步处理，不阻塞 UI）

---

## 2. API 接口详解

### 2.1 生成可视化摘要

#### 端点
```
POST /api/v1/summaries/{task_id}/visual
```

#### 请求头
```http
Authorization: Bearer {access_token}
Content-Type: application/json
Accept-Language: zh-CN
```

#### 请求体
```typescript
interface VisualSummaryRequest {
  visual_type: "mindmap" | "timeline" | "flowchart";  // 必填
  content_style?: "meeting" | "lecture" | "podcast" | "video" | "general";  // 可选，默认 "general"
  provider?: string;           // 可选，LLM 提供商（如 "deepseek"）
  model_id?: string;           // 可选，模型 ID（如 "deepseek-chat"）
  generate_image?: boolean;    // 可选，是否生成图片，默认 true
  image_format?: "png" | "svg"; // 可选，图片格式，默认 "png"
}
```

**字段说明**:

| 字段 | 类型 | 必填 | 说明 |
|-----|------|------|-----|
| `visual_type` | string | ✅ | 可视化类型：<br>• `mindmap`: 思维导图（适合讲座、播客）<br>• `timeline`: 时间轴（适合会议、讲座）<br>• `flowchart`: 流程图（适合教程、会议） |
| `content_style` | string | ❌ | 内容风格，影响生成的提示词：<br>• `meeting`: 会议记录<br>• `lecture`: 讲座课程<br>• `podcast`: 播客访谈<br>• `video`: 教程视频<br>• `general`: 通用内容<br>留空则自动检测 |
| `provider` | string | ❌ | LLM 服务商，如 `deepseek`, `qwen`, `doubao`<br>留空则系统自动选择 |
| `model_id` | string | ❌ | 具体模型 ID，仅部分 provider 需要 |
| `generate_image` | boolean | ❌ | 是否后端渲染图片，默认 `true`<br>• `true`: 返回 PNG/SVG 图片 URL<br>• `false`: 仅返回 Mermaid 语法 |
| `image_format` | string | ❌ | 图片格式，默认 `"png"`<br>• `png`: 体积较大，兼容性好<br>• `svg`: 体积小，可缩放 |

#### 请求示例

**最简请求**（推荐）:
```json
{
  "visual_type": "mindmap"
}
```

**完整参数请求**:
```json
{
  "visual_type": "timeline",
  "content_style": "meeting",
  "generate_image": true,
  "image_format": "svg",
  "provider": "deepseek",
  "model_id": "deepseek-chat"
}
```

#### 响应

**成功响应** (HTTP 200):
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "visual_type": "mindmap",
    "content_style": "lecture",
    "generate_image": true,
    "status": "queued"
  },
  "traceId": "req-xyz123"
}
```

**错误响应** (HTTP 200，但 code ≠ 0):
```json
{
  "code": 40400,
  "message": "任务不存在",
  "data": null,
  "traceId": "req-xyz123"
}
```

**常见错误码**:
- `40400`: 任务不存在
- `40300`: 无权访问该任务
- `40000`: 参数错误（如 visual_type 不合法）
- `40900`: 任务没有转写结果，无法生成可视化摘要

---

### 2.2 查询可视化摘要

#### 端点
```
GET /api/v1/summaries/{task_id}/visual/{visual_type}
```

#### 路径参数
- `task_id`: 任务 ID (UUID)
- `visual_type`: 可视化类型 (`mindmap` | `timeline` | `flowchart`)

#### 请求头
```http
Authorization: Bearer {access_token}
Accept-Language: zh-CN
```

#### 响应

**成功响应** (HTTP 200):
```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "id": "660e8400-e29b-41d4-a716-446655440011",
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "visual_type": "mindmap",
    "format": "mermaid",
    "content": "mindmap\n  root((深度学习基础))\n    神经网络\n      感知机\n      多层网络\n    反向传播\n      梯度下降\n    优化算法\n      SGD\n      Adam",
    "image_url": "/api/v1/media/visuals/user-id/task-id/mindmap_abc123.png",
    "model_used": "deepseek-chat",
    "token_count": 1250,
    "created_at": "2026-01-17T12:34:56Z"
  },
  "traceId": "req-xyz123"
}
```

**字段说明**:

| 字段 | 类型 | 说明 |
|-----|------|-----|
| `id` | string | 摘要记录 ID |
| `task_id` | string | 所属任务 ID |
| `visual_type` | string | 可视化类型 |
| `format` | string | 格式类型（当前固定为 `"mermaid"`） |
| `content` | string | **Mermaid 语法代码**（核心数据，用于前端渲染） |
| `image_url` | string\|null | 后端生成的图片 URL（如果 `generate_image=true`）<br>可直接用于 `<img src="">` |
| `model_used` | string\|null | 使用的 LLM 模型 |
| `token_count` | number\|null | 生成消耗的 token 数 |
| `created_at` | string | 生成时间 (ISO 8601) |

**错误响应**:
```json
{
  "code": 40400,
  "message": "未找到 mindmap 类型的可视化摘要",
  "data": null,
  "traceId": "req-xyz123"
}
```

---

### 2.3 查询所有摘要（包含可视化摘要）

#### 端点
```
GET /api/v1/summaries/{task_id}
```

#### 响应

```json
{
  "code": 0,
  "message": "成功",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "total": 6,
    "items": [
      {
        "id": "summary-1",
        "summary_type": "overview",
        "version": 1,
        "is_active": true,
        "content": "# 会议概览\n...",
        "model_used": "deepseek-chat",
        "created_at": "2026-01-17T12:30:00Z",
        "visual_format": null,
        "image_url": null
      },
      {
        "id": "summary-2",
        "summary_type": "visual_mindmap",
        "version": 1,
        "is_active": true,
        "content": "mindmap\n  root((主题))\n...",
        "model_used": "deepseek-chat",
        "created_at": "2026-01-17T12:35:00Z",
        "visual_format": "mermaid",
        "image_url": "/api/v1/media/visuals/.../mindmap_abc.png"
      }
    ]
  }
}
```

**区分可视化摘要**:
- `summary_type` 以 `visual_` 开头（如 `visual_mindmap`, `visual_timeline`, `visual_flowchart`）
- `visual_format` 不为 `null`（值为 `"mermaid"`）
- `image_url` 可能有值（后端渲染的图片）

---

## 3. 前端集成方案

### 3.1 推荐架构

#### 方案对比

| 方案 | 优点 | 缺点 | 适用场景 |
|-----|------|------|---------|
| **方案 A**: Mermaid.js 客户端渲染 | • 灵活，可交互<br>• 无需后端渲染<br>• 文件小（只传输文本） | • 首次加载需要引入库<br>• 渲染性能依赖客户端 | **推荐**: 桌面端、性能较好的设备 |
| **方案 B**: 后端渲染图片 | • 简单，直接显示<br>• 无需额外库<br>• 跨设备一致 | • 图片较大<br>• 无法交互<br>• 占用存储 | 移动端、低性能设备、需要导出分享 |
| **方案 C**: 混合方案 | • 最佳用户体验<br>• 降级兼容 | • 实现复杂度较高 | **推荐**: 生产环境 |

#### 推荐：混合方案

```typescript
// 伪代码
if (支持 Mermaid.js && 桌面端) {
  使用 Mermaid.js 渲染 visualSummary.content
} else if (visualSummary.image_url) {
  显示图片 <img src={visualSummary.image_url} />
} else {
  显示文本摘要或提示 "浏览器不支持可视化图表"
}
```

---

### 3.2 状态管理

#### 推荐状态设计 (以 Redux/Zustand 为例)

```typescript
interface VisualSummaryState {
  // 可视化摘要数据（按 visual_type 索引）
  summaries: {
    mindmap?: VisualSummary;
    timeline?: VisualSummary;
    flowchart?: VisualSummary;
  };

  // 加载状态
  loading: {
    mindmap: boolean;
    timeline: boolean;
    flowchart: boolean;
  };

  // 错误状态
  errors: {
    mindmap?: string;
    timeline?: string;
    flowchart?: string;
  };

  // 生成请求状态
  generating: {
    mindmap: boolean;
    timeline: boolean;
    flowchart: boolean;
  };
}

interface VisualSummary {
  id: string;
  task_id: string;
  visual_type: "mindmap" | "timeline" | "flowchart";
  format: "mermaid";
  content: string;        // Mermaid 语法
  image_url?: string;     // 图片 URL（可选）
  model_used?: string;
  token_count?: number;
  created_at: string;
}
```

#### Actions

```typescript
// 1. 请求生成可视化摘要
async function generateVisualSummary(
  taskId: string,
  visualType: "mindmap" | "timeline" | "flowchart",
  options?: {
    contentStyle?: string;
    generateImage?: boolean;
    imageFormat?: "png" | "svg";
  }
): Promise<void>;

// 2. 查询可视化摘要
async function fetchVisualSummary(
  taskId: string,
  visualType: "mindmap" | "timeline" | "flowchart"
): Promise<VisualSummary>;

// 3. 轮询检查生成状态
async function pollVisualSummaryStatus(
  taskId: string,
  visualType: string,
  maxAttempts?: number
): Promise<VisualSummary>;
```

---

### 3.3 UI 交互流程

#### 流程图

```
┌────────────────────┐
│ 用户查看任务详情页 │
└─────────┬──────────┘
          │
          ▼
┌─────────────────────┐
│ 显示"生成可视化摘要"│
│ 按钮（3个类型选项） │
└─────────┬───────────┘
          │ 用户点击"生成思维导图"
          ▼
┌─────────────────────┐
│ 调用 POST API       │
│ 显示 Loading 状态   │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────────┐
│ 开始轮询 GET API        │
│ 每 2 秒查询一次          │
│ 最多轮询 30 次 (60秒)   │
└─────────┬───────────────┘
          │
    ┌─────┴─────┐
    │           │
   成功         超时/失败
    │           │
    ▼           ▼
┌────────┐  ┌──────────┐
│ 渲染图表│  │ 显示错误  │
│ (Mermaid│  │ 提示重试  │
│  或图片) │  └──────────┘
└────────┘
```

#### UI 状态设计

```tsx
// 状态枚举
type VisualSummaryStatus =
  | "not_generated"    // 未生成
  | "generating"       // 生成中
  | "ready"            // 已生成，可展示
  | "failed";          // 生成失败

// UI 显示逻辑
function renderVisualSummaryButton(status: VisualSummaryStatus) {
  switch (status) {
    case "not_generated":
      return <Button onClick={handleGenerate}>生成思维导图</Button>;

    case "generating":
      return <Button disabled><Spinner /> 生成中...</Button>;

    case "ready":
      return <Button onClick={handleView}>查看思维导图</Button>;

    case "failed":
      return <Button onClick={handleRetry}>重新生成</Button>;
  }
}
```

---

## 4. Mermaid.js 使用指南

### 4.1 安装

#### NPM
```bash
npm install mermaid
# 或
yarn add mermaid
# 或
pnpm add mermaid
```

#### CDN（快速测试）
```html
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
  mermaid.initialize({ startOnLoad: true });
</script>
```

---

### 4.2 React 集成

#### 方案 1: 使用 `react-mermaid2`（推荐）

```bash
npm install react-mermaid2
```

```tsx
import React from 'react';
import Mermaid from 'react-mermaid2';

interface Props {
  mermaidCode: string;
}

export const MermaidChart: React.FC<Props> = ({ mermaidCode }) => {
  return (
    <div className="mermaid-container">
      <Mermaid chart={mermaidCode} />
    </div>
  );
};
```

#### 方案 2: 手动集成 Mermaid.js

```tsx
import React, { useEffect, useRef } from 'react';
import mermaid from 'mermaid';

interface Props {
  mermaidCode: string;
  theme?: 'default' | 'forest' | 'dark' | 'neutral';
}

export const MermaidChart: React.FC<Props> = ({
  mermaidCode,
  theme = 'neutral'
}) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 初始化 Mermaid
    mermaid.initialize({
      startOnLoad: true,
      theme: theme,
      securityLevel: 'loose',
      fontFamily: 'Arial, sans-serif',
    });
  }, [theme]);

  useEffect(() => {
    if (containerRef.current) {
      // 清空之前的内容
      containerRef.current.innerHTML = mermaidCode;

      // 渲染图表
      mermaid.contentLoaded();
    }
  }, [mermaidCode]);

  return (
    <div
      ref={containerRef}
      className="mermaid"
      style={{
        textAlign: 'center',
        padding: '20px',
        backgroundColor: '#f9f9f9',
        borderRadius: '8px'
      }}
    />
  );
};
```

#### 使用示例

```tsx
import { MermaidChart } from './components/MermaidChart';

function VisualSummaryView({ visualSummary }: { visualSummary: VisualSummary }) {
  return (
    <div>
      <h2>思维导图</h2>
      <MermaidChart
        mermaidCode={visualSummary.content}
        theme="neutral"
      />
    </div>
  );
}
```

---

### 4.3 Vue 集成

#### 安装
```bash
npm install vue3-mermaid
```

#### 使用

```vue
<template>
  <div class="visual-summary">
    <h2>{{ visualType }} 图表</h2>
    <vue-mermaid
      :nodes="mermaidCode"
      type="graph"
      config="{ theme: 'neutral' }"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { VueMermaid } from 'vue3-mermaid';

interface Props {
  mermaidCode: string;
  visualType: string;
}

const props = defineProps<Props>();
</script>
```

---

### 4.4 主题配置

Mermaid 支持多种主题，根据你的 UI 设计选择：

```typescript
const mermaidThemes = {
  light: 'default',    // 浅色主题
  dark: 'dark',        // 深色主题
  neutral: 'neutral',  // 中性主题（推荐）
  forest: 'forest',    // 森林主题（绿色调）
};

// 动态切换主题
mermaid.initialize({
  theme: isDarkMode ? 'dark' : 'neutral'
});
```

---

## 5. 完整示例代码

### 5.1 完整的 React 组件示例

```tsx
import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { MermaidChart } from './MermaidChart';

interface VisualSummary {
  id: string;
  task_id: string;
  visual_type: "mindmap" | "timeline" | "flowchart";
  format: "mermaid";
  content: string;
  image_url?: string;
  model_used?: string;
  created_at: string;
}

interface Props {
  taskId: string;
  accessToken: string;
}

export const VisualSummaryPanel: React.FC<Props> = ({ taskId, accessToken }) => {
  const [activeTab, setActiveTab] = useState<"mindmap" | "timeline" | "flowchart">("mindmap");
  const [summaries, setSummaries] = useState<Record<string, VisualSummary>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [renderMode, setRenderMode] = useState<"mermaid" | "image">("mermaid");

  // 生成可视化摘要
  const generateSummary = async (visualType: string) => {
    setLoading(prev => ({ ...prev, [visualType]: true }));
    setErrors(prev => ({ ...prev, [visualType]: '' }));

    try {
      // 1. 请求生成
      const response = await axios.post(
        `/api/v1/summaries/${taskId}/visual`,
        {
          visual_type: visualType,
          generate_image: renderMode === "image",
          image_format: "png"
        },
        {
          headers: {
            'Authorization': `Bearer ${accessToken}`,
            'Content-Type': 'application/json'
          }
        }
      );

      if (response.data.code !== 0) {
        throw new Error(response.data.message);
      }

      // 2. 轮询查询结果
      await pollSummaryStatus(visualType);

    } catch (error: any) {
      setErrors(prev => ({
        ...prev,
        [visualType]: error.message || '生成失败，请重试'
      }));
    } finally {
      setLoading(prev => ({ ...prev, [visualType]: false }));
    }
  };

  // 轮询查询
  const pollSummaryStatus = async (
    visualType: string,
    maxAttempts = 30,
    interval = 2000
  ) => {
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      try {
        const response = await axios.get(
          `/api/v1/summaries/${taskId}/visual/${visualType}`,
          {
            headers: {
              'Authorization': `Bearer ${accessToken}`
            }
          }
        );

        if (response.data.code === 0) {
          // 成功获取
          setSummaries(prev => ({
            ...prev,
            [visualType]: response.data.data
          }));
          return;
        } else if (response.data.code === 40400) {
          // 尚未生成，继续轮询
          await new Promise(resolve => setTimeout(resolve, interval));
        } else {
          // 其他错误
          throw new Error(response.data.message);
        }
      } catch (error: any) {
        if (attempt === maxAttempts - 1) {
          throw new Error('生成超时，请稍后刷新查看');
        }
        await new Promise(resolve => setTimeout(resolve, interval));
      }
    }
  };

  // 初始加载：尝试获取已存在的可视化摘要
  useEffect(() => {
    const fetchExistingSummaries = async () => {
      const types: Array<"mindmap" | "timeline" | "flowchart"> = ["mindmap", "timeline", "flowchart"];

      for (const type of types) {
        try {
          const response = await axios.get(
            `/api/v1/summaries/${taskId}/visual/${type}`,
            {
              headers: { 'Authorization': `Bearer ${accessToken}` }
            }
          );

          if (response.data.code === 0) {
            setSummaries(prev => ({ ...prev, [type]: response.data.data }));
          }
        } catch (error) {
          // 忽略未生成的类型
        }
      }
    };

    fetchExistingSummaries();
  }, [taskId, accessToken]);

  const currentSummary = summaries[activeTab];
  const isLoading = loading[activeTab];
  const error = errors[activeTab];

  return (
    <div className="visual-summary-panel">
      {/* 标签切换 */}
      <div className="tabs">
        <button
          className={activeTab === "mindmap" ? "active" : ""}
          onClick={() => setActiveTab("mindmap")}
        >
          📊 思维导图
        </button>
        <button
          className={activeTab === "timeline" ? "active" : ""}
          onClick={() => setActiveTab("timeline")}
        >
          📅 时间轴
        </button>
        <button
          className={activeTab === "flowchart" ? "active" : ""}
          onClick={() => setActiveTab("flowchart")}
        >
          🔀 流程图
        </button>
      </div>

      {/* 渲染模式切换 */}
      <div className="render-mode-toggle">
        <label>
          <input
            type="radio"
            value="mermaid"
            checked={renderMode === "mermaid"}
            onChange={() => setRenderMode("mermaid")}
          />
          前端渲染（交互式）
        </label>
        <label>
          <input
            type="radio"
            value="image"
            checked={renderMode === "image"}
            onChange={() => setRenderMode("image")}
          />
          图片展示（快速加载）
        </label>
      </div>

      {/* 内容区域 */}
      <div className="content">
        {isLoading && (
          <div className="loading">
            <div className="spinner" />
            <p>正在生成 {activeTab} 图表，预计需要 5-12 秒...</p>
          </div>
        )}

        {error && (
          <div className="error">
            <p>{error}</p>
            <button onClick={() => generateSummary(activeTab)}>重试</button>
          </div>
        )}

        {!isLoading && !error && !currentSummary && (
          <div className="empty-state">
            <p>尚未生成 {activeTab} 图表</p>
            <button onClick={() => generateSummary(activeTab)}>
              立即生成
            </button>
          </div>
        )}

        {!isLoading && !error && currentSummary && (
          <>
            {renderMode === "mermaid" ? (
              <MermaidChart
                mermaidCode={currentSummary.content}
                theme="neutral"
              />
            ) : currentSummary.image_url ? (
              <img
                src={currentSummary.image_url}
                alt={`${activeTab} 图表`}
                style={{ maxWidth: '100%', height: 'auto' }}
              />
            ) : (
              <div className="fallback">
                <p>图片暂不可用，切换到前端渲染模式查看</p>
                <button onClick={() => setRenderMode("mermaid")}>
                  切换到前端渲染
                </button>
              </div>
            )}

            <div className="meta">
              <span>生成时间: {new Date(currentSummary.created_at).toLocaleString()}</span>
              <span>模型: {currentSummary.model_used}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
```

---

### 5.2 样式参考 (CSS)

```css
.visual-summary-panel {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 20px;
  background: white;
}

.tabs {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
  border-bottom: 2px solid #f0f0f0;
}

.tabs button {
  padding: 10px 20px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 16px;
  transition: all 0.3s;
}

.tabs button.active {
  border-bottom: 2px solid #1890ff;
  color: #1890ff;
  font-weight: bold;
}

.render-mode-toggle {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.loading {
  text-align: center;
  padding: 40px;
}

.spinner {
  border: 4px solid #f3f3f3;
  border-top: 4px solid #1890ff;
  border-radius: 50%;
  width: 40px;
  height: 40px;
  animation: spin 1s linear infinite;
  margin: 0 auto 10px;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.error {
  background: #fff2f0;
  border: 1px solid #ffccc7;
  border-radius: 4px;
  padding: 20px;
  text-align: center;
  color: #cf1322;
}

.empty-state {
  text-align: center;
  padding: 40px;
  color: #8c8c8c;
}

.meta {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid #f0f0f0;
  display: flex;
  justify-content: space-between;
  font-size: 14px;
  color: #8c8c8c;
}
```

---

## 6. 错误处理

### 6.1 常见错误场景

| 场景 | 错误码 | 处理方式 |
|-----|-------|---------|
| 任务不存在 | 40400 | 提示用户刷新页面或返回任务列表 |
| 无权访问任务 | 40300 | 提示用户登录或检查权限 |
| 任务无转写结果 | 40900 | 提示用户等待转写完成后再生成 |
| 生成超时 | - | 提示用户稍后刷新页面查看 |
| LLM 生成失败 | 51000 | 提供重试按钮 |
| 图片渲染失败 | - | 降级显示 Mermaid 语法 |
| Mermaid 渲染失败 | - | 显示错误提示，提供刷新按钮 |

### 6.2 错误处理最佳实践

```typescript
// 统一错误处理函数
function handleVisualSummaryError(error: any): string {
  // API 错误
  if (error.response?.data?.code) {
    const code = error.response.data.code;
    const message = error.response.data.message;

    switch (code) {
      case 40400:
        return '任务不存在，请刷新页面';
      case 40300:
        return '无权访问该任务';
      case 40900:
        return '任务尚未完成转写，请稍后再试';
      case 51000:
        return 'LLM 服务暂时不可用，请稍后重试';
      default:
        return message || '未知错误，请联系客服';
    }
  }

  // 网络错误
  if (error.message === 'Network Error') {
    return '网络连接失败，请检查网络设置';
  }

  // 超时
  if (error.code === 'ECONNABORTED') {
    return '请求超时，请重试';
  }

  // Mermaid 渲染错误
  if (error.name === 'MermaidParseError') {
    return '图表渲染失败，请尝试切换到图片模式';
  }

  return '操作失败，请重试';
}
```

---

## 7. 性能优化

### 7.1 优化建议

#### 1. 懒加载 Mermaid.js

```tsx
import { lazy, Suspense } from 'react';

// 动态加载 Mermaid 组件
const MermaidChart = lazy(() => import('./MermaidChart'));

function VisualSummaryView() {
  return (
    <Suspense fallback={<div>加载图表组件中...</div>}>
      <MermaidChart mermaidCode={code} />
    </Suspense>
  );
}
```

#### 2. 缓存已生成的摘要

```typescript
// 使用 React Query 缓存
import { useQuery } from '@tanstack/react-query';

function useVisualSummary(taskId: string, visualType: string) {
  return useQuery({
    queryKey: ['visualSummary', taskId, visualType],
    queryFn: () => fetchVisualSummary(taskId, visualType),
    staleTime: 10 * 60 * 1000,  // 10分钟内不重新请求
    cacheTime: 30 * 60 * 1000,  // 缓存30分钟
  });
}
```

#### 3. 图片预加载

```tsx
useEffect(() => {
  if (visualSummary.image_url) {
    const img = new Image();
    img.src = visualSummary.image_url;
    // 预加载图片到浏览器缓存
  }
}, [visualSummary.image_url]);
```

#### 4. 防止重复请求

```typescript
const generateSummaryRef = useRef<Record<string, boolean>>({});

const generateSummary = async (visualType: string) => {
  // 防止重复点击
  if (generateSummaryRef.current[visualType]) {
    return;
  }

  generateSummaryRef.current[visualType] = true;

  try {
    await doGenerate(visualType);
  } finally {
    generateSummaryRef.current[visualType] = false;
  }
};
```

---

### 7.2 性能监控

```typescript
// 记录生成耗时
const startTime = Date.now();

await generateSummary(visualType);

const duration = Date.now() - startTime;

// 上报到监控系统
analytics.track('visual_summary_generated', {
  task_id: taskId,
  visual_type: visualType,
  duration_ms: duration,
  success: true
});
```

---

## 8. 测试与调试

### 8.1 测试用例

#### 单元测试（Jest）

```typescript
import { render, screen, waitFor } from '@testing-library/react';
import { VisualSummaryPanel } from './VisualSummaryPanel';

describe('VisualSummaryPanel', () => {
  it('should display generate button when no summary exists', () => {
    render(<VisualSummaryPanel taskId="test-id" accessToken="token" />);

    expect(screen.getByText('立即生成')).toBeInTheDocument();
  });

  it('should show loading state during generation', async () => {
    const { getByText } = render(
      <VisualSummaryPanel taskId="test-id" accessToken="token" />
    );

    const generateBtn = getByText('立即生成');
    generateBtn.click();

    await waitFor(() => {
      expect(screen.getByText(/正在生成/)).toBeInTheDocument();
    });
  });

  it('should render Mermaid chart when summary is ready', async () => {
    // Mock API response
    global.fetch = jest.fn(() =>
      Promise.resolve({
        json: () => Promise.resolve({
          code: 0,
          data: {
            content: 'mindmap\n  root((Test))',
            visual_type: 'mindmap'
          }
        })
      })
    );

    render(<VisualSummaryPanel taskId="test-id" accessToken="token" />);

    await waitFor(() => {
      expect(screen.getByText(/Test/)).toBeInTheDocument();
    });
  });
});
```

---

### 8.2 调试技巧

#### 1. 开启 Mermaid Debug 模式

```typescript
mermaid.initialize({
  startOnLoad: true,
  logLevel: 'debug',  // 开启调试日志
});
```

#### 2. 验证 Mermaid 语法

在线工具: https://mermaid.live/

```typescript
// 在控制台验证语法
console.log('Mermaid Code:', visualSummary.content);

// 复制到 mermaid.live 测试
```

#### 3. 查看 API 响应

```typescript
axios.interceptors.response.use(response => {
  console.log('API Response:', response.data);
  return response;
});
```

---

### 8.3 常见问题排查

#### Q1: Mermaid 图表不显示

**可能原因**:
- Mermaid.js 未正确加载
- 语法错误
- CSS 冲突

**排查步骤**:
```javascript
// 1. 检查 Mermaid 是否加载
console.log(window.mermaid);

// 2. 检查语法
try {
  mermaid.parse(mermaidCode);
  console.log('Syntax valid');
} catch (e) {
  console.error('Syntax error:', e);
}

// 3. 检查容器元素
console.log(document.querySelector('.mermaid'));
```

#### Q2: 图片不显示（404）

**可能原因**:
- 图片路径错误
- 权限问题
- 后端渲染失败

**排查步骤**:
```javascript
// 检查图片 URL
console.log('Image URL:', visualSummary.image_url);

// 尝试直接访问
window.open(visualSummary.image_url);

// 检查后端日志
```

#### Q3: 轮询一直失败

**可能原因**:
- Worker 进程未启动
- LLM 服务不可用
- 数据库连接问题

**排查步骤**:
```bash
# 检查 Worker 状态
docker ps | grep worker

# 检查 Celery 日志
docker logs ai-audio-assistant-worker

# 检查任务队列
redis-cli
> LLEN celery
```

---

## 9. 附录

### 9.1 Mermaid 语法速查

#### Mindmap（思维导图）

```mermaid
mindmap
  root((中心主题))
    分支1
      子节点1-1
      子节点1-2
    分支2
      子节点2-1
```

#### Timeline（时间轴）

```mermaid
timeline
    title 时间轴标题
    section 阶段1
      事件1 : 描述1
      事件2 : 描述2
    section 阶段2
      事件3 : 描述3
```

#### Flowchart（流程图）

```mermaid
flowchart TD
    Start([开始]) --> Step1[步骤1]
    Step1 --> Decision{判断}
    Decision -->|是| Step2[步骤2]
    Decision -->|否| End([结束])
    Step2 --> End
```

---

### 9.2 API 完整请求示例（Postman）

#### 生成可视化摘要

```http
POST /api/v1/summaries/550e8400-e29b-41d4-a716-446655440000/visual
Host: localhost:8000
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Content-Type: application/json
Accept-Language: zh-CN

{
  "visual_type": "mindmap",
  "content_style": "lecture",
  "generate_image": true,
  "image_format": "png"
}
```

#### 查询可视化摘要

```http
GET /api/v1/summaries/550e8400-e29b-41d4-a716-446655440000/visual/mindmap
Host: localhost:8000
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Accept-Language: zh-CN
```

---

### 9.3 TypeScript 类型定义

```typescript
// types/visual-summary.ts

export type VisualType = "mindmap" | "timeline" | "flowchart";

export type ContentStyle = "meeting" | "lecture" | "podcast" | "video" | "general";

export type ImageFormat = "png" | "svg";

export interface VisualSummaryRequest {
  visual_type: VisualType;
  content_style?: ContentStyle;
  provider?: string;
  model_id?: string;
  generate_image?: boolean;
  image_format?: ImageFormat;
}

export interface VisualSummary {
  id: string;
  task_id: string;
  visual_type: VisualType;
  format: "mermaid";
  content: string;
  image_url?: string;
  model_used?: string;
  token_count?: number;
  created_at: string;
}

export interface VisualSummaryResponse {
  code: number;
  message: string;
  data: VisualSummary | null;
  traceId: string;
}

export interface GenerateResponse {
  code: number;
  message: string;
  data: {
    task_id: string;
    visual_type: VisualType;
    content_style: string;
    generate_image: boolean;
    status: "queued";
  };
  traceId: string;
}
```

---

### 9.4 参考资源

- **Mermaid 官方文档**: https://mermaid.js.org/
- **React Mermaid**: https://github.com/mermaid-js/mermaid-react
- **Mermaid Live Editor**: https://mermaid.live/ （在线测试工具）
- **后端实施文档**: `docs/VISUAL_SUMMARY_IMPLEMENTATION.md`

---

## 10. 联系与支持

如有问题或建议，请联系：
- 后端开发团队: backend@example.com
- 项目文档: `/docs`
- Issue 跟踪: GitHub Issues

---

**文档版本**: v1.0
**更新日期**: 2026-01-17
**维护者**: AI Audio Assistant Team
