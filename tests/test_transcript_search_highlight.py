"""转写搜索高亮(_highlight)纯函数单测。

背景:pg_jieba 1.1.1 的 parser 对部分中文 token 上报错误字节偏移,PG 的 ts_headline 包裹命中词
时会把该 multibyte token 整个删掉而非高亮(实证:'库克'→被删空、'谷歌'→正常,内容相关、不可靠)。
故弃用 ts_headline,改在应用层按用户查询词的字面子串高亮(转写段是短句,整段作 snippet)。
本测固定该高亮函数行为。
"""

from __future__ import annotations

from app.services.transcript_search import _highlight


def test_highlights_query_term_at_start() -> None:
    # 正是用户报的回归:'库克' 在句首,ts_headline 会把它删掉,应用层须正确高亮。
    assert _highlight("库克本人没有出席", "库克") == "<mark>库克</mark>本人没有出席"


def test_highlights_term_in_middle() -> None:
    assert _highlight("正在失去库克和高层的信任", "库克") == "正在失去<mark>库克</mark>和高层的信任"


def test_highlights_all_occurrences() -> None:
    assert _highlight("谷歌搜索谷歌浏览器", "谷歌") == "<mark>谷歌</mark>搜索<mark>谷歌</mark>浏览器"


def test_multi_term_query_highlights_each_term() -> None:
    out = _highlight("库克擅长供应链整合", "库克 供应链")
    assert out == "<mark>库克</mark>擅长<mark>供应链</mark>整合"


def test_no_occurrence_returns_content_unchanged() -> None:
    # jieba 可能命中字面不出现的情形(分词差异);此时仍返回该段(命中有效),只是不高亮。
    assert _highlight("由当时的COO主持", "库克") == "由当时的COO主持"


def test_case_insensitive_for_ascii() -> None:
    # ASCII 大小写不敏感,且保留原文大小写。
    assert _highlight("Tim COOK spoke", "cook") == "Tim <mark>COOK</mark> spoke"


def test_escapes_regex_metacharacters_in_query() -> None:
    # 查询里的正则元字符须按字面匹配,不能当模式(否则 '.' 会通配、'(' 会语法错)。
    assert _highlight("版本 a.b 发布", "a.b") == "版本 <mark>a.b</mark> 发布"
    # '.' 不应通配:'axb' 不被 'a.b' 命中
    assert _highlight("版本 axb 发布", "a.b") == "版本 axb 发布"


def test_blank_query_returns_content_unchanged() -> None:
    assert _highlight("任意内容", "   ") == "任意内容"
