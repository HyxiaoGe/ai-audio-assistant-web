"""「我的频道」全局搜索:get_cached_subscriptions 的 search 参数 SQL 形状契约。

裸 service + 假 session,用 postgres 方言编译断言(与 test_task_search_api 同风格):
- 带 search:count 与结果两条 SQL 都追加 channel_title/description 的 ILIKE、都 scope user。
- 无/空白 search:不加 ILIKE(退化为原分页)。
- LIKE 通配 %/_ 被转义(镜像 task_service 既有写法),避免用户输入被当通配。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects import postgresql

from app.services.youtube.subscription_service import YouTubeSubscriptionService

_USER = "11111111-1111-1111-1111-111111111111"


def _csql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()


class _CountResult:
    def __init__(self, n: int) -> None:
        self._n = n

    def scalar(self) -> int:
        return self._n


class _ScalarsResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarsResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """第 1 次 execute 当 count 查询,第 2 次当结果查询;记录每条编译后的 SQL。"""

    def __init__(self, count: int = 0, rows: list[Any] | None = None) -> None:
        self.count = count
        self.rows = rows or []
        self.sqls: list[str] = []
        self.params: list[dict[str, Any]] = []
        self._calls = 0

    async def execute(self, stmt: Any) -> Any:
        self.sqls.append(_csql(stmt))
        # 绑定参数原值(不经 literal_binds 的 %-doubling / 反斜杠字面转义),用于干净断言转义后的模式串
        self.params.append(dict(stmt.compile(dialect=postgresql.dialect()).params))
        self._calls += 1
        return _CountResult(self.count) if self._calls == 1 else _ScalarsResult(self.rows)


async def test_search_adds_ilike_on_title_and_description_scoped_to_user() -> None:
    svc = YouTubeSubscriptionService()
    session = _FakeSession(count=0, rows=[])
    await svc.get_cached_subscriptions(session, _USER, search="tech")

    assert len(session.sqls) == 2
    count_sql, result_sql = session.sqls[0], session.sqls[1]
    for sql in (count_sql, result_sql):
        # 用户隔离两条都在
        assert "youtube_subscriptions.user_id =" in sql
        # 标题 + 简介都 ILIKE,搜索词进模式串
        assert "channel_title ilike" in sql
        assert "channel_description ilike" in sql
        assert "%tech%" in sql
    # 结果查询保留排序/分页
    assert "order by" in result_sql
    assert "limit" in result_sql


async def test_no_search_has_no_ilike() -> None:
    svc = YouTubeSubscriptionService()
    session = _FakeSession()
    await svc.get_cached_subscriptions(session, _USER)
    for sql in session.sqls:
        assert "ilike" not in sql


async def test_blank_search_treated_as_no_search() -> None:
    svc = YouTubeSubscriptionService()
    session = _FakeSession()
    await svc.get_cached_subscriptions(session, _USER, search="   ")
    for sql in session.sqls:
        assert "ilike" not in sql


async def test_search_escapes_like_wildcards() -> None:
    svc = YouTubeSubscriptionService()
    session = _FakeSession()
    await svc.get_cached_subscriptions(session, _USER, search="50%_off")
    # 断言绑定参数原值(避开 literal_binds 渲染的 %-doubling / 反斜杠字面转义):
    # 用户输入的 % _ 已被反斜杠转义,配合 escape='\\' 后不再当通配。
    result_patterns = [v for v in session.params[1].values() if isinstance(v, str) and "50" in v]
    assert result_patterns, f"未找到搜索模式绑定参数:{session.params[1]}"
    assert "%50\\%\\_off%" in result_patterns
    # ESCAPE 子句在编译 SQL 中存在
    assert "escape" in session.sqls[1]
