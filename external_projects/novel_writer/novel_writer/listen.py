"""novel_writer/listen.py — 听书：把章节正文切成适合浏览器朗读的片段（M7）。

只做纯逻辑，不依赖 streamlit，便于单元测试。浏览器端的 speechSynthesis 逐段朗读，
这里保证两件事：
1. 每段不超过 MAX_CHARS 字。Chrome 对很长的单段朗读会在约 15 秒后静默停止，短段可以规避。
2. 章节标题作为每章的第一段朗读（「第N章，章名」），段落之间保持自然停顿。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List

MAX_CHARS = 180

_SENT_END = re.compile(r"(?<=[。！？!?；;])")
_CLAUSE_END = re.compile(r"(?<=[，,、：:])")
_MD_INLINE = re.compile(r"(\*\*|__|\*|_|`|~~)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def clean_line(raw: str) -> str:
    """去掉 Markdown 标记，返回可朗读的纯文本；标题行与空行返回空串。"""
    line = raw.strip()
    if not line or line.startswith("#"):
        return ""
    line = re.sub(r"^>+\s*", "", line)
    line = re.sub(r"^([-*+]|\d+[.)])\s+", "", line)
    line = _MD_LINK.sub(r"\1", line)
    line = _MD_INLINE.sub("", line)
    return line.strip()


def _split_keep(text: str, pattern: "re.Pattern[str]") -> List[str]:
    return [p for p in pattern.split(text) if p and p.strip()]


def _hard_cut(text: str, max_chars: int) -> List[str]:
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]


def _units(line: str, max_chars: int) -> List[str]:
    """把一行拆成不超过 max_chars 的最小单元：先按句末标点，再按逗号，最后硬切。"""
    out: List[str] = []
    for sent in _split_keep(line, _SENT_END):
        if len(sent) <= max_chars:
            out.append(sent)
            continue
        for clause in _split_keep(sent, _CLAUSE_END):
            if len(clause) <= max_chars:
                out.append(clause)
            else:
                out.extend(_hard_cut(clause, max_chars))
    return out


def _pack(units: Iterable[str], max_chars: int) -> List[str]:
    chunks: List[str] = []
    cur = ""
    for u in units:
        if cur and len(cur) + len(u) > max_chars:
            chunks.append(cur)
            cur = ""
        cur += u
    if cur:
        chunks.append(cur)
    return chunks


def chapter_segments(number: int, title: str, text: str, max_chars: int = MAX_CHARS) -> List[str]:
    """一章的朗读片段：第一段是标题，之后是正文。正文为空时只返回空列表（该章跳过）。"""
    body: List[str] = []
    for raw in text.splitlines():
        line = clean_line(raw)
        if line:
            body.extend(_pack(_units(line, max_chars), max_chars))
    if not body:
        return []
    head = f"第{number}章，{title}" if title else f"第{number}章"
    return [head] + body


def build_payload(chapters: Iterable[Dict[str, Any]], max_chars: int = MAX_CHARS) -> Dict[str, Any]:
    """chapters: [{"chapter": int, "title": str, "text": str}, ...]（按章节号升序）。

    返回 {"segments": [str, ...], "chapters": [{"ch", "title", "start"}, ...]}，
    其中 start 是该章第一段在 segments 中的下标，供前端「上一章/下一章」跳转。
    """
    segments: List[str] = []
    index: List[Dict[str, Any]] = []
    for ch in chapters:
        segs = chapter_segments(int(ch["chapter"]), ch.get("title") or "", ch.get("text") or "", max_chars)
        if not segs:
            continue
        index.append({"ch": int(ch["chapter"]), "title": ch.get("title") or "", "start": len(segments)})
        segments.extend(segs)
    return {"segments": segments, "chapters": index}


def payload_to_js(payload: Dict[str, Any]) -> str:
    """序列化为可安全嵌入 <script> 的 JSON 字面量（转义 </，防止提前闭合标签）。"""
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
