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
    role_type: str          # evaluator / coach / custom
    raw_output: str         # 角色 Agent 的原始输出
    score: Optional[float] = None    # evaluator 才有：0-1 浮点
    passed: Optional[bool] = None    # 是否通过阈值
    inject_as: str = "user"          # user | system_reminder


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


def format_feedback(feedback: RoleFeedback) -> str:
    """
    把 RoleFeedback 格式化为注入主 Agent 的消息文本。
    格式化风格：清晰的角色标签 + 结构化反馈内容。
    """
    header_map = {
        "evaluator": "📊 质量评估",
        "coach": "🎯 策略建议",
        "custom": "💬 角色反馈",
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
