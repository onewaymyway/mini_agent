"""front_matter.py — 解析与写出 Markdown 的 YAML front matter（方案 §4.3）。

解析失败（front matter 语法错误）时不抛异常：整段文本视为正文、元信息为空，避免坏文件阻塞导入。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

import yaml

_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def split(text: str) -> Tuple[Dict[str, Any], str]:
    """返回 (元信息, 正文)。没有或无法解析的 front matter → ({}, 原文)。"""
    m = _FM_RE.match(text or "")
    if not m:
        return {}, text or ""
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, text[m.end():]


def dump(meta: Dict[str, Any], body: str) -> str:
    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{head}---\n\n{body.strip()}\n"
