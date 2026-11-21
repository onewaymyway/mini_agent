"""一键清空评估数据：删除评估产生的全部中间结果，让下一次评估从头开始。

会删除（均位于 _revision/ 下）：
  * review/   确定性检查与 LLM 复核的缓存与问题池、评估报告、所有调用的工作目录；
              事实卡（review/cards/）默认也删除，除非 keep_cards=True（只清空评估结果、保留事实卡）；
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


CARDS_REL = ("review", "cards")                 # 事实卡缓存的位置（keep_cards=True 时保留）


def _targets(root: Path, keep_cards: bool) -> List[Path]:
    """要删除的顶层目标。keep_cards 时 review/ 不整体删除，而是删除其中除 cards/ 以外的内容。"""
    out: List[Path] = []
    for d in EVAL_DIRS:
        if d != "review" or not keep_cards:
            if (root / d).exists():
                out.append(root / d)
            continue
        review = root / "review"
        if review.is_dir():
            out.extend(c for c in review.iterdir() if c.name != "cards")
    return out


def _count(path: Path) -> int:
    if path.is_dir():
        return sum(1 for _ in path.rglob("*"))
    return 1


def summary(ws, keep_cards: bool = False) -> Dict[str, Any]:
    """清空前的预览：会删除什么、保留什么（不改动任何文件）。"""
    root = revision_root(ws)
    targets = _targets(root, keep_cards)
    dirs = {d: 0 for d in EVAL_DIRS}
    for t in targets:
        key = "review" if t.parent == root / "review" or t == root / "review" else t.name
        dirs[key] = dirs.get(key, 0) + _count(t)
    cards_kept = sum(1 for _ in (root / "review" / "cards").rglob("*")) if (root / "review" / "cards").is_dir() else 0
    changes = _eval_changes(ws)
    kept_applied = sum(1 for c in CH.list_changes(ws) if c.get("status") == "applied")
    previews = _previews_referencing(ws, {c["id"] for c in changes})
    return {"files": dirs, "draft_changes": [c["id"] for c in changes], "previews": [p.name for p in previews],
            "keep_cards": keep_cards, "cards_kept": cards_kept,
            "kept": {"applied_changes": kept_applied, "versions": "全部保留"}}


def clear(ws, *, confirm: bool = False, keep_cards: bool = False, progress=None) -> Dict[str, Any]:
    say = progress or (lambda _t: None)
    if not confirm:
        raise FlowError("清空评估数据需要明确确认（confirm=True）")
    root = revision_root(ws)
    removed_dirs: List[str] = []
    for target in _targets(root, keep_cards):
        rel = target.relative_to(root).as_posix()
        n = _count(target)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed_dirs.append(rel)
        say(f"已删除 _revision/{rel}（{n} 个文件或目录）")
    if keep_cards:
        say("事实卡已保留（_revision/review/cards/）")
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
    return {"removed_dirs": removed_dirs, "removed_changes": sorted(ids), "removed_previews": [p.name for p in previews],
            "keep_cards": keep_cards}
