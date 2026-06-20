"""转写全文搜索:pg_jieba 中文分词 FTS,跨用户隔离的「哪个视频提到 X + 跳时间戳」。

索引侧(idx_transcripts_fts)与查询侧都用 jiebacfg 配置,二者一致 planner 才能用上 GIN 索引。
查询用 websearch_to_tsquery 而非 to_tsquery —— 前者容忍任意用户自由文本(不会因特殊字符抛
语法错误),适合面向用户的搜索框。
"""

from __future__ import annotations

from sqlalchemy import Select, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.models.transcript import Transcript
from app.schemas.task import TaskSearchHit

# 配置名作为 SQL 常量逐字输出(literal_column),而非 bind 参数:to_tsvector 的首参类型是
# regconfig,作 bind 时 literal_binds 编译无渲染器会报错;且它是固定常量非用户输入,无注入风险。
# 用户查询词仍走 bind 参数(parameterized,防注入)。
_TS_CONFIG = literal_column("'jiebacfg'")
# 片段高亮:<mark> 包裹命中词,单片段、限定词数,前端可直接渲染。
_HEADLINE_OPTS = "StartSel=<mark>,StopSel=</mark>,MaxFragments=1,MaxWords=24,MinWords=6"


def build_search_statement(user_id: str, query: str, limit: int) -> Select:
    """构造 FTS 查询语句(纯函数,便于按 SQL 形状单测)。``query`` 须为已 strip 的非空串。"""
    tsv = func.to_tsvector(_TS_CONFIG, Transcript.content)
    tsq = func.websearch_to_tsquery(_TS_CONFIG, query)
    rank = func.ts_rank(tsv, tsq)
    snippet = func.ts_headline(_TS_CONFIG, Transcript.content, tsq, _HEADLINE_OPTS)
    return (
        select(
            Transcript.task_id.label("task_id"),
            Task.title.label("title"),
            Transcript.start_time.label("start_time"),
            snippet.label("snippet"),
            rank.label("rank"),
        )
        .join(Task, Task.id == Transcript.task_id)
        # 转写无软删列(随任务硬删 CASCADE),仅按任务的 user_id + 软删过滤即可排除软删任务的转写。
        .where(
            Task.user_id == user_id,
            Task.deleted_at.is_(None),
            tsv.op("@@")(tsq),
        )
        .order_by(rank.desc(), Transcript.start_time.asc())
        .limit(limit)
    )


async def search(db: AsyncSession, user_id: str, query: str, limit: int) -> list[TaskSearchHit]:
    """返回当前用户命中转写段(按相关性降序)。空白查询直接返回空,不打 DB。"""
    q = query.strip()
    if not q:
        return []
    result = await db.execute(build_search_statement(user_id, q, limit))
    return [
        TaskSearchHit(
            task_id=str(row.task_id),
            title=row.title,
            snippet=row.snippet,
            start_time=float(row.start_time),
            rank=float(row.rank),
        )
        for row in result
    ]
