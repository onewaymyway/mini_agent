"""
perception/correction_detector.py — 人类反馈纠正检测（Stage 1.4）

对应 self_evolution_implementation_plan.md Stage 1.4 / 设计文档第 6.2 节
"人类反馈：最高质量、最低成本的信号源"。

用户在对话中常给出直接纠正（"不对，应该用 patch_file"、"下次记得先跑测试"），
这是 ground truth，可信度远高于自我反思，产生成本几乎为零。本模块用规则式
短语检测识别这类纠正，立即转成 entry_type="lesson"、source="human_feedback"、
较高 confidence 的记忆条目，不等 SessionEnd。

设计取舍：
  - 规则式检测（短语匹配），不调用 LLM 分类，保持零成本、零延迟
  - 覆盖中英文常见纠正句式，宁可漏检（false negative），不可错误地把无关消息
    标记为纠正（false positive 会污染记忆，让 agent "记住"错误的东西）
  - 检测到纠正后，trigger/outcome 由调用方（agent.py）填充"上一轮 agent 做了什么"，
    本模块只负责"这是不是一句纠正"的判断，不持有对话历史
"""

from __future__ import annotations

import re as _re

# 中文纠正短语（句首或独立出现均可触发，避免命中"不对外公开"这类非纠正语境，
# 因此大多数模式要求短语后紧跟标点或语气词，而不是任意子串匹配）
_ZH_PATTERNS = (
    r"不对[,，。！!\s]",
    r"^不对$",
    r"不是这样",
    r"不应该这样",
    r"不是.{0,10}应该",     # "不是这样，应该..." 类句式：否定 + 应该 共现
    r"应该.{0,10}而不是",   # "应该用 X 而不是 Y"：应该 + 而不是 共现，明确对比纠正
    r"你错了",
    r"这样不对",
    r"这不对",
    r"重新做",
    r"重新来",
    r"下次记住",
    r"下次记得",
    r"下次注意",
    r"以后记得",
    r"以后注意",
    r"不要再",
    r"别再",
    r"我是说",
    r"我的意思是",
    r"我说的不是这个",
)

# 英文纠正短语
_EN_PATTERNS = (
    r"\bthat'?s wrong\b",
    r"\bthat'?s not right\b",
    r"\bthat'?s incorrect\b",
    r"\bnot what i meant\b",
    r"\bnot what i asked\b",
    r"\byou'?re wrong\b",
    r"\bno,? that'?s\b",
    r"\bno,? (please|don'?t)\b",
    r"\bno,? that\b.{0,30}\b(won'?t|wont|doesn'?t|isn'?t)\b",  # "no, that approach won't work"
    r"\bshould (have|use|be).{0,20}\b(instead|not)\b",  # "should use X instead"：对比纠正
    r"\bnext time,? (please|remember|make sure)\b",
    r"\bremember to\b",
    r"\bdon'?t do that again\b",
    r"\bi meant\b",
    r"\bredo (this|that)\b",
    r"\bdo (it|this) again\b",
    r"\bwrong (approach|way|method)\b",
)

_ALL_PATTERNS = tuple(
    _re.compile(p, _re.IGNORECASE) for p in (_ZH_PATTERNS + _EN_PATTERNS)
)


def detect_correction(text: str) -> bool:
    """
    判断一段用户输入文本是否包含明确的纠正信号。

    只在文本前 300 字符内匹配——纠正性表达几乎总是出现在消息开头
    （用户先表明"你错了"，再说明具体怎么改），避免长消息中段偶然
    出现"应该是"之类短语被误判（例如引用代码、转述他人观点等场景）。
    """
    if not text or not isinstance(text, str):
        return False
    head = text[:300]
    return any(p.search(head) for p in _ALL_PATTERNS)


def make_correction_lesson_fields(
    correction_text: str,
    prior_action: str = "",
) -> dict:
    """
    把检测到的纠正文本转换为可直接喂给 MemoryEntry 的字段字典
    （trigger/outcome/root_cause/suggested_action/confidence/source）。

    参数：
        correction_text: 用户的纠正性输入原文
        prior_action:    上一轮 agent 做了什么的简述（由调用方提供，
                          通常是上一条 assistant 消息或工具调用的摘要；
                          为空时 trigger 退化为只描述纠正本身）
    """
    trigger = (
        f"agent 刚执行了：{prior_action[:300]}，随后用户给出纠正"
        if prior_action
        else "用户在对话中给出了明确纠正"
    )
    return {
        "trigger": trigger,
        "outcome": correction_text[:500],
        "root_cause": "",  # 人类纠正通常直接给出做法，根因留给后续反思或留空
        "suggested_action": correction_text[:500],
        "confidence": 0.85,  # 高于规则触发(0.6)，低于满分——纠正文本本身可能不够具体
        "source": "human_feedback",
    }
