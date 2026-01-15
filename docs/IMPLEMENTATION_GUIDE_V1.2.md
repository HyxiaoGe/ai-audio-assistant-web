# V1.2版本实施指南

## 已完成的工作 ✅

### 1. 转写文本预处理和质量评估工具
- ✅ 创建 `app/utils/transcript_processor.py`
  - `TranscriptQuality` 类：质量评估结果
  - `TranscriptProcessor` 类：提供质量评估和预处理功能
  - 支持过滤语气词、合并segment、生成质量提示

### 2. 章节划分提示词模板
- ✅ 创建 `app/prompts/templates/segmentation/` 目录
- ✅ 创建 `config.json` - 章节划分配置
- ✅ 创建 `zh-CN.json` - 中文章节划分提示词
  - 支持5种内容风格的章节划分
  - 包含质量容错指导

### 3. 更新摘要提示词
- ✅ 更新 `app/prompts/templates/summary/config.json` 到v1.2.0
  - 提升max_tokens限制
  - 调整temperature参数
- ✅ 更新 `app/prompts/templates/summary/zh-CN.json`
  - 增加质量提示变量`{quality_notice}`
  - 优化为结构化Markdown输出
  - 三种风格完全差异化（meeting/lecture/podcast）
  - 增加表格、emoji等结构化元素

---

## 剩余工作 🚧

### 第一步：修改 PromptManager 支持新的模板变量

**文件：** `app/prompts/manager.py`

**需要修改的地方：**

当前的 `get_prompt()` 方法可能不支持 `quality_notice` 这个变量。需要确保：

```python
def get_prompt(
    self,
    category: str,          # "summary" 或 "segmentation"
    prompt_type: str,       # "overview", "key_points", "action_items", "segment"
    locale: str = "zh-CN",
    variables: Optional[dict] = None,   # 支持传入变量
    content_style: str = "meeting"
) -> dict:
    """获取提示词配置

    Returns:
        {
            "system": "系统角色描述",
            "user_prompt": "格式化后的用户提示词",
            "model_params": {...}
        }
    """
```

**实现要点：**
1. 加载 segmentation 类型的提示词
2. 根据content_style选择正确的template
3. 用variables中的值替换模板中的占位符（如`{quality_notice}`, `{transcript}`）

---

### 第二步：在LLM服务基类中添加方法

**文件：** `app/services/llm/base.py`

**需要添加的抽象方法：**

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

class LLMService(ABC):
    # ... 现有方法 ...

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> str:
        """通用文本生成

        用于章节划分等需要自定义prompt的场景

        Args:
            prompt: 完整的提示词
            temperature: 温度参数（可选）
            max_tokens: 最大token数（可选）

        Returns:
            str: 生成的文本
        """
        raise NotImplementedError
```

**然后在所有具体实现中添加此方法：**
- `app/services/llm/deepseek.py`
- `app/services/llm/qwen.py`
- `app/services/llm/doubao.py`
- `app/services/llm/moonshot.py`
- `app/services/llm/openrouter.py`

**实现示例（以DeepSeek为例）：**

```python
# app/services/llm/deepseek.py

async def generate(
    self,
    prompt: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None
) -> str:
    """通用文本生成"""

    url = f"{self._base_url}/chat/completions"

    payload = {
        "model": self._model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": temperature or self._temperature,
        "max_tokens": max_tokens or self._max_tokens,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"}
        )
        response.raise_for_status()
        data = response.json()

    return data["choices"][0]["message"]["content"]
```

---

### 第三步：修改 Worker 任务流程

**文件：** `worker/tasks/process_audio.py`

**当前生成摘要的代码位置：** 约750-842行

**需要修改为两阶段生成：**

```python
# ===== 阶段1：质量评估和预处理 =====
from app.utils.transcript_processor import TranscriptProcessor

# 评估转写质量
quality = TranscriptProcessor.assess_quality(segments)

logger.info(
    f"Task {task_id}: Transcript quality - "
    f"{quality.quality_score} (confidence: {quality.avg_confidence:.2f})"
)

# 预处理转写文本
preprocessed_text = TranscriptProcessor.preprocess(
    segments,
    filter_filler_words=True,
    merge_same_speaker=True,
    merge_threshold_seconds=2.0
)

# 生成质量提示
quality_notice = TranscriptProcessor.get_quality_notice(quality)

# ===== 阶段2：根据质量选择LLM服务 =====
if quality.quality_score == "low":
    # 低质量：使用更强的模型
    llm_service = await SmartFactory.get_service(
        "llm",
        provider="openrouter",
        model_id="anthropic/claude-3.5-sonnet"
    )
    logger.warning(
        f"Task {task_id}: Low quality transcript detected, using premium model"
    )
else:
    # 正常质量：使用常规服务
    llm_service = await SmartFactory.get_service("llm", provider=provider, model_id=model_id)

# ===== 阶段3：章节划分（新增） =====

# 注意：只对长内容（>2000字符）进行章节划分
if len(preprocessed_text) > 2000:
    try:
        # 获取章节划分提示词
        segmentation_prompt = get_prompt_manager().get_prompt(
            category="segmentation",
            prompt_type="segment",
            locale="zh-CN",
            variables={
                "transcript": preprocessed_text,
                "quality_notice": quality_notice
            },
            content_style=content_style
        )

        # 构建完整prompt（system + user）
        full_prompt = f"{segmentation_prompt['system']}\n\n{segmentation_prompt['user_prompt']}"

        # 调用LLM进行章节划分
        segmentation_result = await llm_service.generate(
            prompt=full_prompt,
            temperature=0.3,
            max_tokens=1500
        )

        # 解析JSON结果
        import json
        chapters_data = json.loads(segmentation_result)

        # 存储章节信息到数据库
        chapters_summary = Summary(
            task_id=task.id,
            summary_type="chapters",
            version=1,
            is_active=True,
            content=segmentation_result,  # 存储完整JSON
            model_used=llm_service.model_name,
            prompt_version="v1.2.0",
            token_count=len(segmentation_result)
        )
        session.add(chapters_summary)
        await session.commit()

        logger.info(
            f"Task {task_id}: Chapter segmentation completed - "
            f"{chapters_data['total_chapters']} chapters identified"
        )

    except Exception as e:
        logger.warning(
            f"Task {task_id}: Chapter segmentation failed: {e}",
            exc_info=True
        )
        # 章节划分失败不影响后续摘要生成
        chapters_data = None
else:
    # 短内容跳过章节划分
    chapters_data = None
    logger.info(
        f"Task {task_id}: Content too short ({len(preprocessed_text)} chars), "
        "skipping chapter segmentation"
    )

# ===== 阶段4：生成各类摘要（修改现有逻辑） =====

summaries = []
llm_usages = []

for summary_type in ("overview", "key_points", "action_items"):
    logger.info(
        f"Task {task_id}: Generating {summary_type} summary (style: {content_style})"
    )

    # 获取提示词（现在包含quality_notice和chapters支持）
    prompt_config = get_prompt_manager().get_prompt(
        category="summary",
        prompt_type=summary_type,
        locale="zh-CN",
        variables={
            "transcript": preprocessed_text,  # 使用预处理后的文本
            "quality_notice": quality_notice   # 质量提示
        },
        content_style=content_style
    )

    # 调用LLM生成摘要
    try:
        # 构建完整prompt
        system_role = prompt_config["system"]
        user_prompt = prompt_config["user_prompt"]

        # 根据LLM服务的实现方式调用
        if hasattr(llm_service, 'summarize'):
            # 如果LLM服务有summarize方法，使用现有逻辑
            content = await llm_service.summarize(preprocessed_text, summary_type, content_style)
        else:
            # 否则使用新的generate方法
            full_prompt = f"{system_role}\n\n{user_prompt}"
            content = await llm_service.generate(
                prompt=full_prompt,
                temperature=prompt_config["model_params"].get("temperature"),
                max_tokens=prompt_config["model_params"].get("max_tokens")
            )

        # 记录成功
        if llm_provider:
            input_tokens = len(preprocessed_text)
            output_tokens = len(content)
            estimated_cost = 0.0
            if hasattr(llm_service, "estimate_cost"):
                estimated_cost = llm_service.estimate_cost(input_tokens, output_tokens)

            llm_usages.append(
                LLMUsage(
                    user_id=str(task.user_id),
                    task_id=str(task.id),
                    provider=llm_provider,
                    model_id=llm_service.model_name,
                    call_type="summarize",
                    summary_type=summary_type,
                    status="success",
                )
            )

        logger.info(
            f"Task {task_id}: Generated {summary_type} summary ({len(content)} characters)"
        )

        summaries.append(
            Summary(
                task_id=task.id,
                summary_type=summary_type,
                version=1,
                is_active=True,
                content=content,
                model_used=llm_service.model_name,
                prompt_version="v1.2.0",
                token_count=len(content),
            )
        )

    except Exception as exc:
        logger.error(
            f"Task {task_id}: Failed to generate {summary_type} summary: {exc}",
            exc_info=True
        )
        if llm_provider:
            llm_usages.append(
                LLMUsage(
                    user_id=str(task.user_id),
                    task_id=str(task.id),
                    provider=llm_provider,
                    model_id=llm_service.model_name,
                    call_type="summarize",
                    summary_type=summary_type,
                    status="failed",
                )
            )
        # 继续处理其他summary_type

# 保存所有摘要
session.add_all(summaries)
if llm_usages:
    session.add_all(llm_usages)
await session.commit()

logger.info(
    f"Task {task_id}: All summaries saved to database",
    extra={"task_id": task_id, "summary_count": len(summaries)}
)
```

---

### 第四步：添加API端点获取章节信息

**文件：** `app/api/v1/summaries.py`

**新增端点：**

```python
@router.get("/{task_id}/chapters", response_model=dict)
async def get_task_chapters(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取任务的章节划分信息

    Returns:
        {
            "code": 0,
            "data": {
                "total_chapters": 3,
                "chapters": [...]
            }
        }
    """
    # 查询chapters类型的summary
    result = await db.execute(
        select(Summary).where(
            Summary.task_id == task_id,
            Summary.summary_type == "chapters",
            Summary.is_active == True,
            Summary.deleted_at.is_(None)
        )
    )
    chapters_summary = result.scalar_one_or_none()

    if not chapters_summary:
        return success(data=None, message="暂无章节划分信息")

    # 解析JSON
    import json
    chapters_data = json.loads(chapters_summary.content)

    return success(data=chapters_data)
```

---

## 测试计划 🧪

### 单元测试

**测试文件：** `tests/test_transcript_processor.py`

```python
import pytest
from app.services.asr.base import TranscriptSegment
from app.utils.transcript_processor import TranscriptProcessor, TranscriptQuality

def test_assess_quality_high():
    segments = [
        TranscriptSegment("speaker_0", 0.0, 3.0, "大家好", 0.95, None),
        TranscriptSegment("speaker_0", 3.0, 6.0, "今天我们讨论", 0.92, None),
    ]
    quality = TranscriptProcessor.assess_quality(segments)
    assert quality.quality_score == "high"
    assert quality.avg_confidence >= 0.8

def test_assess_quality_low():
    segments = [
        TranscriptSegment("speaker_0", 0.0, 3.0, "大家好", 0.45, None),
        TranscriptSegment("speaker_0", 3.0, 6.0, "今天我们讨论", 0.52, None),
    ]
    quality = TranscriptProcessor.assess_quality(segments)
    assert quality.quality_score == "low"
    assert quality.avg_confidence < 0.6

def test_preprocess_filter_filler_words():
    segments = [
        TranscriptSegment("speaker_0", 0.0, 1.0, "嗯", 0.65, None),
        TranscriptSegment("speaker_0", 1.0, 4.0, "大家好", 0.92, None),
        TranscriptSegment("speaker_0", 4.0, 5.0, "啊", 0.60, None),
    ]
    preprocessed = TranscriptProcessor.preprocess(segments)
    assert "嗯" not in preprocessed
    assert "啊" not in preprocessed
    assert "大家好" in preprocessed

def test_preprocess_merge_segments():
    segments = [
        TranscriptSegment("speaker_0", 0.0, 3.0, "大家好", 0.92, None),
        TranscriptSegment("speaker_0", 3.5, 6.0, "今天我们讨论", 0.90, None),
        TranscriptSegment("speaker_1", 6.5, 9.0, "好的", 0.88, None),
    ]
    preprocessed = TranscriptProcessor.preprocess(segments, merge_threshold_seconds=1.0)
    # speaker_0的两个segment应该被合并
    assert "[speaker_0] 大家好 今天我们讨论" in preprocessed
    assert "[speaker_1] 好的" in preprocessed
```

---

### 集成测试

1. **测试短内容（<2000字符）**
   - 应该跳过章节划分
   - 生成3种摘要

2. **测试中等内容（2000-5000字符）**
   - 应该生成2-3个章节
   - 生成4种内容（chapters + 3种摘要）

3. **测试长内容（>5000字符）**
   - 应该生成3-5个章节
   - 验证章节划分的合理性

4. **测试低质量转写**
   - 构造低置信度的segments
   - 验证是否使用了premium model
   - 验证quality_notice是否正确生成

5. **测试三种风格**
   - meeting: 验证输出包含决策、待办、遗留问题
   - lecture: 验证输出包含概念、知识点、学习重点
   - podcast: 验证输出包含观点、金句、启示

---

## 回滚方案 🔄

如果新版本出现问题，可以快速回滚：

```bash
cd /Users/sean/.claude-worktrees/ai-audio-assistant-web/amazing-hawking

# 回滚提示词
cd app/prompts/templates/summary
mv zh-CN.json zh-CN-v1.2-failed.json
mv zh-CN-v1.1-backup.json zh-CN.json

# 回滚config.json
git checkout app/prompts/templates/summary/config.json

# 删除新增的segmentation提示词
rm -rf app/prompts/templates/segmentation

# 删除transcript_processor
rm app/utils/transcript_processor.py

# 回滚worker代码
git checkout worker/tasks/process_audio.py
```

---

## 预期效果 📊

### 成本变化
- **正常质量（80%）：** 减少10-15%（过滤语气词）
- **低质量（20%）：** 增加约170%（使用premium model）
- **综合成本：** 增加约26%

### 质量提升
- ✅ 输出结构化程度显著提升
- ✅ 三种风格差异化明显
- ✅ 长内容通过章节化更易阅读
- ✅ 低质量转写的处理能力增强
- ✅ 表格、emoji等元素提升可读性

### Token消耗
- Overview: 500 → 1500 tokens (3x)
- Key Points: 800 → 1200 tokens (1.5x)
- Action Items: 600 → 1000 tokens (1.67x)
- **总输出token增加约2x**

---

## 下一步行动 ✈️

1. **立即实施**：完成剩余的4个步骤
2. **本地测试**：使用测试数据验证功能
3. **调优提示词**：根据测试结果调整
4. **发布到测试环境**
5. **收集用户反馈**
6. **迭代优化**

---

**文档版本：** V1.0
**创建日期：** 2025-01-15
**作者：** Claude Code
