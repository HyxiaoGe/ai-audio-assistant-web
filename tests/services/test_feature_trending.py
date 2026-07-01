"""flags.get_curated_trending_queries 单元测试。

按 kill-switch DOA 教训:测**真实数据流那道缝**——喂 service_configs 行 config jsonb 列的
标量(scalar_one_or_none 的返回),而非在 ConfigManager 内存层播种。reader 就是这样读的。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.services.feature.configs import CuratedTrendingItem
from app.services.feature.flags import get_curated_trending_queries


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _Session:
    """最小假会话:execute 恒返给定 config jsonb 标量(模拟单行 config 列查询)。"""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def execute(self, _stmt: object) -> _Result:
        return _Result(self._value)


async def test_no_config_row_returns_none() -> None:
    # 无 feature/discover_trending 配置行 → None → 调用方回落组织化
    assert await get_curated_trending_queries(_Session(None)) is None


async def test_replace_mode_returns_queries_in_config_order() -> None:
    cfg = {"mode": "replace", "items": [{"query": "大模型"}, {"query": "Claude Code"}, {"query": "OpenAI"}]}
    assert await get_curated_trending_queries(_Session(cfg)) == ["大模型", "Claude Code", "OpenAI"]


async def test_empty_items_returns_none() -> None:
    # mode 对但 items 空 → 视同未配置,回落组织化
    assert await get_curated_trending_queries(_Session({"mode": "replace", "items": []})) is None


async def test_non_replace_mode_returns_none() -> None:
    # v1 只支持 replace;其它 mode(如未来的 pin)当前当作未启用覆盖
    cfg = {"mode": "pin", "items": [{"query": "大模型"}]}
    assert await get_curated_trending_queries(_Session(cfg)) is None


async def test_missing_mode_returns_none() -> None:
    assert await get_curated_trending_queries(_Session({"items": [{"query": "大模型"}]})) is None


async def test_blank_and_malformed_items_are_skipped() -> None:
    # 空白 query、缺 query、query=None 都跳过;只保留有效项
    cfg = {
        "mode": "replace",
        "items": [{"query": "  "}, {"query": "OpenAI"}, {}, {"query": None}, {"query": "Anthropic"}],
    }
    assert await get_curated_trending_queries(_Session(cfg)) == ["OpenAI", "Anthropic"]


async def test_all_invalid_items_returns_none() -> None:
    cfg = {"mode": "replace", "items": [{"query": "   "}, {}, {"query": None}]}
    assert await get_curated_trending_queries(_Session(cfg)) is None


async def test_non_dict_config_returns_none() -> None:
    # config 列若不是 dict(脏数据)→ 别 .get() 崩,回落 None
    assert await get_curated_trending_queries(_Session("garbage")) is None


async def test_non_list_items_returns_none() -> None:
    # items 是字符串(直写脏数据)→ 绝不逐字符迭代出逐字垃圾词,回落 None
    assert await get_curated_trending_queries(_Session({"mode": "replace", "items": "大模型"})) is None


async def test_mixed_garbage_items_do_not_crash() -> None:
    # 裸字符串条目宽松接受;数字/None/空白/嵌套都安全跳过,不抛
    cfg = {"mode": "replace", "items": ["大模型", "  ", 123, None, ["x"], {"query": "OpenAI"}]}
    assert await get_curated_trending_queries(_Session(cfg)) == ["大模型", "OpenAI"]


async def test_read_error_returns_none() -> None:
    class _Boom:
        async def execute(self, _stmt: object) -> _Result:
            raise RuntimeError("db down")

    # 读错兜 None(fail-safe 回落组织化),不抛
    assert await get_curated_trending_queries(_Boom()) is None


class _CapturingSession:
    """记录传入 execute 的 statement,用于断言查询打到正确的列与谓词。"""

    def __init__(self, value: Any) -> None:
        self._value = value
        self.stmt: Any = None

    async def execute(self, stmt: object) -> _Result:
        self.stmt = stmt
        return _Result(self._value)


async def test_reader_targets_config_column_with_correct_predicate() -> None:
    """守住 kill-switch DOA 那道缝:确认 reader 真的查 config 列(不是 enabled)、
    provider='discover_trending'、service_type='feature'、owner_user_id IS NULL(全局行)。

    _Session 忽略 statement 的其它测试证不了这点——查错列/错 provider 也会全绿。这里编译出
    带字面量的 SQL 直接断言,任何"读错字段"式回归都会在此炸出来。
    """
    session = _CapturingSession({"mode": "replace", "items": [{"query": "大模型"}]})
    assert await get_curated_trending_queries(session) == ["大模型"]

    sql = str(session.stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "service_configs.config" in sql  # 选的是 config jsonb 列,不是 enabled
    assert "'discover_trending'" in sql  # 查的是精选 provider,不是 kill-switch 的 'discover'
    assert "'feature'" in sql
    assert "service_configs.owner_user_id IS NULL" in sql  # 全局行(非按用户)


def test_schema_rejects_empty_or_blank_query() -> None:
    # config 端点 PUT 空/全空白 query 直接拒(不存死条目)
    with pytest.raises(ValidationError):
        CuratedTrendingItem(query="")
    with pytest.raises(ValidationError):
        CuratedTrendingItem(query="   ")


def test_schema_strips_query() -> None:
    assert CuratedTrendingItem(query="  大模型  ").query == "大模型"
