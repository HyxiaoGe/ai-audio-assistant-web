"""Strip a whole-content markdown code fence wrapper from prose summaries.

LLMs occasionally return an entire prose summary wrapped in a `````markdown ...
```` code fence, and the worker persists it verbatim. Rendered downstream by
react-markdown, the whole block becomes a single fenced code block: ``{{IMAGE:
..}}`` placeholders never reach the paragraph renderer (images don't show) and
headings/bold degrade to literal text.

``strip_markdown_fence`` removes only that single outer wrapper at the content
boundary — surgically, never touching legitimate code blocks inside the prose.
It mirrors the frontend edge defense in ai-audio-assistant-ui
``src/lib/markdown-fence.ts`` so the source-side cleanup and the render-side
defense agree exactly.
"""

from __future__ import annotations

import re

_FENCE = "```"
# Opening fence line: empty info string or ``markdown``/``md`` (case-insensitive).
# ``` ```python ``` and other real languages are intentionally left untouched.
_OPEN_FENCE_RE = re.compile(r"^`{3}(markdown|md)?\s*$", re.IGNORECASE)
# Closing fence line: a bare ``` (optional trailing whitespace).
_CLOSE_FENCE_RE = re.compile(r"^`{3}\s*$")
# Any fence start, used to detect a nested fence inside the body.
_ANY_FENCE_RE = re.compile(r"^`{3}")


def strip_markdown_fence(content: str | None) -> str | None:
    """Return the inner text if ``content`` is wrapped in a single outer
    ``markdown`` (or bare) code fence; otherwise return ``content`` unchanged.

    The unwrap requires all of:

    - the first non-empty line is an opening fence (info empty or ``markdown``/``md``);
    - the last non-empty line is a closing bare fence;
    - no other fence line appears between them.

    The last condition guarantees a single whole-content wrapper rather than
    prose that legitimately contains a code block. If any condition is unmet the
    content is returned verbatim (conservative: a real code block is never
    mangled, at the cost of leaving a genuinely-ambiguous wrapper in place).
    """
    if not content or _FENCE not in content:
        return content

    lines = content.split("\n")

    first = 0
    while first < len(lines) and lines[first].strip() == "":
        first += 1
    last = len(lines) - 1
    while last >= 0 and lines[last].strip() == "":
        last -= 1

    # Need the opening and closing fences on different lines.
    if first >= last:
        return content

    if not _OPEN_FENCE_RE.match(lines[first].strip()):
        return content
    if not _CLOSE_FENCE_RE.match(lines[last].strip()):
        return content

    # A fence line inside the body means a nested code block: not a safe single wrapper.
    for i in range(first + 1, last):
        if _ANY_FENCE_RE.match(lines[i].strip()):
            return content

    return "\n".join(lines[first + 1 : last]).strip()
