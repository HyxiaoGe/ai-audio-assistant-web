"""转写全文搜索:pg_jieba 中文分词 FTS,跨用户隔离的「哪个视频提到 X + 跳时间戳」。

索引侧(idx_transcripts_fts)与查询侧都用 jiebacfg 配置,二者一致 planner 才能用上 GIN 索引。
查询用 websearch_to_tsquery 而非 to_tsquery —— 前者容忍任意用户自由文本(不会因特殊字符抛
语法错误),适合面向用户的搜索框。

高亮**不用** PG 的 ts_headline:pg_jieba 1.1.1 的 parser 对部分中文 token 上报错误的字节偏移,
ts_headline 在包裹命中词时会把该 multibyte token 整个删掉而非高亮(dev 实证:'库克' 被删空、
'谷歌' 正常,内容相关、不可靠;ASCII token 不受影响)。转写段是短句(dev 实测 p95≈34 字符),
故 DB 直接返回整段 content,高亮在应用层按用户查询词的字面子串做(见 _highlight)。前端
SearchSnippet 用带捕获组的 split 安全渲染 <mark>(其余正文 React 自动转义),杜绝 XSS。
"""

from __future__ import annotations

import re

from sqlalchemy import Select, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.models.transcript import Transcript
from app.schemas.task import TaskSearchHit

# 配置名作为 SQL 常量逐字输出(literal_column),而非 bind 参数:to_tsvector 的首参类型是
# regconfig,作 bind 时 literal_binds 编译无渲染器会报错;且它是固定常量非用户输入,无注入风险。
# 用户查询词仍走 bind 参数(parameterized,防注入)。
_TS_CONFIG = literal_column("'jiebacfg'")


def build_search_statement(user_id: str, query: str, limit: int) -> Select:
    """构造 FTS 查询语句(纯函数,便于按 SQL 形状单测)。``query`` 须为已 strip 的非空串。"""
    tsv = func.to_tsvector(_TS_CONFIG, Transcript.content)
    tsq = func.websearch_to_tsquery(_TS_CONFIG, query)
    rank = func.ts_rank(tsv, tsq)
    return (
        select(
            Transcript.task_id.label("task_id"),
            Task.title.label("title"),
            Transcript.start_time.label("start_time"),
            # 返回原始整段 content(短句),高亮在应用层做(_highlight),不依赖 ts_headline。
            Transcript.content.label("content"),
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


def _highlight(content: str, query: str) -> str:
    """在 content 中用 <mark> 包裹用户查询词的字面出现(大小写不敏感,保留原文大小写)。

    替代 ts_headline(pg_jieba 字节偏移 bug 会删命中词,见模块 docstring)。按空白切分查询为
    词组,每词在原文做字面子串匹配(re.escape 防查询里的正则元字符被当模式);长词优先排序避免
    短词先匹配切断长词。jiebacfg 分词与字面子串口径可能不一致(命中段落里查询词未必逐字出现),
    此时返回原段不高亮 —— 命中仍有效,只是该段无可高亮处。
    """
    terms = [t for t in query.split() if t]
    if not terms:
        return content
    pattern = "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))
    return re.sub(f"({pattern})", r"<mark>\1</mark>", content, flags=re.IGNORECASE)


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
            snippet=_highlight(row.content, q),
            start_time=float(row.start_time),
            rank=float(row.rank),
        )
        for row in result
    ]
