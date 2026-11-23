"""llm_io.py — LLM 输出的解析：去掉代码围栏、解析 YAML，失败时返回可读的错误。"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

import yaml

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n|\n?```\s*$")


def parse_yaml(reply: str) -> Tuple[Optional[Any], Optional[str]]:
    """返回 (对象, 错误)。成功时错误为 None。"""
    text = _FENCE.sub("", reply or "").strip()
    if not text:
        return None, "输出为空，请只输出 YAML"
    try:
        obj = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"不是合法的 YAML（{str(exc).splitlines()[0][:120]}）"
    if not isinstance(obj, dict):
        return None, "顶层应为键值结构（字段名: 值）"
    return obj, None
