"""novel_writer/chapter_rename.py — 把正文目录里不规范的章节文件名改成规范形式（先预览，再执行）。

规范：「第NNN章 章名.md」（超过 99 章三位，否则两位）。
- plan_chapter_renames：只读，逐个文件给出动作 keep / rename / conflict。
- apply_chapter_renames：只执行 rename 动作；目标已存在（或两个来源映射到同一目标）时绝不覆盖，只记为冲突。
只处理 正文/ 顶层的文件，不碰 正文/_drafts/（候选稿）。
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer.chapter_names import chapter_number_from_name, normalize_chapter_title
from novel_writer.fsutil import read_json
from novel_writer.workspace import Workspace, chapter_filename

_CHAPTER_GLOB = "第*章*.md"


def _entries(ws: Workspace) -> List[Path]:
    body = ws.root / "正文"
    if not body.is_dir():
        return []
    return sorted(p for p in body.glob(_CHAPTER_GLOB) if p.is_file())


def plan_chapter_renames(ws: Workspace, total_chapters: Optional[int] = None) -> List[Dict[str, Any]]:
    files = _entries(ws)
    numbers = {p: chapter_number_from_name(p.name) for p in files}
    known = [n for n in numbers.values() if n is not None]
    if total_chapters is None:
        # 位数以书籍规划的总章数为准（超过 99 章三位），缺失时再用已有文件推断
        cfg = read_json(ws.path("workspace_config"), default={}) or {}
        total_chapters = int(cfg.get("total_chapters_planned") or 0)
    total = total_chapters or max(max(known, default=0), len(files))

    rows: List[Dict[str, Any]] = []
    for p in files:
        n = numbers[p]
        row: Dict[str, Any] = {"file": p.name, "target": None, "action": "keep", "reason": ""}
        if n is None:
            row.update(action="conflict", reason="文件名开头读不到章节号")
            rows.append(row)
            continue
        title = normalize_chapter_title(p.stem)
        if not title:
            row.update(action="conflict", reason="去掉章节号后没有章名")
            rows.append(row)
            continue
        target = chapter_filename(n, title, total)
        row["target"] = target
        if target == p.name:
            row["reason"] = "已是规范名称"
        elif (p.parent / target).exists():
            row.update(action="conflict", reason="目标文件已存在，未覆盖")
        else:
            row.update(action="rename", reason="去掉重复章节号")
        rows.append(row)

    # 两个来源映射到同一目标：两者都标为冲突，避免后执行的覆盖先执行的
    counts = Counter(r["target"] for r in rows if r["action"] == "rename")
    for r in rows:
        if r["action"] == "rename" and counts[r["target"]] > 1:
            r.update(action="conflict", reason="多个文件会改成同一名称，未改动")
    return rows


def apply_chapter_renames(ws: Workspace, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    body = ws.root / "正文"
    done, skipped = [], []
    for row in plan:
        if row.get("action") != "rename":
            continue
        src, dst = body / row["file"], body / row["target"]
        if not src.is_file():
            skipped.append({**row, "reason": "源文件已不存在"})
            continue
        if dst.exists():
            skipped.append({**row, "action": "conflict", "reason": "目标文件已存在，未覆盖"})
            continue
        os.rename(src, dst)
        done.append({"from": row["file"], "to": row["target"]})
    return {"renamed": done, "skipped": skipped}
