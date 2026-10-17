"""novel_writer/intake.py — 新建向导的意图表单（方案 5.1「新建向导」）。

向导收集的内容保存到 `_studio/intake.json`，并整理成一段「作者意图」文本，
由 runner 注入**每一步**的 prompt（planner 是主要消费者）。
"""

from __future__ import annotations

from typing import Any, Dict, List

# (字段名, 中文标签, 是否多行)
INTAKE_FIELDS: List[tuple] = [
    ("genre", "题材", False),
    ("theme", "主题（一句话）", False),
    ("platform", "平台 / 出版形式", False),
    ("protagonist", "主角（身份与处境）", False),
    ("desire", "主角核心欲望", False),
    ("obstacle", "主要障碍", False),
    ("conflict", "核心冲突", False),
    ("ending", "结局走向", False),
    ("fragments", "已有创意碎片", True),
    ("reference_style", "参考风格文本（可粘贴一段，供风格指纹使用）", True),
    ("notes", "其它要求", True),
]
INTAKE_KEYS = [k for k, _, _ in INTAKE_FIELDS]
ENDING_OPTIONS = ["", "HE（圆满）", "BE（悲剧）", "OE（开放）", "未定"]


class IntakeError(ValueError):
    pass


def clean_intake(raw: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(raw, dict):
        raise IntakeError("意图必须是对象")
    unknown = [k for k in raw if k not in INTAKE_KEYS]
    if unknown:
        raise IntakeError(f"未知的意图字段：{unknown}")
    return {k: str(raw.get(k) or "").strip() for k in INTAKE_KEYS}


def intake_note(intake: Dict[str, str]) -> str:
    """整理成注入 prompt 的文本；空字段不输出。全部为空时返回空串。"""
    lines = []
    for key, label, _ in INTAKE_FIELDS:
        value = (intake.get(key) or "").strip()
        if value and key != "reference_style":
            lines.append(f"- {label}：{value}")
    ref = (intake.get("reference_style") or "").strip()
    if ref:
        lines.append("- 参考风格文本（节选）：\n" + ref[:2000])
    return "\n".join(lines)
