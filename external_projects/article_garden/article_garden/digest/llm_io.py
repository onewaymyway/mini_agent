"""llm_io.py — LLM 输出的解析：去掉代码围栏、解析 YAML，失败时返回可读的错误。"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

import yaml

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n|\n?```\s*$")


def describe_yaml_error(exc: yaml.YAMLError, text: str) -> str:
    """把 YAML 错误翻译成具体位置：第几行、第几列、出错的那一行原文，以及常见原因。"""
    mark = getattr(exc, "problem_mark", None)
    problem = str(getattr(exc, "problem", None) or "格式错误")
    if mark is None:
        return f"YAML 格式错误（{problem}）"
    lines = text.splitlines()
    line_no = mark.line + 1
    snippet = lines[mark.line].strip() if mark.line < len(lines) else ""
    if len(snippet) > 90:
        snippet = snippet[:90] + "…"
    return (f"第 {line_no} 行第 {mark.column + 1} 列 YAML 格式错误（{problem}）：「{snippet}」。"
            "常见原因：值以 > * & ! # % @ 等符号开头，或含有未括起的 “: ”。请把该值用英文双引号括起来。")


def parse_yaml(reply: str) -> Tuple[Optional[Any], Optional[str]]:
    """返回 (对象, 错误)。成功时错误为 None。"""
    text = _FENCE.sub("", reply or "").strip()
    if not text:
        return None, "输出为空，请只输出 YAML"
    try:
        obj = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, describe_yaml_error(exc, text)
    if not isinstance(obj, dict):
        return None, "顶层应为键值结构（字段名: 值）"
    return obj, None
