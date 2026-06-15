from __future__ import annotations

import re

from app.services.summary.markdown_fence import strip_markdown_fence

# content 永久保留的配图占位锚点:{{IMAGE: t|d|k}} / {IMAGE: t|d|k} / 旧式 {{IMAGE: d}} / {IMAGE: d}
_IMAGE_PLACEHOLDER_RE = re.compile(r"\{\{IMAGE:[^{}]*\}\}|\{IMAGE:[^{}]*\}")
# markdown 图片语法 ![alt](url)(占位被替换后可能出现)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# markdown 链接 [text](url):保留可见文本,丢 URL
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# 行首标题/引用标记 + 行内强调/删除线/代码标记
_MD_MARKS_RE = re.compile(r"^#{1,6}\s*|^>\s*|\*\*|__|~~|`", re.MULTILINE)
# 行首无序/有序列表前缀
_LIST_PREFIX_RE = re.compile(r"^\s*[-*+]\s+|^\s*\d+[.)]\s+", re.MULTILINE)


def summary_excerpt(content: str | None, max_length: int = 80) -> str:
    """摘要正文 → 单行纯文本摘录:剥围栏/图片占位/markdown 标记,折叠空白后截断。

    无可用文本返回空串(调用方据此回落 None)。纯本地正则,无 IO。
    """
    if not content:
        return ""
    text = strip_markdown_fence(content) or ""
    text = _IMAGE_PLACEHOLDER_RE.sub("", text)
    text = _MD_IMAGE_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_MARKS_RE.sub("", text)
    text = _LIST_PREFIX_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" :：,，.。;；|-#")
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"
