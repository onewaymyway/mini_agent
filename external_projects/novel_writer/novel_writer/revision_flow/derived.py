"""P4 派生物标记、版本差异、按版本导出。

  * stale_items：修改完成后，列出需要作者重新检查的派生物（只给出建议，不自动运行 LLM）；
  * diff_versions：两个版本之间逐文件的行级差异统计，并可输出 unified diff 文本；
  * export_version：把某个版本当作一本独立的书导出（复用 final_export.export_final）。
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _git_style_block(path: str, a: str, b: str, status: str, la: List[str], lb: List[str], context: int) -> str:
    """生成单个文件的 git 风格差异块（diff --git 头 + unified 正文）。"""
    fa = "/dev/null" if status == "added" else f"a/{path}"
    fb = "/dev/null" if status == "removed" else f"b/{path}"
    header = [f"diff --git a/{path} b/{path}"]
    if status == "added":
        header.append("new file mode 100644")
    elif status == "removed":
        header.append("deleted file mode 100644")
    body = list(difflib.unified_diff(la, lb, fromfile=fa, tofile=fb, n=context, lineterm=""))
    return "\n".join(header + body)


def diff_versions(ws, a: str, b: str, *, context: int = 3) -> List[Dict[str, Any]]:
    """逐文件比较版本 a 与 b。

    返回 [{path, status, added, removed, diff}]：status 为 same / changed / added / removed；
    diff 为 git 风格的 unified diff 文本（same 时为空串）。added / removed 统计的是行数。
    """
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
        if status == "same":
            out.append({"path": rel, "status": "same", "added": 0, "removed": 0, "diff": ""})
            continue
        block = _git_style_block(rel, a, b, status, la, lb, context)
        added = sum(1 for ln in block.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in block.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        out.append({"path": rel, "status": status, "added": added, "removed": removed, "diff": block})
    return out


def format_diff(rows: List[Dict[str, Any]], path: Optional[str] = None) -> str:
    """把差异行拼成一段文本：path 为空时拼接全部有变化的文件。"""
    blocks = [r["diff"] for r in rows if r.get("diff") and (path is None or r["path"] == path)]
    return "\n".join(blocks)


def export_version(ws, vid: str) -> Dict[str, Any]:
    """导出某个版本。版本目录结构与工作目录一致，可直接作为 Workspace 使用。"""
    V.get_entry(ws, vid)
    from novel_writer.final_export import export_final
    return export_final(Workspace(version_dir(ws, vid)))
