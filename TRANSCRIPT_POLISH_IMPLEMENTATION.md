# 转写润色（Transcript Polish）完整实现方案

> 给 Claude Code 的实现指令。请严格按照步骤顺序执行，每步完成后跑 `mypy app/` 和 `uvicorn app.main:app` 确认无报错。

---

## 背景

ASR 转写普遍存在错别字、断词、中英混杂识别错误等问题（如 "论魂"→"论文"、"open、I"→"OpenAI"、"A震"→"AI的"）。在 ASR 转写和 LLM 摘要之间插入一个固定的 LLM 纠错步骤，提升转写文本和下游摘要的质量。

## 核心设计原则

1. **默认开启，不加开关** — 纠错是流程固定环节，不需要 TaskOptions 新字段
2. **只做段内文字纠错，不动结构** — segment 数量、时间戳、speaker_id 全部保持不变
3. **润色失败不阻断主流程** — 降级为使用原始转写继续 summarize
4. **利用现有 DB 字段** — `is_edited` + `original_content` 已存在，不需要 migration
5. **使用 `chat()` 方法** — 所有 LLM provider（doubao/deepseek/qwen/moonshot/openrouter）都已实现 `chat(messages, **kwargs)`，不要用 `summarize()` 或 `generate()`

---

## Step 1: StageType 加 POLISH 阶段

**文件**: `app/core/task_stages.py`

在 `StageType` enum 中，`TRANSCRIBE` 和 `SUMMARIZE` 之间加入：

```python
POLISH = "polish"
```

最终顺序：
```python
class StageType(str, Enum):
    RESOLVE_YOUTUBE = "resolve_youtube"
    DOWNLOAD = "download"
    TRANSCODE = "transcode"
    UPLOAD_STORAGE = "upload_storage"
    TRANSCRIBE = "transcribe"
    POLISH = "polish"          # ← 新增
    SUMMARIZE = "summarize"
```

不需要改 StageStatus 和 RetryMode。

---

## Step 2: 新建转写润色服务

**新建文件**: `app/services/transcript_polish.py`

```python
"""转写润色服务 - 利用 LLM 纠正 ASR 转写错误

设计要点：
- 按时间窗口分组（~180秒），让 LLM 看到上下文处理跨段错误
- 严格要求 LLM 逐段返回，段数不变
- 解析失败时回退到原文，绝不丢数据
- 使用 chat() 方法，兼容所有已集成的 LLM provider
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

from loguru import logger

from app.services.llm.base import LLMService


# ============================================================
# 数据结构
# ============================================================

@dataclass
class PolishResult:
    """单个片段的润色结果"""
    sequence: int
    original_content: str
    polished_content: str
    changed: bool


# ============================================================
# System Prompt（硬编码，内容稳定不需要 PromptHub）
# ============================================================

POLISH_SYSTEM_PROMPT = """你是一个专业的语音转写校对助手。你的任务是修正 ASR（语音识别）转写文本中的错误。

修正范围：
1. 错别字和同音字错误：如"论魂"→"论文"，"战场边"→"沾点边"
2. 英文术语识别错误：如"open、I"→"OpenAI"，"LL wl"→根据上下文判断正确内容
3. 中英混杂识别错误：如"A震"→"AI的"
4. 明显的多余字或漏字
5. 冗余语气词：如果整段只有"嗯"、"呃"、"那个"等语气词且无实质内容，将内容替换为空字符串""

严格规则：
- 输出段数必须与输入完全一致，一一对应
- 不要合并段落，不要拆分段落，不要调换顺序
- 不要改变原意，不要添加原文没有的内容
- 保持说话风格（口语保持口语）
- 如果某段没有错误，原样输出
- 每行格式：[序号] 修正后的内容
- 只输出修正结果，不要输出任何解释或说明"""


# ============================================================
# 分组策略
# ============================================================

def group_segments_by_time(
    segments: List[Dict[str, Any]],
    window_seconds: float = 180.0,
    max_per_group: int = 50,
) -> List[List[Dict[str, Any]]]:
    """按时间窗口分组，让 LLM 看到上下文。

    Args:
        segments: 片段列表，每项需包含 sequence, content, start_time, end_time
        window_seconds: 时间窗口大小（秒），默认 3 分钟
        max_per_group: 每组最大片段数

    Returns:
        分组后的片段列表
    """
    if not segments:
        return []

    groups: List[List[Dict[str, Any]]] = []
    current_group: List[Dict[str, Any]] = []
    group_start: float = segments[0]["start_time"]

    for seg in segments:
        elapsed = seg["start_time"] - group_start
        if current_group and (
            elapsed >= window_seconds or len(current_group) >= max_per_group
        ):
            groups.append(current_group)
            current_group = [seg]
            group_start = seg["start_time"]
        else:
            current_group.append(seg)

    if current_group:
        groups.append(current_group)

    return groups


# ============================================================
# Prompt 构建
# ============================================================

def build_polish_user_prompt(segments: List[Dict[str, Any]]) -> str:
    """构建 user prompt，逐段编号。"""
    lines = [f"[{seg['sequence']}] {seg['content']}" for seg in segments]
    return "请校对以下 ASR 转写文本，逐段修正错误：\n\n" + "\n".join(lines)


# ============================================================
# 结果解析
# ============================================================

_RESULT_PATTERN = re.compile(r"\[(\d+)\]\s*(.*)")


def parse_polish_response(
    response: str,
    original_segments: List[Dict[str, Any]],
) -> List[PolishResult]:
    """解析 LLM 返回的润色结果。

    期望格式：每行 [序号] 内容。
    解析失败的段回退到原文。
    """
    # 解析 LLM 输出
    parsed: Dict[int, str] = {}
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = _RESULT_PATTERN.match(line)
        if match:
            seq = int(match.group(1))
            content = match.group(2).strip()
            parsed[seq] = content

    # 逐段匹配
    results: List[PolishResult] = []
    for seg in original_segments:
        seq = seg["sequence"]
        original = seg["content"]

        if seq in parsed:
            polished = parsed[seq]
            # 空字符串表示纯语气词段，保留原文避免数据丢失
            if not polished:
                polished = original
            changed = polished != original
        else:
            # LLM 没返回这一段，保持原文
            polished = original
            changed = False

        results.append(PolishResult(
            sequence=seq,
            original_content=original,
            polished_content=polished,
            changed=changed,
        ))

    return results


# ============================================================
# 主入口
# ============================================================

async def polish_transcripts(
    llm_service: LLMService,
    segments: List[Dict[str, Any]],
    window_seconds: float = 180.0,
) -> List[PolishResult]:
    """对转写片段进行 LLM 润色。

    Args:
        llm_service: LLM 服务实例（需实现 chat 方法）
        segments: 转写数据列表，每项需包含:
            - sequence (int): 序号
            - content (str): 文本内容
            - start_time (float): 开始时间
            - end_time (float): 结束时间
        window_seconds: 分组时间窗口（秒）

    Returns:
        所有片段的润色结果列表
    """
    if not segments:
        return []

    groups = group_segments_by_time(segments, window_seconds)
    all_results: List[PolishResult] = []

    logger.info(
        "Polish: %d segments split into %d groups (window=%ds)",
        len(segments),
        len(groups),
        int(window_seconds),
    )

    for group_idx, group in enumerate(groups, start=1):
        user_prompt = build_polish_user_prompt(group)

        messages = [
            {"role": "system", "content": POLISH_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response: str = await llm_service.chat(
                messages,
                temperature=0.3,
                max_tokens=len(user_prompt) * 2,
            )

            group_results = parse_polish_response(response, group)
            all_results.extend(group_results)

            changed_in_group = sum(1 for r in group_results if r.changed)
            logger.info(
                "Polish group %d/%d: %d/%d segments changed",
                group_idx,
                len(groups),
                changed_in_group,
                len(group),
            )

        except Exception as exc:
            # 单组失败不影响其他组，该组全部回退到原文
            logger.warning(
                "Polish group %d/%d failed, falling back to original: %s",
                group_idx,
                len(groups),
                exc,
            )
            for seg in group:
                all_results.append(PolishResult(
                    sequence=seg["sequence"],
                    original_content=seg["content"],
                    polished_content=seg["content"],
                    changed=False,
                ))

    return all_results
```

---

## Step 3: 集成到 process_audio.py（异步版）

**文件**: `worker/tasks/process_audio.py`

### 3.1 新增 import

在文件顶部的 import 区域添加：

```python
from app.services.transcript_polish import polish_transcripts
```

### 3.2 插入润色逻辑

**插入位置**：在 transcripts 写入 DB 并 commit 之后、RAG chunk ingest 之后、`await _update_task(session, task, "summarizing", 80, ...)` **之前**。

具体来说，找到类似这段代码的位置：

```python
            # （现有代码）record_usage_sync / stage_manager.complete_stage(TRANSCRIBE) 之后
            # （现有代码）RAG chunk ingest 之后（如果有的话）

            # ======== 在这里插入润色逻辑 ========

            await _update_task(session, task, "summarizing", 80, "summarizing", request_id)
```

在 `_update_task(..., "summarizing", ...)` 之前插入以下代码：

```python
            # ========== 转写润色（固定步骤）==========
            await _update_task(session, task, "polishing", 72, "polishing", request_id)
            stage_manager.start_stage(session, StageType.POLISH)

            try:
                # 从 DB 读取刚保存的 transcripts
                polish_query = (
                    select(Transcript)
                    .where(Transcript.task_id == task_id)
                    .order_by(Transcript.sequence)
                )
                polish_result = await session.execute(polish_query)
                transcript_rows = polish_result.scalars().all()

                seg_dicts = [
                    {
                        "sequence": t.sequence,
                        "content": t.content,
                        "start_time": float(t.start_time),
                        "end_time": float(t.end_time),
                    }
                    for t in transcript_rows
                ]

                # 获取 LLM 服务（复用 summarize 的选择逻辑）
                polish_provider, polish_model_id = _resolve_llm_selection(
                    task, str(task.user_id)
                )
                polish_llm: LLMService = await _maybe_await(
                    get_llm_service(polish_provider, polish_model_id, str(task.user_id))
                )

                polish_results = await polish_transcripts(polish_llm, seg_dicts)

                # 写回 DB：只更新有变化的段
                changed_count = 0
                for pr in polish_results:
                    if pr.changed:
                        for t in transcript_rows:
                            if t.sequence == pr.sequence:
                                t.original_content = pr.original_content
                                t.content = pr.polished_content
                                t.is_edited = True
                                changed_count += 1
                                break

                if changed_count > 0:
                    await _commit(session)

                stage_manager.complete_stage(
                    session,
                    StageType.POLISH,
                    {
                        "total_segments": len(seg_dicts),
                        "changed_segments": changed_count,
                    },
                )
                logger.info(
                    "Task %s: Polish completed, %d/%d segments changed",
                    task_id,
                    changed_count,
                    len(seg_dicts),
                    extra={
                        "task_id": task_id,
                        "changed_segments": changed_count,
                        "total_segments": len(seg_dicts),
                    },
                )

            except Exception as exc:
                # 润色失败不阻断主流程，降级使用原始转写继续 summarize
                logger.warning(
                    "Task %s: Polish failed, continuing with original transcripts: %s",
                    task_id,
                    exc,
                    extra={"task_id": task_id, "error": str(exc)},
                )
                stage_manager.fail_stage(
                    session,
                    StageType.POLISH,
                    ErrorCode.LLM_SERVICE_FAILED,
                    str(exc),
                )
                # 注意：不 return，继续走 summarize

            # ========== 润色结束 ==========
```

### 3.3 关键修改：summarize 阶段必须从 DB 重新读取

当前 summarize 阶段用的是内存中的 `segments` 变量（ASR 返回值）：

```python
full_text = "\n".join([seg.content for seg in segments])
```

**这行必须改掉**，因为 polish 写入的修正内容在 DB 中，内存里的 segments 还是旧的。

改为从 DB 读取最新内容：

```python
            # 从 DB 读取最新的转写内容（可能已被润色修改）
            summarize_query = (
                select(Transcript)
                .where(Transcript.task_id == task_id)
                .order_by(Transcript.sequence)
            )
            summarize_result = await session.execute(summarize_query)
            latest_transcripts = summarize_result.scalars().all()
            full_text = "\n".join([t.content for t in latest_transcripts])
```

替换掉原来的 `full_text = "\n".join([seg.content for seg in segments])`。

注意：如果当前代码中 summarize 部分是在新的 `async with async_session_factory() as session:` 块里，那就在那个 session 里查询。确保在正确的 session 上下文中执行。

---

## Step 4: 集成到 process_youtube.py（同步版）

**文件**: `worker/tasks/process_youtube.py`

### 4.1 新增 import

```python
from app.services.transcript_polish import polish_transcripts
```

### 4.2 插入润色逻辑

同样在 transcripts 写入 DB 之后、summarize 之前插入。注意这个文件是同步的，LLM 调用需要用 `asyncio.run()` 包装。

找到 `_update_task(session, task, "summarizing", ...)` 之前，插入：

```python
            # ========== 转写润色（固定步骤）==========
            _update_task(session, task, "polishing", 72, "polishing", request_id)
            stage_manager.start_stage(session, StageType.POLISH)

            try:
                transcript_rows = (
                    session.query(Transcript)
                    .filter(Transcript.task_id == task_id)
                    .order_by(Transcript.sequence)
                    .all()
                )

                seg_dicts = [
                    {
                        "sequence": t.sequence,
                        "content": t.content,
                        "start_time": float(t.start_time),
                        "end_time": float(t.end_time),
                    }
                    for t in transcript_rows
                ]

                polish_provider, polish_model_id = _resolve_llm_selection(
                    task, str(task.user_id)
                )
                polish_llm = asyncio.run(
                    SmartFactory.get_service(
                        "llm",
                        provider=polish_provider,
                        model_id=polish_model_id,
                        user_id=str(task.user_id),
                    )
                )

                polish_results = asyncio.run(
                    polish_transcripts(polish_llm, seg_dicts)
                )

                changed_count = 0
                for pr in polish_results:
                    if pr.changed:
                        for t in transcript_rows:
                            if t.sequence == pr.sequence:
                                t.original_content = pr.original_content
                                t.content = pr.polished_content
                                t.is_edited = True
                                changed_count += 1
                                break

                if changed_count > 0:
                    session.commit()

                stage_manager.complete_stage(
                    session,
                    StageType.POLISH,
                    {
                        "total_segments": len(seg_dicts),
                        "changed_segments": changed_count,
                    },
                )
                logger.info(
                    "Task %s: Polish completed, %d/%d segments changed",
                    task_id,
                    changed_count,
                    len(seg_dicts),
                    extra={
                        "task_id": task_id,
                        "changed_segments": changed_count,
                        "total_segments": len(seg_dicts),
                    },
                )

            except Exception as exc:
                logger.warning(
                    "Task %s: Polish failed, continuing with original transcripts: %s",
                    task_id,
                    exc,
                    extra={"task_id": task_id, "error": str(exc)},
                )
                stage_manager.fail_stage(
                    session,
                    StageType.POLISH,
                    ErrorCode.LLM_SERVICE_FAILED,
                    str(exc),
                )

            # ========== 润色结束 ==========
```

### 4.3 同样修改 summarize 阶段的文本读取

将 `full_text = "\n".join([seg.content for seg in segments])` 改为：

```python
                # 从 DB 读取最新的转写内容（可能已被润色修改）
                latest_transcripts = (
                    session.query(Transcript)
                    .filter(Transcript.task_id == task_id)
                    .order_by(Transcript.sequence)
                    .all()
                )
                full_text = "\n".join([t.content for t in latest_transcripts])
```

---

## Step 5: 进度映射调整

当前映射：
- transcribing: 20-70%
- summarizing: 70-99%

新映射：
- transcribing: 20-70%
- polishing: 70-80%
- summarizing: 80-99%

具体修改点：
1. polish 阶段 `_update_task` 的 progress 设为 72
2. summarize 阶段 `_update_task` 的 progress 从原来的 75/80 调整为 82

在两个 worker 文件中找到 summarize 阶段的 `_update_task(session, task, "summarizing", 75/80, ...)` 并将进度值改为 82。

---

## Step 6: ErrorCode 确认

检查 `app/i18n/codes.py` 中是否已有 `LLM_SERVICE_FAILED` 错误码。如果没有，用现有的 `AI_SUMMARY_SERVICE_UNAVAILABLE` 或 `AI_SUMMARY_GENERATION_FAILED` 替代。polish 阶段的 `stage_manager.fail_stage` 中使用的错误码必须是 ErrorCode enum 中已存在的值。

---

## 不需要做的事情

- ❌ 不需要数据库 migration（`is_edited` 和 `original_content` 字段已存在于 transcripts 表）
- ❌ 不需要修改 TaskOptions schema（不加开关，固定开启）
- ❌ 不需要新 API 端点（前端读 transcripts 时 `is_edited=True` 的就是润色过的）
- ❌ 不需要新的 PromptHub slug（polish prompt 硬编码在服务模块中）
- ❌ 不需要修改 Transcript 模型或 schema
- ❌ 不需要修改 RetryMode enum
- ❌ 不做段合并/拆分（V1 只做段内文字纠错）

---

## 实现顺序 Checklist

```
[ ] 1. app/core/task_stages.py — StageType 加 POLISH
[ ] 2. app/services/transcript_polish.py — 新建润色服务模块
[ ] 3. worker/tasks/process_audio.py — 插入润色步骤 + 修改 summarize 文本读取
[ ] 4. worker/tasks/process_youtube.py — 插入润色步骤 + 修改 summarize 文本读取
[ ] 5. 调整两个 worker 文件中 summarize 阶段的进度值
[ ] 6. mypy app/ 通过
[ ] 7. uvicorn app.main:app 启动无报错
```

---

## 关键约束提醒（给 Claude Code）

1. **润色失败绝对不能阻断主流程** — try/except 包裹整个 polish 块，except 里只 log + fail_stage，不 return
2. **chat() 方法的 kwargs** — 所有 provider 的 chat() 都接受 `temperature` 和 `max_tokens` 作为 kwargs，参考 doubao.py / deepseek.py / qwen.py 的实现
3. **process_audio.py 是全异步** — 用 `await session.execute(select(...))` 查询，用 `await _commit(session)` 提交
4. **process_youtube.py 是同步** — 用 `session.query(Transcript).filter(...)` 查询，用 `session.commit()` 提交，LLM 调用用 `asyncio.run()` 包装
5. **segments 变量是 ASR 返回值** — 它在内存中，polish 写 DB 后不会自动更新，所以 summarize 必须重新从 DB 读取
6. **遵循项目规范** — type annotations、loguru logger（不是 logging）、BusinessError、extra dict in logger calls
7. **不要改 LLMService 基类** — 不需要加 generate() 方法，直接用现有的 chat()
8. **Transcript model import** — 确保在插入代码的位置能 import 到 `Transcript` 和 `select`（检查文件顶部现有 imports）
