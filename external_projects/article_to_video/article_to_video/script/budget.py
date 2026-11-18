"""script/budget.py — 字数与时长换算（与 importer 的语速估算一致：4.2 字/秒）。"""

from __future__ import annotations

CHARS_PER_SECOND = 4.2
PACING_TOLERANCE = 0.15  # 设计文档：章的口播字数允许 ±15%


def chars_for_minutes(minutes: float) -> int:
    return round(float(minutes) * 60 * CHARS_PER_SECOND)


def count_chars(text: str) -> int:
    return len(text.replace(" ", "").replace("\n", ""))


def pacing_issue(total_chars: int, budget_chars: int) -> "str | None":
    if budget_chars <= 0:
        return None
    dev = (total_chars - budget_chars) / budget_chars
    if abs(dev) > PACING_TOLERANCE:
        return f"字数 {total_chars} 偏离预算 {budget_chars}（{dev:+.0%}），应在 ±15% 内"
    return None
