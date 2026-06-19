"""溯源 PR6:把已落库的溯源字段透出到 API 响应(普通响应只放非敏感项,token 仅管理员)。

钉住三件事:
1. _to_summary_item 把 Summary.prompt_slug / quality_tier 透出到 SummaryItem;
2. SummaryItem 刻意**不**暴露 input_tokens / output_tokens(产品决策:成本/token 仅管理员);
3. TaskService.get_task_detail 把 Task.asr_provider/asr_engine/asr_variant/llm_provider 透出;
4. 管理员 token-usage 端点的纯构造器对各摘要 token 求和。
不起真实 DB(mock session;纯函数/纯构造器)。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import app.api.v1.summaries as summaries_mod
from app.schemas.summary import SummaryItem
from app.services.task_service import TaskService


def _fake_summary(**over) -> SimpleNamespace:
    base = dict(
        id="s1",
        summary_type="overview",
        version=1,
        is_active=True,
        content="正文",
        model_used="chat-default",
        prompt_version="1.2.0",
        token_count=42,
        created_at=datetime(2026, 1, 1),
        visual_format=None,
        image_model_used=None,
        images=None,
        prompt_slug="summary-overview-zh",
        quality_tier="high",
        input_tokens=999,
        output_tokens=42,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeResult:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def scalar_one_or_none(self) -> object:
        return self._obj


class _FakeSession:
    """只回放一个预置 Task,忽略 stmt —— 用于隔离 get_task_detail 的字段映射。"""

    def __init__(self, task: object) -> None:
        self._task = task

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self._task)


def test_to_summary_item_exposes_prompt_slug_and_quality_tier() -> None:
    item = summaries_mod._to_summary_item(_fake_summary(), None)
    assert item.prompt_slug == "summary-overview-zh"
    assert item.quality_tier == "high"
    # PR3/PR5 已有项不回退
    assert item.model_used == "chat-default"
    assert item.prompt_version == "1.2.0"


def test_summary_item_does_not_expose_raw_tokens() -> None:
    # 产品决策:input/output token 间接泄 prompt 长度/成本结构,只给管理员端点,不进普通响应。
    assert "input_tokens" not in SummaryItem.model_fields
    assert "output_tokens" not in SummaryItem.model_fields


async def test_get_task_detail_exposes_asr_and_llm_provenance() -> None:
    task = SimpleNamespace(
        id="t1",
        title="T",
        source_type="upload",
        source_key=None,
        source_url=None,
        status="completed",
        progress=100,
        stage=None,
        duration_seconds=10,
        detected_language="zh",
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
        error_message=None,
        stages=[],
        options=None,
        is_public=False,
        published_at=None,
        asr_provider="tencent",
        asr_engine="16k_zh",
        asr_variant="file",
        llm_provider="proxy",
    )
    user = SimpleNamespace(id="u1", email="e@x", scopes=[])
    resp = await TaskService.get_task_detail(_FakeSession(task), user, "t1")
    assert resp.asr_provider == "tencent"
    assert resp.asr_engine == "16k_zh"
    assert resp.asr_variant == "file"
    assert resp.llm_provider == "proxy"


def test_token_usage_builder_sums_and_maps() -> None:
    summaries = [
        _fake_summary(summary_type="overview", input_tokens=1000, output_tokens=100),
        _fake_summary(summary_type="key_points", input_tokens=500, output_tokens=50),
        _fake_summary(summary_type="action_items", input_tokens=None, output_tokens=None),
    ]
    resp = summaries_mod._build_token_usage_response("t1", summaries)
    assert resp.task_id == "t1"
    assert resp.total == 3
    assert resp.total_input_tokens == 1500  # None 视作 0
    assert resp.total_output_tokens == 150
    assert resp.items[0].summary_type == "overview"
    assert resp.items[0].input_tokens == 1000
    assert resp.items[0].model_used == "chat-default"
