"""
role_agents/feedback.py — 角色 Agent 反馈格式化与注入

职责：
  - 把角色 Agent 输出格式化为带标签的结构化消息
  - 决定反馈注入到主 Agent 历史的方式（user 消息 / system_reminder）
  - 从评估输出中提取评分
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RoleFeedback:
    """单次角色 Agent 的反馈结果。"""
    role_name: str
    role_type: str          # evaluator / coach / custom / goal_judge
    raw_output: str         # 角色 Agent 的原始输出
    score: Optional[float] = None    # evaluator 才有：0-1 浮点
    passed: Optional[bool] = None    # 是否通过阈值
    inject_as: str = "user"          # user | system_reminder
    # [SYS-GOAL-MODE] goal_judge 才有：DONE | CONTINUE | NEED_COMPACT
    goal_status: Optional[str] = None
    # [SYS-TURN-JUDGE] turn_judge 才有：NEED_USER | AUTO_CONTINUE | NEED_COMPACT
    turn_status: Optional[str] = None


def extract_score(text: str) -> Optional[float]:
    """
    从评估输出中提取评分。
    支持多种格式：
      SCORE: 8/10  →  0.8
      score: 0.75  →  0.75
      评分：7      →  0.7（假设 10 分制）
      [SCORE: 85]  →  0.85（百分制）
    """
    patterns = [
        # SCORE: 8/10 或 8 / 10
        (r'SCORE\s*:\s*(\d+(?:\.\d+)?)\s*/\s*(\d+)', 'fraction'),
        # score: 0.75
        (r'score\s*:\s*(0?\.\d+|\d+(?:\.\d+)?)', 'float'),
        # 评分：8
        (r'评分[：:]\s*(\d+(?:\.\d+)?)\s*(?:/\s*(\d+))?', 'cn'),
        # [SCORE: 85] 百分制
        (r'\[SCORE\s*:\s*(\d+(?:\.\d+)?)\]', 'percent'),
    ]
    text_lower = text

    for pattern, fmt in patterns:
        m = re.search(pattern, text_lower, re.IGNORECASE)
        if not m:
            continue
        try:
            if fmt == 'fraction':
                numerator = float(m.group(1))
                denominator = float(m.group(2))
                return min(1.0, numerator / denominator)
            elif fmt == 'float':
                v = float(m.group(1))
                # 如果值 > 1，当作百分制
                return min(1.0, v / 100 if v > 1 else v)
            elif fmt == 'cn':
                numerator = float(m.group(1))
                denominator = float(m.group(2)) if m.group(2) else 10.0
                return min(1.0, numerator / denominator)
            elif fmt == 'percent':
                v = float(m.group(1))
                return min(1.0, v / 100 if v > 1 else v)
        except (ValueError, ZeroDivisionError):
            continue
    return None


_GOAL_STATUS_RE = re.compile(r'GOAL_STATUS\s*:\s*(DONE|CONTINUE|NEED_COMPACT)', re.IGNORECASE)


def extract_goal_status(text: str) -> Optional[str]:
    """从 GoalJudge 输出中提取状态（DONE / CONTINUE / NEED_COMPACT）。

    [Phase 5 重构] GoalJudge 现在约定输出结构化 JSON（见
    role_agents/verdict.py::parse_judge_verdict + prompts/system/goal_judge.md）。
    本函数已 deprecated，保留仅为过渡期兼容（还没切换到直接调用
    `parse_judge_verdict` 的调用方）：优先按 JSON 解析，解析失败时回退到旧的
    纯文本 "GOAL_STATUS: X" 正则提取，两者都失败才返回 None。

    找不到时返回 None（调用方应将其当作 CONTINUE 处理并原样把输出注入反馈，
    保守起见不能默认判定为 DONE）。
    """
    from mini_agent.role_agents.verdict import parse_judge_verdict

    verdict = parse_judge_verdict(
        text, valid_statuses=["DONE", "CONTINUE", "NEED_COMPACT"], fallback_status="",
    )
    if verdict.parse_ok:
        return verdict.status

    m = _GOAL_STATUS_RE.search(text)
    if not m:
        return None
    return m.group(1).upper()


_TURN_STATUS_RE = re.compile(r'TURN_STATUS\s*:\s*(NEED_USER|AUTO_CONTINUE|NEED_COMPACT)', re.IGNORECASE)


def extract_turn_status(text: str) -> Optional[str]:
    """从 TurnJudge 输出中提取状态（NEED_USER / AUTO_CONTINUE / NEED_COMPACT）。

    [Phase 5 重构] TurnJudge 现在约定输出结构化 JSON（见
    role_agents/verdict.py::parse_judge_verdict + prompts/system/turn_judge.md）。
    本函数已 deprecated，保留仅为过渡期兼容：优先按 JSON 解析，解析失败时回退到
    旧的纯文本 "TURN_STATUS: X" 正则提取，两者都失败才返回 None。

    找不到时返回 None（调用方应将其当作 NEED_USER 处理，保守起见绝不能默认判定为
    AUTO_CONTINUE，避免解析失败导致本该交还用户的一轮被系统悄悄接管）。
    """
    from mini_agent.role_agents.verdict import parse_judge_verdict

    verdict = parse_judge_verdict(
        text, valid_statuses=["NEED_USER", "AUTO_CONTINUE", "NEED_COMPACT"], fallback_status="",
    )
    if verdict.parse_ok:
        return verdict.status

    m = _TURN_STATUS_RE.search(text)
    if not m:
        return None
    return m.group(1).upper()


def format_feedback(feedback: RoleFeedback) -> str:
    """
    把 RoleFeedback 格式化为注入主 Agent 的消息文本。
    格式化风格：清晰的角色标签 + 结构化反馈内容。
    """
    header_map = {
        "evaluator": "📊 质量评估",
        "coach": "🎯 策略建议",
        "custom": "💬 角色反馈",
        "goal_judge": "🎯 目标核查",
        "turn_judge": "🧭 轮次核查",
    }
    header = header_map.get(feedback.role_type, "💬 角色反馈")
    role_label = f"[{header} · {feedback.role_name}]"

    lines = [role_label, ""]
    lines.append(feedback.raw_output.strip())

    if feedback.score is not None:
        lines.append("")
        score_pct = int(feedback.score * 100)
        status = "✅ 通过" if feedback.passed else "⚠️ 需要修订"
        lines.append(f"综合评分：{score_pct}/100  {status}")

    if feedback.goal_status is not None:
        lines.append("")
        status_map = {
            "DONE": "✅ 目标已达成",
            "CONTINUE": "🔄 尚未达成，需继续尝试",
            "NEED_COMPACT": "🗜️ 建议先压缩历史再继续",
        }
        lines.append(f"目标状态：{status_map.get(feedback.goal_status, feedback.goal_status)}")

    if feedback.turn_status is not None:
        lines.append("")
        turn_status_map = {
            "NEED_USER": "🙋 需要真人用户输入",
            "AUTO_CONTINUE": "🤖 自动接管，代替用户继续推进",
            "NEED_COMPACT": "🗜️ 建议先压缩历史再继续",
        }
        lines.append(f"轮次状态：{turn_status_map.get(feedback.turn_status, feedback.turn_status)}")

    return "\n".join(lines)


def build_inject_message(feedback: RoleFeedback) -> dict:
    """
    构建注入主 Agent _history 的消息字典。
    inject_as="user"  → role="user" 消息（最常见，主 Agent 自然读取）
    inject_as="system_reminder" → 追加到 system（CoachAgent 建议用这种）
    """
    content = format_feedback(feedback)
    if feedback.inject_as == "user":
        return {"role": "user", "content": content}
    else:
        # system_reminder 也用 user 消息包装，但加特殊前缀供主 Agent 识别
        return {"role": "user", "content": f"[system_reminder]\n{content}"}
