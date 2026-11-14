"""P4 派生物标记、版本差异、按版本导出。

  * stale_items：修改完成后，列出需要作者重新检查的派生物（只给出建议，不自动运行 LLM）；
  * diff_versions：两个版本之间逐文件的行级差异统计，并可输出 unified diff 文本；
  * export_version：把某个版本当作一本独立的书导出（复用 final_export.export_final）。
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Dict, List

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import list_content_files, version_dir
from novel_writer.textio import read_text_lenient
from novel_writer.workspace import Workspace

PLOT_PATH = "大纲规划/线索设定/伏笔/_index.md"
CHAR_PATH = "大纲规划/人物设定/角色索引.md"
FINAL_REPORT = "一致性检查/全书终审报告.md"


def stale_items(done_chapters: List[int], change_kinds: List[str]) -> List[Dict[str, Any]]:
    """返回需要重新检查的派生物清单（建议项，作者决定是否重跑，不自动调用 LLM）。"""
    marks: List[Dict[str, Any]] = []
    if "setting" in change_kinds:
        marks.append({"item": "人物索引", "path": CHAR_PATH, "action": "recheck", "reason": "设定变更可能涉及人物"})
        marks.append({"item": "伏笔账本", "path": PLOT_PATH, "action": "recheck", "reason": "设定变更可能影响伏笔"})
    for n in sorted(done_chapters):
        marks.append({"item": f"第{n}章一致性", "path": None, "action": "recheck", "reason": "本章已改写"})
    if done_chapters:
        marks.append({"item": "全书终审报告", "path": FINAL_REPORT, "action": "rerun_manual",
                      "reason": "全书内容已变化，终审结论可能过期"})
    return marks


def diff_versions(ws, a: str, b: str, *, context: int = 2, with_text: bool = False) -> List[Dict[str, Any]]:
    """逐文件比较版本 a 与 b。返回 [{path, status, added, removed, diff?}]。status: same/changed/added/removed。"""
    ra, rb = version_dir(ws, a), version_dir(ws, b)
    fa, fb = set(list_content_files(ra)), set(list_content_files(rb))
    out: List[Dict[str, Any]] = []
    for rel in sorted(fa | fb):
        if rel not in fa:
            status, la, lb = "added", [], read_text_lenient(rb / rel)[0].splitlines()
        elif rel not in fb:
            status, la, lb = "removed", read_text_lenient(ra / rel)[0].splitlines(), []
        else:
            la = read_text_lenient(ra / rel)[0].splitlines()
            lb = read_text_lenient(rb / rel)[0].splitlines()
            status = "same" if la == lb else "changed"
        added = removed = 0
        for line in difflib.ndiff(la, lb):
            if line.startswith("+ "):
                added += 1
            elif line.startswith("- "):
                removed += 1
        item: Dict[str, Any] = {"path": rel, "status": status, "added": added, "removed": removed}
        if with_text and status != "same":
            item["diff"] = "\n".join(difflib.unified_diff(la, lb, a, b, n=context, lineterm=""))
        out.append(item)
    return out


def export_version(ws, vid: str) -> Dict[str, Any]:
    """导出某个版本。版本目录结构与工作目录一致，可直接作为 Workspace 使用。"""
    V.get_entry(ws, vid)
    from novel_writer.final_export import export_final
    return export_final(Workspace(version_dir(ws, vid)))
