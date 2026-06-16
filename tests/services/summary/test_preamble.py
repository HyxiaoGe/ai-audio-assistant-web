"""Tests for strip_summary_preamble(剥摘要正文最开头的客套/元描述开场白)。

开场白是 LLM 不稳定逸出:同任务 overview/action_items 偶有、key_points 没有。
正则四重约束(纯客套词起头 + 服务化/元描述标记 + 句末标点 + 剥完非空),
宁可漏剥也不误删正文——下方"不应剥"用例钉死安全边界。
"""

from app.services.summary.preamble import strip_summary_preamble

# ----------------------------------------------------------------------------
# 应剥(strip):客套开场词 + 服务化/元描述标记 双重命中,且剥完仍有正文
# ----------------------------------------------------------------------------


def test_strip_overview_preamble_with_blank_line():
    src = "好的，这是为您生成的课程概览摘要。\n\n# GDP的真相\n\n## 导语\n正文"
    assert strip_summary_preamble(src) == "# GDP的真相\n\n## 导语\n正文"


def test_strip_action_items_long_preamble():
    src = "好的，这是根据您提供的讲座转写文本提取出的待办事项、行动项和未解决问题。\n\n# 待办事项与行动计划\n- a"
    assert strip_summary_preamble(src) == "# 待办事项与行动计划\n- a"


def test_strip_preamble_ending_with_colon():
    src = "好的，以下是为您整理的关键要点：\n\n- 要点1\n- 要点2"
    assert strip_summary_preamble(src) == "- 要点1\n- 要点2"


def test_strip_preamble_single_newline():
    src = "当然，这是您要的会议总结。\n正文第一段"
    assert strip_summary_preamble(src) == "正文第一段"


def test_strip_english_preamble_case_insensitive():
    src = "Sure, here's the summary you requested.\n\n# Title\nbody"
    assert strip_summary_preamble(src) == "# Title\nbody"


def test_strip_preamble_halfwidth_comma():
    src = "好的,这是为您生成的摘要。\n\n正文"
    assert strip_summary_preamble(src) == "正文"


def test_strip_preamble_with_leading_whitespace():
    src = "\n  好的，这是为您生成的摘要。\n\n正文"
    assert strip_summary_preamble(src) == "正文"


# ----------------------------------------------------------------------------
# 不应剥(原样返回):安全边界
# ----------------------------------------------------------------------------


def test_keep_keypoints_list_body():
    # key_points 列表开头,无客套词
    src = "- **GDP的本质**…\n…"
    assert strip_summary_preamble(src) == src


def test_keep_when_preamble_not_first_line():
    # 正文以 # 开头,「好的」在第二行——只看第一行,不剥
    src = "# 标题\n好的，我们开始吧，这部分讲…"
    assert strip_summary_preamble(src) == src


def test_keep_legit_body_starting_with_zheshi():
    # 以「这是」开头、含「摘要」,但无客套开场词 → 不剥
    src = "这是一个关于摘要算法的技术讲座。\n正文"
    assert strip_summary_preamble(src) == src


def test_keep_legit_haode_without_service_marker():
    # 有客套词「好的」但无服务化/元描述标记 → 不剥(正文里合法的「好的」)
    src = "好的，我们开始今天的课。\n正文"
    assert strip_summary_preamble(src) == src


# ----------------------------------------------------------------------------
# 词头融合的正文(客套字符后无分隔符)——separator 约束钉死,绝不能误删
# ----------------------------------------------------------------------------


def test_keep_haoping_fused_with_meta_noun():
    # 「好评」非客套词「好」+ 分隔符,即便首句含「总结」也不剥
    src = "好评如潮的产品总结。\n正文部分继续这里很长"
    assert strip_summary_preamble(src) == src


def test_keep_haochu_fused_with_meta_noun():
    src = "好处很多的总结方法介绍。\n正文继续这里也很长"
    assert strip_summary_preamble(src) == src


def test_keep_haode_fused_with_meta_noun():
    # 「好的总结」是偏正短语(好的+总结,无逗号),不是开场白「好的，…」
    src = "好的总结需要反复打磨。\n下面是方法"
    assert strip_summary_preamble(src) == src


def test_keep_dangran_fused_body():
    src = "当然界面设计也要点到为止。\n正文"
    assert strip_summary_preamble(src) == src


# ----------------------------------------------------------------------------
# 应剥:逗号分隔 + 仅元描述名词(无服务化措辞)也能命中真开场白
# ----------------------------------------------------------------------------


def test_strip_comma_gated_meta_only_preamble():
    # 「好的，总结如下：」——逗号分隔 + 元描述「总结」,无「为您/根据您」也是开场白
    src = "好的，总结如下：\n- a\n- b"
    assert strip_summary_preamble(src) == "- a\n- b"


def test_strip_bare_hao_with_comma():
    # 裸「好」+ 逗号 + 服务化措辞 → 真开场白变体
    src = "好，这是为您生成的摘要。\n正文"
    assert strip_summary_preamble(src) == "正文"


def test_empty_and_none_return_empty_string():
    assert strip_summary_preamble("") == ""
    assert strip_summary_preamble(None) == ""


def test_keep_when_stripping_would_empty():
    # 整条只有一句开场白没有正文 → 原样返回(剥完为空,不剥)
    src = "好的，这是为您生成的摘要。"
    assert strip_summary_preamble(src) == src


# ----------------------------------------------------------------------------
# 补充边界
# ----------------------------------------------------------------------------


def test_only_strips_once_not_looping():
    # 仅剥一次:第二行又是一句客套话也不连续剥(只删首句开场白)
    src = "好的，这是为您生成的摘要。\n当然，这是您要的总结。\n真正正文"
    assert strip_summary_preamble(src) == "当然，这是您要的总结。\n真正正文"


def test_keep_body_without_terminal_punctuation():
    # 客套词起头但首句无句末标点(被换行截断)→ 句子约束不满足,不剥
    src = "好的 这是为您生成的摘要\n正文"
    assert strip_summary_preamble(src) == src
