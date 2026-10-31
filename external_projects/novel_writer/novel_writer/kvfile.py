"""novel_writer/kvfile.py — 小文件的文件头编解码（伏笔 / 人物 / 蓝图共用）。

格式：
    ---
    key: <JSON值>        每行一个字段，值必须写在同一行
    ---
    正文（由系统生成，仅供阅读）

只负责"文件头 <-> 字段字典"的转换，不做业务校验；业务校验由各存储模块完成。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Sequence

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class KVFormatError(Exception):
    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("；".join(self.problems))


def encode(rec: Dict[str, Any], order: Sequence[str] = ()) -> str:
    """字段字典 -> 文件头文本。order 指定字段的输出顺序，其余字段按原顺序排在后面。"""
    keys = [k for k in order if k in rec] + [k for k in rec if k not in order]
    lines = ["---"]
    for k in keys:
        if not KEY_RE.match(str(k)):
            raise KVFormatError([f"字段名不合法：{k!r}"])
        lines.append(f"{k}: {json.dumps(rec[k], ensure_ascii=False, separators=(',', ':'))}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def strip_trailing_commas(raw: str) -> str:
    """删除字符串之外、紧跟 } ] 或行尾的逗号。只做这一件事，不改变任何值的含义。

    LLM 生成的 JSON 常见问题：对象或数组末尾多一个逗号（如 `[...],`）。
    字符串内部的逗号（包括 `,}` 这样的文字）保持不变。
    """
    out: List[str] = []
    in_str = False
    esc = False
    n = len(raw)
    for i, ch in enumerate(raw):
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            continue
        if ch == ",":
            j = i + 1
            while j < n and raw[j].isspace():
                j += 1
            if j >= n or raw[j] in "}]":
                continue            # 多余的尾逗号：丢弃
        out.append(ch)
    return "".join(out)


def loads_value(raw: str) -> Any:
    """解析字段值（JSON）。先按严格 JSON；失败时只容忍尾逗号。两者都失败则抛出原始错误。"""
    try:
        return json.loads(raw)
    except ValueError as first:
        cleaned = strip_trailing_commas(raw)
        if cleaned == raw:
            raise first
        try:
            return json.loads(cleaned)
        except ValueError:
            raise first


def decode(text: str, where: str) -> Dict[str, Any]:
    """文件头文本 -> 字段字典。遇到第一个格式错误即报出文件名与行号。"""
    # Windows 编辑器/PowerShell 常写入 UTF-8 BOM，会让首行变成 \ufeff---
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        raise KVFormatError([f"{where}：缺少文件头（应以 --- 开始）"])
    fields: Dict[str, Any] = {}
    for n, line in enumerate(lines[1:], start=2):
        if line.strip() == "---":
            return fields
        if not line.strip():
            continue
        key, sep, raw = line.partition(": ")
        if not sep:
            raise KVFormatError([f"{where} 第 {n} 行格式错误（应为 key: JSON值，且值必须写在同一行）"])
        if key in fields:
            raise KVFormatError([f"{where} 第 {n} 行：字段 {key} 重复"])
        try:
            fields[key] = loads_value(raw)
        except ValueError as exc:
            shown = raw if len(raw) <= 60 else raw[:60] + "…"
            raise KVFormatError([f"{where} 第 {n} 行的 {key} 不是合法 JSON：值为 {shown}。"
                                 "字符串内若需要引号，请使用中文「」『』，不要使用英文双引号；字符串用双引号包裹，且整个值写在同一行"])
    raise KVFormatError([f"{where}：文件头未闭合（缺少结束的 ---）"])
