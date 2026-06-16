"""Strip a leading courtesy/meta preamble sentence from a prose summary.

LLMs occasionally leak a conversational opener into the persisted summary
body — e.g. ``好的，这是为您生成的课程概览摘要。`` or
``Sure, here's the summary you requested.`` — instead of starting with the
real prose. The opener is unstable: the same task may have it on overview /
action_items but not on key_points. Persisted into ``Summary.content`` it
pollutes every surface (list-card excerpts take the first chars of content;
public/private detail pages render content verbatim).

``strip_summary_preamble`` removes only that first opener sentence — surgically,
under four conservative constraints so legitimate prose is never deleted
(``宁可漏剥也别误删正文``):

1. Only the very first line / paragraph is inspected (anchored at ``^``); the
   opener is removed once, never looping into the body.
2. The opener must START with a pure courtesy word (``好的``/``当然``/``Sure``/…)
   IMMEDIATELY FOLLOWED BY an interjection separator (comma / colon / space) —
   ``好的，`` / ``Sure,`` — so fused prose like ``好评如潮的…总结。`` / ``好的总结需要
   打磨。`` (no separator after the courtesy chars) is never matched. A bare
   ``这是``/``以下是`` is excluded too (it can begin legitimate prose).
3. The opener sentence must ALSO contain a service-y phrase (``为您``/``根据您``/…)
   or a meta noun (``摘要``/``要点``/``待办``/summary/…) — double-hit required.
4. The opener must end with terminal punctuation (``。.：:！!``) reached before
   the first newline, AND the remaining body must be non-empty after stripping;
   otherwise nothing is removed.

Mirrors the conservative spirit of ``strip_markdown_fence``: when any condition
is unmet the content is returned verbatim.
"""

from __future__ import annotations

import re

# 1) 纯客套开场词(去前导空白后必须以此起头)。中英;英文大小写不敏感。
#    注意:`这是`/`以下是` 不在此列(否则会误删「这是一个关于摘要算法的讲座。」这类正文),
#    它们只在跟客套词之后、作为开场白句的一部分时才算。
_COURTESY = (
    r"好的|好|当然可以|当然|没问题"
    r"|Sure|Certainly|Of\s+course|Okay|OK"
)

# 3) 服务化措辞 / 元描述名词(开场白句内必须含其一)。
_SERVICE_OR_META = (
    # 服务化措辞
    r"为您|为你|给您|帮您|根据您|根据你|您要的|你要的|您需要|以下是|这是为"
    r"|for\s+you|you\s+requested|here'?s|here\s+is|based\s+on"
    # 元描述名词
    r"|摘要|概览|要点|关键点|待办|行动|总结"
    r"|summary|key\s*point|action\s*item"
)

# 2)+4) 开场白首句:行首(允许前导空白)→ 客套词起头 → 句子主体(不跨换行,
#      非空)→ 句末标点收尾。``[^\n。.：:！!]*`` 保证只吃首行、遇换行即止,
#      故「首行无句末标点、被换行截断」不命中(满足安全用例)。
_PREAMBLE_RE = re.compile(
    r"^[ \t\r\n]*"  # 前导空白/空行
    r"(?:" + _COURTESY + r")"  # 客套开场词起头
    r"[ \t，,、：:！!]"  # 客套词后必须紧跟分隔符(逗号/冒号/空白),排除「好评/好处/好的总结」词头融合的正文
    r"[^\n。.：:！!]*?"  # 同句其余字符(不跨行,惰性)
    r"(?:" + _SERVICE_OR_META + r")"  # 服务化/元描述标记(同句内)
    r"[^\n。.：:！!]*?"  # 标记之后到句末的字符(不跨行)
    r"[。.：:！!]"  # 句末标点
    r"[ \t\r\n]*",  # 开场白后的空白/空行(正文从下一非空行开始)
    re.IGNORECASE,
)


def strip_summary_preamble(content: str | None) -> str:
    """Return ``content`` with a leading courtesy/meta preamble sentence removed.

    Removes at most one opener and only when all four constraints hold; otherwise
    returns ``content`` unchanged (``None`` / falsy → ``""``). Pure local regex,
    no IO.
    """
    if not content:
        return ""

    match = _PREAMBLE_RE.match(content)
    if not match:
        return content

    remainder = content[match.end() :]
    # 4) 剥完之后剩余正文非空,才真正剥(防止把「整条就一句」误清空)。
    if not remainder.strip():
        return content

    return remainder
