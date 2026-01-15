# Worker任务流程集成指南

## 背景

我们创建了新的`worker/tasks/summary_generator.py`模块，提供质量感知的摘要生成功能。现在需要在`worker/tasks/process_audio.py`中集成它。

## 需要修改的位置

**文件：** `worker/tasks/process_audio.py`

**当前代码位置：** 约750-842行（生成摘要的部分）

## 修改步骤

### 1. 添加导入

在文件顶部添加新的导入：

```python
from worker.tasks.summary_generator import generate_summaries_with_quality_awareness
```

### 2. 替换现有的摘要生成代码

**查找这段代码：**

```python
# 约731-750行
full_text = "\n".join([seg.content for seg in segments])

options = task.options or {}
content_style = options.get("summary_style", "meeting")
if not isinstance(content_style, str):
    content_style = "meeting"

logger.info(
    "Task %s: Starting LLM summarization with %d characters of text (style: %s)",
    task_id,
    len(full_text),
    content_style,
    extra={
        "task_id": task_id,
        "text_length": len(full_text),
        "content_style": content_style,
    },
)

summaries = []
llm_usages: list[LLMUsage] = []
for summary_type in ("overview", "key_points", "action_items"):
    # ... 大量生成逻辑 ...
```

**替换为：**

```python
# 提取content_style
options = task.options or {}
content_style = options.get("summary_style", "meeting")
if not isinstance(content_style, str):
    content_style = "meeting"

# 提取LLM provider配置
llm_provider_option = options.get("provider")
llm_model_id_option = options.get("model_id")

logger.info(
    "Task %s: Starting quality-aware summary generation (style: %s)",
    task_id,
    content_style,
    extra={
        "task_id": task_id,
        "content_style": content_style,
        "segments_count": len(segments),
    },
)

# 使用新的质量感知摘要生成函数
try:
    summaries, summary_metadata = await generate_summaries_with_quality_awareness(
        task_id=str(task.id),
        segments=segments,
        content_style=content_style,
        session=session,
        user_id=str(task.user_id),
        provider=llm_provider_option,
        model_id=llm_model_id_option,
    )

    # 记录元数据
    logger.info(
        "Task %s: Summary generation completed - quality: %s, confidence: %.2f, "
        "provider: %s, model: %s, summaries: %d",
        task_id,
        summary_metadata["quality_score"],
        summary_metadata["avg_confidence"],
        summary_metadata["llm_provider"],
        summary_metadata["llm_model"],
        summary_metadata["summaries_generated"],
        extra={"task_id": task_id, "summary_metadata": summary_metadata},
    )

    # 更新任务的llm_provider字段
    if summary_metadata.get("llm_provider"):
        task.llm_provider = summary_metadata["llm_provider"]

    # 保存所有摘要到数据库
    session.add_all(summaries)
    await session.commit()

    logger.info(
        "Task %s: All summaries saved to database",
        task_id,
        extra={"task_id": task_id, "summary_count": len(summaries)},
    )

except Exception as exc:
    logger.error(
        "Task %s: Summary generation failed: %s",
        task_id,
        exc,
        exc_info=True,
        extra={"task_id": task_id},
    )
    # 摘要生成失败，任务标记为failed
    raise BusinessError(
        ErrorCode.AI_SUMMARY_GENERATION_FAILED,
        reason=f"Failed to generate summaries: {exc}",
    ) from exc
```

### 3. 删除旧的LLMUsage记录逻辑（可选）

新的实现暂时没有集成LLMUsage记录，如果需要保留usage追踪，可以后续在`summary_generator.py`中添加。

目前可以先删除或注释掉旧的`llm_usages`相关代码：

```python
# 删除这些行：
llm_usages: list[LLMUsage] = []
# ... 以及所有 llm_usages.append() 的代码
# ... 以及 session.add_all(llm_usages) 的代码
```

## 完整的修改示例

**修改前（简化版）：**

```python
# Line ~731
full_text = "\n".join([seg.content for seg in segments])
options = task.options or {}
content_style = options.get("summary_style", "meeting")

summaries = []
for summary_type in ("overview", "key_points", "action_items"):
    content = await llm_service.summarize(full_text, summary_type, content_style)
    summaries.append(Summary(...))

session.add_all(summaries)
await session.commit()
```

**修改后：**

```python
# Line ~731
from worker.tasks.summary_generator import generate_summaries_with_quality_awareness

options = task.options or {}
content_style = options.get("summary_style", "meeting")
provider = options.get("provider")
model_id = options.get("model_id")

try:
    summaries, metadata = await generate_summaries_with_quality_awareness(
        task_id=str(task.id),
        segments=segments,
        content_style=content_style,
        session=session,
        user_id=str(task.user_id),
        provider=provider,
        model_id=model_id,
    )

    task.llm_provider = metadata.get("llm_provider")
    session.add_all(summaries)
    await session.commit()

    logger.info(f"Task {task_id}: Summaries generated - {metadata}")

except Exception as exc:
    logger.error(f"Task {task_id}: Summary generation failed: {exc}", exc_info=True)
    raise BusinessError(ErrorCode.AI_SUMMARY_GENERATION_FAILED) from exc
```

## 验证步骤

1. **语法检查**：确保没有import错误
2. **本地测试**：使用测试音频运行一次完整流程
3. **日志检查**：查看日志确认质量评估、预处理、章节划分都正常工作
4. **数据库检查**：确认生成了4种summary（overview, key_points, action_items, chapters）
5. **格式检查**：查看生成的摘要是否符合新的结构化格式

## 测试用例

```python
# 测试短内容（应跳过章节划分）
task_options = {"summary_style": "meeting"}
# 预期：生成3种摘要，无chapters

# 测试长内容（应生成章节）
task_options = {"summary_style": "lecture"}
# 预期：生成4种摘要，包含chapters

# 测试低质量转写
# 构造低置信度的segments
# 预期：日志显示"Low quality transcript detected"，使用premium model
```

## 回滚方案

如果新代码出现问题，快速回滚：

```bash
cd worker/tasks
git checkout process_audio.py
```

删除新文件：

```bash
rm worker/tasks/summary_generator.py
```

## 注意事项

1. **异步处理**：所有LLM调用都是异步的，确保使用`await`
2. **错误处理**：单个摘要失败不应影响其他摘要的生成
3. **日志记录**：关键步骤都有详细日志，便于调试
4. **性能**：质量评估和预处理的开销很小（<100ms）
5. **成本**：章节划分会增加一次LLM调用，但只对长内容执行

## 后续优化

1. **并行生成**：3种摘要可以并行生成，提升速度
2. **LLMUsage追踪**：集成usage记录功能
3. **缓存机制**：相同文本的章节划分可以缓存
4. **流式生成**：支持流式生成摘要（用于UI实时展示）

---

**文档版本：** V1.0
**创建日期：** 2025-01-15
