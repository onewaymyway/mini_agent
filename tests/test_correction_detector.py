"""
tests/test_correction_detector.py — Stage 1.4 验证

对应 self_evolution_implementation_plan.md Stage 1.4：
  人类反馈纠正检测（规则式短语识别），命中后生成 source="human_feedback" 的
  高质量 lesson 字段。宁可漏检，不可误判（误判会污染记忆）。
"""

from __future__ import annotations

import pytest

from mini_agent.perception.correction_detector import (
    detect_correction,
    make_correction_lesson_fields,
)


# ── 正例：应该被检测为纠正 ────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "不对，应该用 patch_file 而不是 write_file",
    "不是这样的，你理解错了",
    "不是这样，应该先确认参数",
    "你错了，这个函数应该返回None",
    "这样不对",
    "下次记得先跑测试",
    "下次记住要检查权限",
    "以后注意先备份",
    "别再用这种方式了",
    "不要再这样做了",
    "我是说要修改config.py而不是main.py",
    "我的意思是先测试再提交",
    "重新做一遍",
    "that's wrong, please use the other approach",
    "that's not right",
    "you're wrong about this",
    "no, that approach won't work",
    "next time remember to run tests first",
    "i meant the other file",
    "wrong approach, try again",
    "you should use patch_file instead, not write_file",
])
def test_detects_correction_phrases(text):
    assert detect_correction(text) is True, f"应检测到纠正: {text!r}"


# ── 负例：不应该被误判为纠正 ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "请帮我写一个排序算法",
    "这个函数应该是用来计算总和的",
    "我们应该先讨论一下架构",
    "不对外公开这个接口",
    "Thanks, that works great!",
    "继续吧",
    "这个类应该继承自BaseModel",
    "this should be a simple fix",
    "",
    "   ",
])
def test_does_not_flag_normal_messages(text):
    assert detect_correction(text) is False, f"不应误判为纠正: {text!r}"


def test_detect_correction_handles_non_string():
    assert detect_correction(None) is False
    assert detect_correction(123) is False
    assert detect_correction([1, 2, 3]) is False


def test_detect_correction_only_checks_head_300_chars():
    """纠正短语必须出现在前 300 字符内才会被识别，避免长消息中段误判。"""
    long_prefix = "这是一段很长的描述。" * 40  # 远超 300 字符
    text = long_prefix + "应该用 patch_file 而不是 write_file"
    assert len(long_prefix) > 300
    assert detect_correction(text) is False  # 纠正短语在300字符之后，不应命中


# ── make_correction_lesson_fields ────────────────────────────────────────────

def test_make_correction_lesson_fields_with_prior_action():
    fields = make_correction_lesson_fields(
        correction_text="不对，应该先检查文件是否存在",
        prior_action="直接调用了 write_file 覆盖了原文件",
    )
    assert fields["source"] == "human_feedback"
    assert fields["confidence"] == 0.85
    assert "直接调用了 write_file" in fields["trigger"]
    assert fields["outcome"] == "不对，应该先检查文件是否存在"
    assert fields["suggested_action"] == "不对，应该先检查文件是否存在"


def test_make_correction_lesson_fields_without_prior_action():
    fields = make_correction_lesson_fields(correction_text="下次记得先备份")
    assert fields["source"] == "human_feedback"
    assert "用户在对话中给出了明确纠正" in fields["trigger"]


def test_make_correction_lesson_fields_truncates_long_text():
    long_text = "x" * 1000
    fields = make_correction_lesson_fields(correction_text=long_text)
    assert len(fields["outcome"]) <= 500
    assert len(fields["suggested_action"]) <= 500


def test_correction_confidence_higher_than_rule_triggered():
    """人类反馈的 confidence（0.85）应明显高于规则触发的 confidence（0.6），
    对应设计文档 6.2 节"人类纠正可信度远高于自我反思"。"""
    from mini_agent.perception.lesson_rules import _RULE_TRIGGERED_CONFIDENCE
    fields = make_correction_lesson_fields(correction_text="不对")
    assert fields["confidence"] > _RULE_TRIGGERED_CONFIDENCE
