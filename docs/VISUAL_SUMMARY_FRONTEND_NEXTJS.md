# Visual Summary Frontend Integration Guide (Next.js)

## 为 ai-audio-assistant-ui 项目量身定制的集成指南

本文档基于你的实际 Next.js 项目结构，提供可直接使用的代码和集成方案。

---

## 1. TypeScript 类型定义扩展

### 1.1 更新 `src/types/api.ts`

在现有的 `SummaryType` 和 `SummaryItem` 基础上添加可视化摘要支持：

```typescript
// ============================================================================
// 摘要相关（扩展）
// ============================================================================

/**
 * 摘要类型（v1.3 新增可视化类型）
 */
export type SummaryType =
  | "overview"
  | "key_points"
  | "action_items"
  | "visual_mindmap"      // 新增：思维导图
  | "visual_timeline"     // 新增：时间轴
  | "visual_flowchart"    // 新增：流程图

/**
 * 可视化摘要类型（仅可视化部分）
 */
export type VisualType = "mindmap" | "timeline" | "flowchart"

/**
 * 内容风格
 */
export type ContentStyle = "meeting" | "lecture" | "podcast" | "video" | "general"

/**
 * 摘要项（v1.3 新增可视化字段）
 */
export interface SummaryItem {
  id: string
  summary_type: SummaryType
  version: number
  is_active: boolean
  content: string
  model_used: string | null
  prompt_version: string | null
  token_count: number | null
  created_at: string

  // v1.3 新增可视化字段
  visual_format?: "mermaid" | "json" | null
  visual_content?: string | null
  image_key?: string | null
  image_format?: "png" | "svg" | null
}

/**
 * 可视化摘要生成请求
 */
export interface VisualSummaryRequest {
  visual_type: VisualType
  content_style?: ContentStyle | null
  provider?: string | null
  model_id?: string | null
  generate_image?: boolean  // 是否生成 PNG/SVG 图片，默认 true
  image_format?: "png" | "svg"  // 图片格式，默认 png
}

/**
 * 可视化摘要响应（单个）
 */
export interface VisualSummaryResponse {
  id: string
  task_id: string
  visual_type: VisualType
  format: "mermaid" | "json"
  content: string  // Mermaid 语法代码
  image_url?: string | null  // 生成的图片 URL（如果启用了后端渲染）
  model_used?: string | null
  token_count?: number | null
  created_at: string
}

/**
 * SSE 流式事件类型
 */
export interface VisualStreamEvent {
  type: "visual.generating" | "visual.rendering" | "visual.uploading" | "visual.completed" | "error"
  data?: {
    visual_type?: VisualType
    progress?: number
    content?: string
    image_url?: string
    error?: string
  }
}
```

### 1.2 创建新文件 `src/types/visual-summary.ts`（可选，更清晰的模块化）

```typescript
/**
 * 可视化摘要专用类型定义
 */

export type VisualType = "mindmap" | "timeline" | "flowchart"
export type ContentStyle = "meeting" | "lecture" | "podcast" | "video" | "general"
export type ImageFormat = "png" | "svg"

export interface VisualSummaryGenerateRequest {
  visual_type: VisualType
  content_style?: ContentStyle
  provider?: string
  model_id?: string
  generate_image?: boolean
  image_format?: ImageFormat
}

export interface VisualSummaryData {
  id: string
  task_id: string
  visual_type: VisualType
  format: "mermaid"
  content: string
  image_url?: string
  model_used?: string
  created_at: string
}

export interface MermaidRenderOptions {
  theme?: "default" | "dark" | "neutral" | "forest"
  backgroundColor?: string
  width?: number
  height?: number
}

export interface VisualSummaryError {
  code: number
  message: string
  visual_type?: VisualType
}
```

---

## 2. API Client 扩展

### 2.1 更新 `src/lib/api-client.ts`

在现有的 API client 中添加可视化摘要相关方法：

```typescript
import type {
  ApiResponse,
  VisualSummaryRequest,
  VisualSummaryResponse,
  VisualType
} from "@/types/api"

// ... 现有代码 ...

/**
 * 生成可视化摘要（异步任务）
 */
export async function generateVisualSummary(
  taskId: string,
  data: VisualSummaryRequest
): Promise<ApiResponse<{ task_id: string; status: string }>> {
  return request<{ task_id: string; status: string }>({
    url: `/api/v1/summaries/${taskId}/visual`,
    method: "POST",
    data,
  })
}

/**
 * 获取已生成的可视化摘要
 */
export async function getVisualSummary(
  taskId: string,
  visualType: VisualType
): Promise<ApiResponse<VisualSummaryResponse>> {
  return request<VisualSummaryResponse>({
    url: `/api/v1/summaries/${taskId}/visual/${visualType}`,
    method: "GET",
  })
}

/**
 * 获取图片 URL（如果后端生成了图片）
 * @param imageKey - Summary 对象中的 image_key 字段
 */
export function getVisualImageUrl(imageKey: string): string {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
  return `${baseUrl}/api/v1/media/${imageKey}`
}

/**
 * 订阅可视化摘要生成进度（SSE）
 * @param taskId 任务 ID
 * @param visualType 可视化类型
 * @param onEvent 事件回调
 * @param onError 错误回调
 * @returns 关闭 SSE 连接的函数
 */
export function subscribeVisualSummaryProgress(
  taskId: string,
  visualType: VisualType,
  onEvent: (event: VisualStreamEvent) => void,
  onError?: (error: Error) => void
): () => void {
  const token = getToken()
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
  const url = `${baseUrl}/api/v1/summaries/${taskId}/visual/${visualType}/stream?token=${encodeURIComponent(token || "")}`

  const eventSource = new EventSource(url)

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      onEvent(data)
    } catch (error) {
      console.error("Failed to parse SSE event:", error)
    }
  }

  eventSource.onerror = (error) => {
    console.error("SSE connection error:", error)
    onError?.(new Error("SSE connection failed"))
    eventSource.close()
  }

  // 返回关闭函数
  return () => {
    eventSource.close()
  }
}
```

---

## 3. React 组件实现

### 3.1 创建 `src/components/task/VisualSummaryView.tsx`

这是一个完整的可视化摘要展示组件，集成了 Mermaid.js 渲染和图片展示：

```typescript
"use client"

import React, { useEffect, useState, useRef } from "react"
import mermaid from "mermaid"
import { Loader2, Download, Maximize2, AlertCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription } from "@/components/ui/alert"
import type { VisualSummaryResponse, VisualType } from "@/types/api"
import { getVisualSummary, getVisualImageUrl } from "@/lib/api-client"

interface VisualSummaryViewProps {
  taskId: string
  visualType: VisualType
  autoLoad?: boolean  // 是否自动加载
  renderMode?: "mermaid" | "image" | "both"  // 渲染模式
  className?: string
}

export function VisualSummaryView({
  taskId,
  visualType,
  autoLoad = true,
  renderMode = "mermaid",
  className = "",
}: VisualSummaryViewProps) {
  const [data, setData] = useState<VisualSummaryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mermaidRendered, setMermaidRendered] = useState(false)
  const mermaidRef = useRef<HTMLDivElement>(null)

  // 初始化 Mermaid
  useEffect(() => {
    mermaid.initialize({
      startOnLoad: false,
      theme: "default",
      securityLevel: "loose",
      fontFamily: "ui-sans-serif, system-ui, sans-serif",
    })
  }, [])

  // 加载可视化摘要数据
  const loadVisualSummary = async () => {
    setLoading(true)
    setError(null)

    try {
      const response = await getVisualSummary(taskId, visualType)

      if (response.code !== 0) {
        throw new Error(response.message)
      }

      setData(response.data)
    } catch (err) {
      const message = err instanceof Error ? err.message : "加载失败"
      setError(message)
      console.error("Failed to load visual summary:", err)
    } finally {
      setLoading(false)
    }
  }

  // 渲染 Mermaid 图表
  const renderMermaid = async () => {
    if (!data?.content || !mermaidRef.current) return

    try {
      const { svg } = await mermaid.render(
        `mermaid-${data.id}`,
        data.content
      )

      if (mermaidRef.current) {
        mermaidRef.current.innerHTML = svg
        setMermaidRendered(true)
      }
    } catch (err) {
      console.error("Mermaid render error:", err)
      setError("图表渲染失败，可能是 Mermaid 语法错误")
    }
  }

  // 自动加载
  useEffect(() => {
    if (autoLoad) {
      loadVisualSummary()
    }
  }, [taskId, visualType, autoLoad])

  // 数据加载后渲染 Mermaid
  useEffect(() => {
    if (data && (renderMode === "mermaid" || renderMode === "both")) {
      renderMermaid()
    }
  }, [data, renderMode])

  // 下载图片
  const handleDownloadImage = () => {
    if (!data?.image_url) return

    const link = document.createElement("a")
    link.href = getVisualImageUrl(data.image_url)
    link.download = `${visualType}_${taskId}.${data.image_url.split(".").pop()}`
    link.click()
  }

  // 全屏查看
  const handleFullscreen = () => {
    if (!mermaidRef.current) return

    if (mermaidRef.current.requestFullscreen) {
      mermaidRef.current.requestFullscreen()
    }
  }

  // 加载状态
  if (loading) {
    return (
      <div className={`flex items-center justify-center py-12 ${className}`}>
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <span className="ml-3 text-muted-foreground">正在加载可视化摘要...</span>
      </div>
    )
  }

  // 错误状态
  if (error) {
    return (
      <Alert variant="destructive" className={className}>
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>
          {error}
          <Button
            variant="outline"
            size="sm"
            className="ml-4"
            onClick={loadVisualSummary}
          >
            重试
          </Button>
        </AlertDescription>
      </Alert>
    )
  }

  // 无数据
  if (!data) {
    return (
      <div className={`text-center py-12 text-muted-foreground ${className}`}>
        <p>暂无可视化摘要</p>
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          onClick={loadVisualSummary}
        >
          加载
        </Button>
      </div>
    )
  }

  return (
    <div className={`space-y-4 ${className}`}>
      {/* 工具栏 */}
      <div className="flex items-center justify-between border-b pb-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>模型: {data.model_used || "未知"}</span>
          <span>•</span>
          <span>格式: {data.format}</span>
          {data.image_url && (
            <>
              <span>•</span>
              <span>已生成图片</span>
            </>
          )}
        </div>

        <div className="flex gap-2">
          {data.image_url && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleDownloadImage}
            >
              <Download className="h-4 w-4 mr-2" />
              下载图片
            </Button>
          )}

          {renderMode === "mermaid" && mermaidRendered && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleFullscreen}
            >
              <Maximize2 className="h-4 w-4 mr-2" />
              全屏
            </Button>
          )}
        </div>
      </div>

      {/* 图表内容 */}
      {renderMode === "image" && data.image_url ? (
        // 仅显示后端生成的图片
        <div className="flex justify-center bg-muted/30 rounded-lg p-6">
          <img
            src={getVisualImageUrl(data.image_url)}
            alt={`${visualType} visual summary`}
            className="max-w-full h-auto"
          />
        </div>
      ) : renderMode === "mermaid" ? (
        // 仅使用 Mermaid.js 渲染
        <div
          ref={mermaidRef}
          className="mermaid-container flex justify-center bg-muted/30 rounded-lg p-6 overflow-auto"
          style={{ minHeight: "400px" }}
        />
      ) : (
        // 同时显示两者（both 模式）
        <div className="space-y-4">
          {data.image_url && (
            <div>
              <h4 className="text-sm font-medium mb-2">后端渲染图片</h4>
              <div className="flex justify-center bg-muted/30 rounded-lg p-6">
                <img
                  src={getVisualImageUrl(data.image_url)}
                  alt={`${visualType} visual summary (backend)`}
                  className="max-w-full h-auto"
                />
              </div>
            </div>
          )}

          <div>
            <h4 className="text-sm font-medium mb-2">客户端渲染（Mermaid.js）</h4>
            <div
              ref={mermaidRef}
              className="mermaid-container flex justify-center bg-muted/30 rounded-lg p-6 overflow-auto"
              style={{ minHeight: "400px" }}
            />
          </div>
        </div>
      )}

      {/* 原始代码（可折叠查看）*/}
      <details className="border rounded-lg p-4">
        <summary className="cursor-pointer text-sm font-medium">
          查看 Mermaid 源代码
        </summary>
        <pre className="mt-3 p-4 bg-muted rounded text-xs overflow-x-auto">
          <code>{data.content}</code>
        </pre>
      </details>
    </div>
  )
}

// 导出便捷的预设组件
export function MindmapView({ taskId, ...props }: Omit<VisualSummaryViewProps, "visualType">) {
  return <VisualSummaryView taskId={taskId} visualType="mindmap" {...props} />
}

export function TimelineView({ taskId, ...props }: Omit<VisualSummaryViewProps, "visualType">) {
  return <VisualSummaryView taskId={taskId} visualType="timeline" {...props} />
}

export function FlowchartView({ taskId, ...props }: Omit<VisualSummaryViewProps, "visualType">) {
  return <VisualSummaryView taskId={taskId} visualType="flowchart" {...props} />
}
```

### 3.2 扩展现有的 `src/components/task/SummaryView.tsx`

在现有的摘要视图中集成可视化摘要标签页：

```typescript
"use client"

import React, { useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { VisualSummaryView } from "./VisualSummaryView"
// ... 现有导入 ...

export function SummaryView({ taskId }: { taskId: string }) {
  const [activeTab, setActiveTab] = useState<string>("overview")

  // ... 现有状态和逻辑 ...

  return (
    <div className="space-y-4">
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-6">
          <TabsTrigger value="overview">概述</TabsTrigger>
          <TabsTrigger value="key_points">要点</TabsTrigger>
          <TabsTrigger value="action_items">行动项</TabsTrigger>
          {/* v1.3 新增可视化标签 */}
          <TabsTrigger value="mindmap">思维导图</TabsTrigger>
          <TabsTrigger value="timeline">时间轴</TabsTrigger>
          <TabsTrigger value="flowchart">流程图</TabsTrigger>
        </TabsList>

        {/* 现有文本摘要标签页 */}
        <TabsContent value="overview">
          {/* 现有逻辑 */}
        </TabsContent>

        <TabsContent value="key_points">
          {/* 现有逻辑 */}
        </TabsContent>

        <TabsContent value="action_items">
          {/* 现有逻辑 */}
        </TabsContent>

        {/* v1.3 新增可视化摘要标签页 */}
        <TabsContent value="mindmap">
          <VisualSummaryView
            taskId={taskId}
            visualType="mindmap"
            autoLoad={activeTab === "mindmap"}
            renderMode="mermaid"
          />
        </TabsContent>

        <TabsContent value="timeline">
          <VisualSummaryView
            taskId={taskId}
            visualType="timeline"
            autoLoad={activeTab === "timeline"}
            renderMode="mermaid"
          />
        </TabsContent>

        <TabsContent value="flowchart">
          <VisualSummaryView
            taskId={taskId}
            visualType="flowchart"
            autoLoad={activeTab === "flowchart"}
            renderMode="mermaid"
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}
```

---

## 4. 可视化摘要生成组件

### 4.1 创建 `src/components/task/VisualSummaryGenerator.tsx`

用于触发可视化摘要生成的组件：

```typescript
"use client"

import React, { useState } from "react"
import { Loader2, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Alert, AlertDescription } from "@/components/ui/alert"
import type { VisualType, ContentStyle } from "@/types/api"
import { generateVisualSummary, subscribeVisualSummaryProgress } from "@/lib/api-client"

interface VisualSummaryGeneratorProps {
  taskId: string
  onGenerated?: (visualType: VisualType) => void
}

export function VisualSummaryGenerator({
  taskId,
  onGenerated,
}: VisualSummaryGeneratorProps) {
  const [visualType, setVisualType] = useState<VisualType>("mindmap")
  const [contentStyle, setContentStyle] = useState<ContentStyle | "auto">("auto")
  const [generateImage, setGenerateImage] = useState(true)
  const [imageFormat, setImageFormat] = useState<"png" | "svg">("png")
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const handleGenerate = async () => {
    setLoading(true)
    setError(null)
    setProgress(0)

    try {
      // 1. 发起生成请求
      const response = await generateVisualSummary(taskId, {
        visual_type: visualType,
        content_style: contentStyle === "auto" ? null : contentStyle,
        generate_image: generateImage,
        image_format: imageFormat,
      })

      if (response.code !== 0) {
        throw new Error(response.message)
      }

      // 2. 订阅 SSE 进度更新
      const unsubscribe = subscribeVisualSummaryProgress(
        taskId,
        visualType,
        (event) => {
          switch (event.type) {
            case "visual.generating":
              setProgress(30)
              break
            case "visual.rendering":
              setProgress(60)
              break
            case "visual.uploading":
              setProgress(80)
              break
            case "visual.completed":
              setProgress(100)
              setLoading(false)
              onGenerated?.(visualType)
              unsubscribe()
              break
            case "error":
              setError(event.data?.error || "生成失败")
              setLoading(false)
              unsubscribe()
              break
          }
        },
        (err) => {
          setError(err.message)
          setLoading(false)
        }
      )

      // 超时保护（30秒）
      setTimeout(() => {
        if (loading) {
          unsubscribe()
          setLoading(false)
          setError("生成超时，请稍后查看")
        }
      }, 30000)
    } catch (err) {
      const message = err instanceof Error ? err.message : "生成失败"
      setError(message)
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6 border rounded-lg p-6 bg-card">
      <div className="flex items-center gap-2">
        <Sparkles className="h-5 w-5 text-primary" />
        <h3 className="text-lg font-semibold">生成可视化摘要</h3>
      </div>

      {/* 可视化类型选择 */}
      <div className="space-y-2">
        <Label>可视化类型</Label>
        <Select value={visualType} onValueChange={(v) => setVisualType(v as VisualType)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="mindmap">思维导图（适合讲座/播客）</SelectItem>
            <SelectItem value="timeline">时间轴（适合会议/讲座）</SelectItem>
            <SelectItem value="flowchart">流程图（适合会议/教程）</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* 内容风格选择 */}
      <div className="space-y-2">
        <Label>内容风格</Label>
        <Select value={contentStyle} onValueChange={(v) => setContentStyle(v as ContentStyle | "auto")}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">自动检测</SelectItem>
            <SelectItem value="meeting">会议</SelectItem>
            <SelectItem value="lecture">讲座</SelectItem>
            <SelectItem value="podcast">播客</SelectItem>
            <SelectItem value="video">视频</SelectItem>
            <SelectItem value="general">通用</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* 图片生成选项 */}
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <Label>生成图片</Label>
          <p className="text-sm text-muted-foreground">
            是否在后端渲染 PNG/SVG 图片
          </p>
        </div>
        <Switch checked={generateImage} onCheckedChange={setGenerateImage} />
      </div>

      {/* 图片格式选择 */}
      {generateImage && (
        <div className="space-y-2">
          <Label>图片格式</Label>
          <Select value={imageFormat} onValueChange={(v) => setImageFormat(v as "png" | "svg")}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="png">PNG（适合分享）</SelectItem>
              <SelectItem value="svg">SVG（矢量，文件小）</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* 生成按钮 */}
      <Button
        className="w-full"
        onClick={handleGenerate}
        disabled={loading}
      >
        {loading ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            生成中... {progress}%
          </>
        ) : (
          <>
            <Sparkles className="mr-2 h-4 w-4" />
            生成可视化摘要
          </>
        )}
      </Button>
    </div>
  )
}
```

---

## 5. 安装 Mermaid 依赖

在你的 Next.js 项目中安装 Mermaid.js：

```bash
cd /Users/sean/code/ai-audio-assistant-ui
npm install mermaid
# 或
pnpm add mermaid
# 或
yarn add mermaid
```

---

## 6. CSS 样式优化（可选）

创建 `src/styles/mermaid.css` 来自定义 Mermaid 图表样式：

```css
/* Mermaid 图表全局样式 */
.mermaid-container {
  width: 100%;
  overflow-x: auto;
}

.mermaid-container svg {
  max-width: 100%;
  height: auto;
}

/* 思维导图样式优化 */
.mermaid-container .mindmap-node {
  font-family: ui-sans-serif, system-ui, sans-serif;
}

/* 暗黑模式适配 */
@media (prefers-color-scheme: dark) {
  .mermaid-container svg {
    background-color: transparent;
  }

  .mermaid-container text {
    fill: hsl(var(--foreground));
  }

  .mermaid-container rect,
  .mermaid-container circle,
  .mermaid-container path {
    stroke: hsl(var(--border));
  }
}
```

在 `src/app/layout.tsx` 中引入：

```typescript
import "@/styles/mermaid.css"
```

---

## 7. 完整使用示例

### 7.1 在任务详情页集成

假设你的任务详情页在 `src/app/tasks/[id]/page.tsx`：

```typescript
"use client"

import React, { useState } from "react"
import { SummaryView } from "@/components/task/SummaryView"
import { VisualSummaryGenerator } from "@/components/task/VisualSummaryGenerator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export default function TaskDetailPage({ params }: { params: { id: string } }) {
  const taskId = params.id
  const [activeTab, setActiveTab] = useState("summary")
  const [refreshKey, setRefreshKey] = useState(0)

  return (
    <div className="container py-6 space-y-6">
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="summary">摘要</TabsTrigger>
          <TabsTrigger value="visual-generate">生成可视化</TabsTrigger>
        </TabsList>

        <TabsContent value="summary">
          <SummaryView key={refreshKey} taskId={taskId} />
        </TabsContent>

        <TabsContent value="visual-generate">
          <VisualSummaryGenerator
            taskId={taskId}
            onGenerated={() => {
              // 生成完成后切换到摘要标签页并刷新
              setActiveTab("summary")
              setRefreshKey((k) => k + 1)
            }}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}
```

---

## 8. 错误处理和边缘情况

### 8.1 常见错误码处理

在你的 API client 中添加错误处理：

```typescript
export async function getVisualSummary(
  taskId: string,
  visualType: VisualType
): Promise<ApiResponse<VisualSummaryResponse>> {
  try {
    return await request<VisualSummaryResponse>({
      url: `/api/v1/summaries/${taskId}/visual/${visualType}`,
      method: "GET",
    })
  } catch (error) {
    if (error instanceof ApiError) {
      // 特定错误码处理
      switch (error.code) {
        case ErrorCode.TASK_NOT_FOUND:
          throw new Error("任务不存在")
        case ErrorCode.SUMMARY_NOT_FOUND:
          throw new Error(`${visualType} 可视化摘要尚未生成`)
        case ErrorCode.PERMISSION_DENIED:
          throw new Error("无权访问此任务")
        case ErrorCode.LLM_FAILED:
          throw new Error("LLM 服务失败，请稍后重试")
        default:
          throw error
      }
    }
    throw error
  }
}
```

### 8.2 Mermaid 渲染失败处理

```typescript
const renderMermaid = async () => {
  if (!data?.content || !mermaidRef.current) return

  try {
    const { svg } = await mermaid.render(
      `mermaid-${data.id}`,
      data.content
    )

    if (mermaidRef.current) {
      mermaidRef.current.innerHTML = svg
      setMermaidRendered(true)
    }
  } catch (err) {
    console.error("Mermaid render error:", err)

    // 回退到显示后端图片（如果存在）
    if (data.image_url) {
      setRenderMode("image")
    } else {
      setError("图表渲染失败，Mermaid 语法可能存在错误")
    }
  }
}
```

---

## 9. 性能优化建议

### 9.1 懒加载 Mermaid.js

```typescript
import { lazy, Suspense } from "react"

const VisualSummaryView = lazy(() =>
  import("@/components/task/VisualSummaryView").then((m) => ({
    default: m.VisualSummaryView
  }))
)

export function SummaryView({ taskId }: { taskId: string }) {
  return (
    <Suspense fallback={<div>加载中...</div>}>
      <VisualSummaryView taskId={taskId} visualType="mindmap" />
    </Suspense>
  )
}
```

### 9.2 缓存已渲染的 SVG

```typescript
const [svgCache, setSvgCache] = useState<Map<string, string>>(new Map())

const renderMermaid = async () => {
  const cacheKey = `${data.id}-${data.content}`

  // 检查缓存
  if (svgCache.has(cacheKey)) {
    if (mermaidRef.current) {
      mermaidRef.current.innerHTML = svgCache.get(cacheKey)!
      setMermaidRendered(true)
    }
    return
  }

  // 渲染并缓存
  const { svg } = await mermaid.render(`mermaid-${data.id}`, data.content)
  setSvgCache((cache) => new Map(cache).set(cacheKey, svg))

  if (mermaidRef.current) {
    mermaidRef.current.innerHTML = svg
    setMermaidRendered(true)
  }
}
```

---

## 10. 测试建议

### 10.1 单元测试示例（使用 Jest + React Testing Library）

```typescript
import { render, screen, waitFor } from "@testing-library/react"
import { VisualSummaryView } from "@/components/task/VisualSummaryView"
import { getVisualSummary } from "@/lib/api-client"

jest.mock("@/lib/api-client")

describe("VisualSummaryView", () => {
  it("renders loading state initially", () => {
    render(<VisualSummaryView taskId="test-task" visualType="mindmap" />)
    expect(screen.getByText(/正在加载/i)).toBeInTheDocument()
  })

  it("renders visual summary when data loads", async () => {
    const mockData = {
      id: "123",
      task_id: "test-task",
      visual_type: "mindmap",
      format: "mermaid",
      content: "mindmap\n  root((Test))",
      created_at: "2026-01-17T00:00:00Z",
    }

    ;(getVisualSummary as jest.Mock).mockResolvedValue({
      code: 0,
      data: mockData,
      message: "成功",
      traceId: "trace-123",
    })

    render(<VisualSummaryView taskId="test-task" visualType="mindmap" />)

    await waitFor(() => {
      expect(screen.queryByText(/正在加载/i)).not.toBeInTheDocument()
    })
  })

  it("shows error when API fails", async () => {
    ;(getVisualSummary as jest.Mock).mockRejectedValue(
      new Error("API Error")
    )

    render(<VisualSummaryView taskId="test-task" visualType="mindmap" />)

    await waitFor(() => {
      expect(screen.getByText(/加载失败/i)).toBeInTheDocument()
    })
  })
})
```

---

## 11. 部署注意事项

### 11.1 环境变量配置

在 `.env.local` 中确保 API URL 配置正确：

```bash
NEXT_PUBLIC_API_URL=https://your-backend-api.com
```

### 11.2 生产构建优化

在 `next.config.js` 中优化 Mermaid 打包：

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  webpack: (config) => {
    // Mermaid.js 使用较大的依赖，优化打包
    config.optimization = {
      ...config.optimization,
      moduleIds: 'deterministic',
      splitChunks: {
        chunks: 'all',
        cacheGroups: {
          mermaid: {
            test: /[\\/]node_modules[\\/]mermaid[\\/]/,
            name: 'mermaid',
            priority: 10,
          },
        },
      },
    }
    return config
  },
}

module.exports = nextConfig
```

---

## 12. 完整文件清单

### 需要创建的新文件：

1. `src/components/task/VisualSummaryView.tsx` - 可视化摘要展示组件
2. `src/components/task/VisualSummaryGenerator.tsx` - 可视化摘要生成组件
3. `src/styles/mermaid.css` - Mermaid 样式（可选）
4. `src/types/visual-summary.ts` - 可视化摘要类型定义（可选）

### 需要修改的现有文件：

1. `src/types/api.ts` - 添加可视化摘要类型
2. `src/lib/api-client.ts` - 添加 API 方法
3. `src/components/task/SummaryView.tsx` - 集成可视化摘要标签页

### 需要安装的依赖：

```bash
npm install mermaid
```

---

## 13. 快速开始指南

### 步骤 1: 安装依赖

```bash
cd /Users/sean/code/ai-audio-assistant-ui
npm install mermaid
```

### 步骤 2: 更新类型定义

复制上面 `src/types/api.ts` 的扩展内容到你的文件中。

### 步骤 3: 更新 API Client

复制上面 `src/lib/api-client.ts` 的新增方法到你的文件中。

### 步骤 4: 创建可视化组件

创建 `VisualSummaryView.tsx` 和 `VisualSummaryGenerator.tsx` 组件。

### 步骤 5: 集成到 SummaryView

修改现有的 `SummaryView.tsx`，添加可视化摘要标签页。

### 步骤 6: 测试

启动开发服务器并测试：

```bash
npm run dev
```

访问任务详情页，点击"生成可视化"标签页，选择类型并生成。

---

## 14. 常见问题

**Q: Mermaid 渲染后样式异常？**
A: 检查 CSS 冲突，可能需要为 `.mermaid-container` 设置 `isolation: isolate` 或使用 Shadow DOM。

**Q: SSE 连接失败？**
A: 确保后端启用了 CORS，并且 SSE 端点正确配置。检查浏览器控制台 Network 标签页。

**Q: 图片加载失败（403/404）？**
A: 检查 `getVisualImageUrl()` 返回的 URL 是否正确，以及后端 `/api/v1/media/` 端点是否正常工作。

**Q: 生成超时？**
A: 后端生成可能需要 5-12 秒，确保 SSE 超时设置合理（建议 30 秒以上）。

---

## 15. 进阶功能

### 15.1 导出为 PDF

```typescript
import html2canvas from "html2canvas"
import jsPDF from "jspdf"

const handleExportPDF = async () => {
  if (!mermaidRef.current) return

  const canvas = await html2canvas(mermaidRef.current)
  const imgData = canvas.toDataURL("image/png")

  const pdf = new jsPDF({
    orientation: "landscape",
    unit: "px",
    format: [canvas.width, canvas.height],
  })

  pdf.addImage(imgData, "PNG", 0, 0, canvas.width, canvas.height)
  pdf.save(`${visualType}_${taskId}.pdf`)
}
```

### 15.2 交互式编辑

集成 Monaco Editor 允许用户直接编辑 Mermaid 语法：

```typescript
import Editor from "@monaco-editor/react"

const [editedContent, setEditedContent] = useState(data.content)

<Editor
  height="400px"
  language="mermaid"
  value={editedContent}
  onChange={(value) => setEditedContent(value || "")}
  theme="vs-dark"
/>
```

---

## 16. 参考资源

- [Mermaid.js 官方文档](https://mermaid.js.org/)
- [Next.js 官方文档](https://nextjs.org/docs)
- [React 19 文档](https://react.dev/)
- [Radix UI 组件库](https://www.radix-ui.com/)

---

**文档版本**: v1.0
**最后更新**: 2026-01-17
**适用项目**: ai-audio-assistant-ui (Next.js 16 + React 19)
