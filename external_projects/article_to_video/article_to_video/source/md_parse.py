"""source/md_parse.py — Markdown 文章 → 带稳定 ID 的块序列（纯代码，不调用 LLM）。

块 ID：``s{章号:02d}.p{序号:02d}``。章号按一级标题递增，一级标题之前的内容为 s00。
每个块记录源行号范围（1 起），便于回溯与 V2 忠实度校验。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .ascii_graph import classify_code
from .author_meta import parse_front_matter

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^:?-{2,}:?$")
_IMG_RE = re.compile(r"^!\[(.*?)\]\((.*?)\)")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_STOP_PREFIXES = ("#", "```", ">", "|", "$$", "![", "\\boxed", "\\[")
_RULE_LINES = ("---", "***", "___")


def plain_text(s: str) -> str:
    """去掉行内 Markdown 标记，供字幕/卡片使用。"""
    s = _LINK_RE.sub(r"\1", s)
    s = re.sub(r"\*\*|__|`", "", s)
    return s.strip()


def _is_stop(rs: str) -> bool:
    return rs.startswith(_STOP_PREFIXES) or rs == "[" or rs in _RULE_LINES


def _brace_end(lines: List[str], i: int) -> int:
    """从 i 行开始，直到花括号配平的那一行（含）。"""
    depth, seen = 0, False
    for j in range(i, len(lines)):
        for ch in lines[j]:
            if ch == "{":
                depth += 1
                seen = True
            elif ch == "}":
                depth -= 1
        if seen and depth <= 0:
            return j
    return len(lines) - 1


_CONCEPT_TITLE = re.compile(r"^[A-Za-z][A-Za-z ]*[A-Za-z]$")


def _mark_concepts(blocks: List[Dict[str, Any]]) -> None:
    """概念条目（A6）：英文的一级标题（如 Experience Loop）是术语的首次出现，记录其后第一句定义。
    只做标记，不生成画面（定义卡在三期实施）。"""
    for i, b in enumerate(blocks):
        if b.get("kind") != "heading" or b.get("level") != 1 or not _CONCEPT_TITLE.match(b.get("text", "")):
            continue
        b["concept"] = True
        b["definition"] = ""
        for nxt in blocks[i + 1:i + 4]:
            if nxt.get("kind") in ("paragraph", "quote") and nxt.get("plain", nxt.get("text", "")):
                text = nxt.get("plain") or nxt.get("text", "")
                b["definition"] = re.split(r"(?<=[。！？])", text.strip("* "))[0].strip()
                break


def parse_markdown(text: str) -> Dict[str, Any]:

    lines = text.splitlines()
    n = len(lines)
    front_matter, start = parse_front_matter(lines)
    body_head = lines[start:start + 15]

    blocks: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    sec, idx, h2 = 0, 0, ""

    def emit(kind: str, s: int, e: int, **fields: Any) -> None:
        nonlocal idx
        idx += 1
        rec: Dict[str, Any] = {
            "id": f"s{sec:02d}.p{idx:02d}",
            "kind": kind,
            "section": f"s{sec:02d}",
            "h2": h2,
            "line_start": s + 1,
            "line_end": e + 1,
        }
        rec.update(fields)
        blocks.append(rec)

    i = start
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue

        # 代码块（``` 围栏）
        if s.startswith("```"):
            lang = s[3:].strip()
            j = i + 1
            body: List[str] = []
            while j < n and not lines[j].strip().startswith("```"):
                body.append(lines[j])
                j += 1
            end = min(j, n - 1)
            emit("code", i, end, lang=lang, content="\n".join(body), **classify_code(body, lang))
            i = end + 1
            continue

        # 标题
        m = _HEADING_RE.match(s)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1:
                sec += 1
                idx = 0
                h2 = ""
                sections.append({"id": f"s{sec:02d}", "title": title, "line_start": i + 1})
            elif level == 2:
                h2 = title
            emit("heading", i, i, level=level, text=title)
            i += 1
            continue

        # 公式：$$…$$、单独成行的 […]、\boxed{…}、\[…\]
        if s.startswith("$$"):
            if len(s) >= 4 and s.endswith("$$"):
                content, end, src = s[2:-2].strip(), i, "dollar"
            else:
                buf = [s[2:]]
                j = i + 1
                while j < n and "$$" not in lines[j]:
                    buf.append(lines[j])
                    j += 1
                if j < n:
                    buf.append(lines[j].split("$$")[0])
                end = min(j, n - 1)
                content, src = "\n".join(buf).strip(), "dollar"
            emit("math", i, end, content=content, src=src)
            i = end + 1
            continue
        if s == "[":
            j = i + 1
            buf = []
            while j < n and lines[j].strip() != "]":
                buf.append(lines[j])
                j += 1
            end = min(j, n - 1)
            emit("math", i, end, content="\n".join(buf).strip(), src="bracket")
            i = end + 1
            continue
        if s.startswith("\\boxed{"):
            end = _brace_end(lines, i)
            emit("math", i, end, content="\n".join(lines[i:end + 1]).strip(), src="boxed")
            i = end + 1
            continue
        if s.startswith("\\["):
            j = i
            while j < n and "\\]" not in lines[j]:
                j += 1
            end = min(j, n - 1)
            emit("math", i, end, content="\n".join(lines[i:end + 1]).strip(), src="latex_display")
            i = end + 1
            continue

        # 引用块
        if s.startswith(">"):
            j = i
            buf = []
            while j < n and lines[j].strip().startswith(">"):
                t = lines[j].strip()[1:]
                buf.append(t[1:] if t.startswith(" ") else t)
                j += 1
            qtext = "\n".join(x.strip() for x in buf if x.strip())
            emit("quote", i, j - 1, text=qtext, plain=plain_text(qtext))
            i = j
            continue

        # 表格
        if s.startswith("|"):
            j = i
            rows: List[List[str]] = []
            while j < n and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            header = None
            body_rows = rows
            if len(rows) >= 2 and all(_TABLE_SEP_RE.match(c) for c in rows[1]):
                header, body_rows = rows[0], rows[2:]
            emit("table", i, j - 1, header=header, rows=body_rows)
            i = j
            continue

        # 列表
        lm = _LIST_RE.match(raw)
        if lm:
            ordered = lm.group(2)[0].isdigit()
            j = i
            items: List[str] = []
            while j < n:
                r = lines[j]
                rs = r.strip()
                if not rs:
                    break
                lm2 = _LIST_RE.match(r)
                if lm2:
                    items.append(lm2.group(3).strip())
                elif items and not _is_stop(rs):
                    items[-1] += " " + rs
                else:
                    break
                j += 1
            emit("list", i, j - 1, ordered=ordered, items=items)
            i = j
            continue

        # 图片、分隔线
        if s.startswith("!["):
            im = _IMG_RE.match(s)
            emit("image", i, i, alt=im.group(1) if im else "", url=im.group(2) if im else "")
            i += 1
            continue
        if s in _RULE_LINES:
            emit("rule", i, i)
            i += 1
            continue

        # 段落：连续的非空、非块起始行
        j = i
        buf = []
        while j < n:
            rs = lines[j].strip()
            if not rs:
                break
            if j > i and (_is_stop(rs) or _LIST_RE.match(lines[j])):
                break
            buf.append(rs)
            j += 1
        ptext = "\n".join(buf)
        emit("paragraph", i, j - 1, text=ptext, plain=plain_text(" ".join(buf)))
        i = j

    _mark_concepts(blocks)
    return {
        "front_matter": front_matter,
        "body_start_line": start + 1,
        "body_head_lines": body_head,
        "sections": sections,
        "blocks": blocks,
    }
