"""Tests for strip_markdown_fence.

Ported 1:1 from the frontend edge-defense tests
(ai-audio-assistant-ui src/lib/markdown-fence.test.ts) so the source-side
cleanup and the render-side defense provably share one behavior, plus a couple
of Python-side cases (None input, key_points list body).
"""

from app.services.summary.markdown_fence import strip_markdown_fence

F = "`" * 3  # ```


def test_unwraps_whole_document_markdown_fence():
    inner = "## 标题\n\n正文段落\n\n{{IMAGE: timeline | 标题 | a, b}}"
    wrapped = f"{F}markdown\n{inner}\n{F}"
    assert strip_markdown_fence(wrapped) == inner


def test_unwraps_whole_document_bare_fence():
    inner = "## 标题\n\n正文"
    assert strip_markdown_fence(f"{F}\n{inner}\n{F}") == inner


def test_unwraps_md_fence():
    inner = "正文"
    assert strip_markdown_fence(f"{F}md\n{inner}\n{F}") == inner


def test_tolerates_surrounding_whitespace_and_blank_lines():
    inner = "## 标题\n\n正文"
    wrapped = f"\n\n  {F}markdown\n{inner}\n{F}  \n\n"
    assert strip_markdown_fence(wrapped) == inner


def test_leaves_plain_markdown_unchanged():
    plain = "## 标题\n\n正文\n\n{{IMAGE: a | b | c}}"
    assert strip_markdown_fence(plain) == plain


def test_does_not_unwrap_partial_code_block():
    doc = f"这是一段教程摘要：\n\n{F}python\nprint(1)\n{F}\n\n继续正文"
    assert strip_markdown_fence(doc) == doc


def test_does_not_strip_whole_doc_python_code_block():
    doc = f"{F}python\nprint(1)\n{F}"
    assert strip_markdown_fence(doc) == doc


def test_does_not_unwrap_when_body_has_inner_fence():
    doc = f"{F}markdown\n## 标题\n\n{F}js\nx\n{F}\n\n正文\n{F}"
    assert strip_markdown_fence(doc) == doc


def test_empty_input_unchanged():
    assert strip_markdown_fence("") == ""


def test_none_input_unchanged():
    # Worker content is always str, but guard against a falsy/None slipping through.
    assert strip_markdown_fence(None) is None


def test_preserves_image_pipe_placeholder_verbatim():
    ph = "{{IMAGE: timeline | 雷军早期创业 | 三色公司倒闭, 盘古惨败}}"
    wrapped = f"{F}markdown\n## 标题\n\n{ph}\n{F}"
    assert ph in strip_markdown_fence(wrapped)


def test_unwraps_key_points_list_body():
    # key_points / action_items are prose lists too; same wrapper bug applies.
    inner = "- 要点一\n- 要点二\n- 要点三"
    assert strip_markdown_fence(f"{F}markdown\n{inner}\n{F}") == inner


def test_uppercase_info_string_is_unwrapped():
    inner = "正文"
    assert strip_markdown_fence(f"{F}MARKDOWN\n{inner}\n{F}") == inner
