"""Tests for summary_excerpt(摘要正文 → 单行纯文本摘录)。"""

from app.services.summary.excerpt import summary_excerpt

F = "`" * 3  # ```


def test_strips_heading_and_image_placeholder():
    # 与 _make_image_summary 夹具同款正文:标题 + 配图占位锚点
    assert summary_excerpt("# 摘要\n{{IMAGE:concept|图1|关键词}}") == "摘要"


def test_strips_bold_and_inline_marks():
    assert summary_excerpt("**重点**说明 `code`") == "重点说明 code"


def test_strips_list_bullets_and_joins_lines():
    assert summary_excerpt("- 第一点\n- 第二点\n- 第三点") == "第一点 第二点 第三点"


def test_keeps_link_text_drops_url():
    assert summary_excerpt("详见 [官网](https://example.com) 说明") == "详见 官网 说明"


def test_strips_old_and_single_brace_placeholders():
    assert summary_excerpt("前文 {{IMAGE: 描述}} 中段 {IMAGE: 类型|描述|关键}} 后文").startswith("前文")
    assert "IMAGE" not in summary_excerpt("前文 {{IMAGE: 描述}} 中段 {IMAGE: 类型|描述|关键}} 后文")


def test_unwraps_whole_doc_markdown_fence_first():
    assert summary_excerpt(f"{F}markdown\n正文内容\n{F}") == "正文内容"


def test_truncates_with_ellipsis():
    out = summary_excerpt("啊" * 100, max_length=80)
    assert len(out) == 80
    assert out.endswith("…")


def test_empty_and_none_return_empty_string():
    assert summary_excerpt("") == ""
    assert summary_excerpt(None) == ""
    assert summary_excerpt("   \n  ") == ""
