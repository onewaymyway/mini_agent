"""一键清空评估数据：删除评估产生的全部中间结果，让下一次评估从头开始。

会删除（均位于 _revision/ 下）：
  * review/   事实卡、确定性检查与 LLM 复核的缓存与问题池、评估报告、所有调用的工作目录；
  * outline/  大纲重生成的候选记录（已采纳的候选只是记录，删除不影响它派生出的版本）；
  * pipeline/ 一键评估全流程的运行状态；
  * changes/ 中由评估采纳产生、且尚未执行（draft / confirmed）的变更，以及引用它们的预览文件。

不会删除（这些是历史，删除会破坏版本与追溯）：
  * versions/ 全部版本（包括由大纲重生成、改写派生的版本）；
  * 已应用（applied）的变更、手动创建的变更（没有评估来源）、执行记录 runs/；
  * 章节正文与设定文件。

必须显式确认（confirm=True）。运行中的后台任务或页面操作由界面负责拦截，本模块只做删除。
"""
from __future__ import annotations

import shutil
from typing import Any, Dict, List

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow.common import FlowError, revision_root

EVAL_DIRS = ("review", "outline", "pipeline")
DRAFT_STATES = ("draft", "confirmed")          # 尚未执行的变更才会被清理


def _eval_changes(ws) -> List[Dict[str, Any]]:
    return [c for c in CH.list_changes(ws)
            if isinstance(c.get("origin"), dict) and c["origin"].get("finding_id") and c.get("status") in DRAFT_STATES]


def _previews_referencing(ws, ids: set) -> List:
    folder = revision_root(ws) / "preview"
    out = []
    if not folder.is_dir():
        return out
    for p in folder.glob("*.json"):
        text = p.read_text(encoding="utf-8")
        if any(f'"{cid}"' in text for cid in ids):
            out.append(p)
    return out


def summary(ws) -> Dict[str, Any]:
    """清空前的预览：会删除什么、保留什么（不改动任何文件）。"""
    root = revision_root(ws)
    dirs = {d: sum(1 for _ in (root / d).rglob("*")) if (root / d).is_dir() else 0 for d in EVAL_DIRS}
    changes = _eval_changes(ws)
    kept_applied = sum(1 for c in CH.list_changes(ws) if c.get("status") == "applied")
    previews = _previews_referencing(ws, {c["id"] for c in changes})
    return {"files": dirs, "draft_changes": [c["id"] for c in changes], "previews": [p.name for p in previews],
            "kept": {"applied_changes": kept_applied, "versions": "全部保留"}}


def clear(ws, *, confirm: bool = False, progress=None) -> Dict[str, Any]:
    say = progress or (lambda _t: None)
    if not confirm:
        raise FlowError("清空评估数据需要明确确认（confirm=True）")
    root = revision_root(ws)
    removed_dirs: List[str] = []
    for d in EVAL_DIRS:
        target = root / d
        if target.exists():
            n = sum(1 for _ in target.rglob("*"))
            shutil.rmtree(target)
            removed_dirs.append(d)
            say(f"已删除 _revision/{d}/（{n} 个文件或目录）")
    changes = _eval_changes(ws)
    ids = {c["id"] for c in changes}
    previews = _previews_referencing(ws, ids)
    for p in previews:
        p.unlink()
        say(f"已删除预览 {p.name}")
    for c in changes:
        (CH._change_dir(ws) / f"{c['id']}.json").unlink(missing_ok=True)
        say(f"已删除评估变更 {c['id']}")
    say("评估数据已清空；版本与已应用的变更保留")
    return {"removed_dirs": removed_dirs, "removed_changes": sorted(ids), "removed_previews": [p.name for p in previews]}
