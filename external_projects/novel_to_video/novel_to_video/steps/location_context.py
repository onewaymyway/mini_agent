"""地点细化的上下文检索（L5）：从原始章节正文与已有剧本中找出与某个地点相关的段落。

区域预拆解、场景设定图两处共用。原则：
  * 原始正文（chapters 对应的 novel_writer 导出章节）是细节的首选依据：外观、陈设、房间、特殊物件都在正文里；
  * 剧本只是正文的改编，行数有限，作为补充；
  * 检索是确定性的字符串包含匹配（名称与别名），不做语义检索；超出上限时按章节顺序截断，并在结果里给出截断标记。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

MAX_SCRIPT_LINES = 20
PRESPLIT_PARAS = 40
PRESPLIT_CHARS = 6000
SHEET_PARAS = 30
SHEET_CHARS = 5000


def names_of(ent: Dict[str, Any]) -> List[str]:
    """实体的名称与别名（去空、去重、保序）。"""
    out: List[str] = []
    for n in [ent.get("name")] + list(ent.get("aliases") or []):
        n = str(n or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def script_lines(project_dir: Path, names: List[str], limit: int = MAX_SCRIPT_LINES) -> List[str]:
    """已有剧本中含任一名称的行（最多 limit 行）。剧本还没生成时返回空列表。"""
    out: List[str] = []
    for script in sorted((Path(project_dir) / "chapters").glob("*/script.md")):
        for line in script.read_text(encoding="utf-8").splitlines():
            if line.strip() and any(n in line for n in names):
                out.append(line.strip())
                if len(out) >= limit:
                    return out
    return out


def novel_paragraphs(project_dir: Path, names: List[str], *, max_paras: int, max_chars: int) -> List[Dict[str, Any]]:
    """原始章节正文中含任一名称的段落，按章节顺序收集。

    返回 [{chapter, text}]。达到段落数或字数上限即停止（不做截断到半段）。
    导出包缺失某章正文时跳过该章，不影响其它章。
    """
    from ..chapter_io import load_index, source_text

    names = [n for n in names if n]
    if not names:
        return []
    out: List[Dict[str, Any]] = []
    total = 0
    for row in sorted(load_index(Path(project_dir)).get("chapters", []), key=lambda r: r.get("no", 0)):
        try:
            text = source_text(Path(project_dir), row["no"])
        except (OSError, KeyError, ValueError):
            continue
        for raw in text.splitlines():
            para = raw.strip()
            if not para or not any(n in para for n in names):
                continue
            if len(out) >= max_paras or total + len(para) > max_chars:
                return out
            out.append({"chapter": row["no"], "text": para})
            total += len(para)
    return out
