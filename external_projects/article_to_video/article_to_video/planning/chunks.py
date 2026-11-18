"""planning/chunks.py — 把文章切成分节组（S1 的输入），并生成章节目录（S2 的覆盖对象）。

分组规则：按一级标题顺序累加，不超过 max_chars（默认 4000 字符）；单节超长时在块边界切开。
组内记录 block_ids 与源行号，便于回溯。
"""

from __future__ import annotations

from typing import Any, Dict, List

MAX_GROUP_CHARS = 4000


def _block_text(lines: List[str], b: Dict[str, Any]) -> str:
    return "\n".join(lines[b["line_start"] - 1:b["line_end"]])


def _make_group(idx: int, blocks: List[Dict[str, Any]], lines: List[str]) -> Dict[str, Any]:
    section_ids: List[str] = []
    for b in blocks:
        if b["section"] not in section_ids:
            section_ids.append(b["section"])
    start = min(b["line_start"] for b in blocks)
    end = max(b["line_end"] for b in blocks)
    text = "\n".join(lines[start - 1:end])
    return {
        "id": f"g{idx:02d}",
        "section_ids": section_ids,
        "block_ids": [b["id"] for b in blocks],
        "line_start": start,
        "line_end": end,
        "chars": len(text),
        "text": text,
    }


def _span_chars(lines: List[str], blocks: List[Dict[str, Any]]) -> int:
    """组的实际字符数：与最终记录的 chars 一致（含块之间的空行）。"""
    start = min(b["line_start"] for b in blocks)
    end = max(b["line_end"] for b in blocks)
    return len("\n".join(lines[start - 1:end]))


def build_groups(lines: List[str], doc: Dict[str, Any], max_chars: int = MAX_GROUP_CHARS) -> List[Dict[str, Any]]:
    per_section: Dict[str, List[Dict[str, Any]]] = {}
    for b in doc["blocks"]:
        per_section.setdefault(b["section"], []).append(b)

    groups: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []

    def flush() -> None:
        nonlocal cur
        if cur:
            groups.append(_make_group(len(groups) + 1, cur, lines))
            cur = []

    for _sec, blocks in per_section.items():
        if _span_chars(lines, blocks) > max_chars:
            # 单节超长：先收尾，再在块边界上切
            flush()
            for b in blocks:
                if cur and _span_chars(lines, cur + [b]) > max_chars:
                    flush()
                cur.append(b)
            flush()
        else:
            if cur and _span_chars(lines, cur + blocks) > max_chars:
                flush()
            cur.extend(blocks)
    flush()
    return groups


def section_catalog(lines: List[str], doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """章节目录：每个一级标题（及仅有前言内容的 s00）一条，含标题与字符数。
    只含分隔线的段落不算内容，不进入目录。"""
    titles = {s["id"]: s["title"] for s in doc.get("sections", [])}
    chars: Dict[str, int] = {}
    has_content: Dict[str, bool] = {}
    order: List[str] = []
    for b in doc["blocks"]:
        sid = b["section"]
        if sid not in chars:
            chars[sid] = 0
            has_content[sid] = False
            order.append(sid)
        chars[sid] += len(_block_text(lines, b))
        if b["kind"] != "rule":
            has_content[sid] = True
    return [
        {"id": sid, "title": titles.get(sid, "前言"), "chars": chars[sid]}
        for sid in order if has_content[sid]
    ]
