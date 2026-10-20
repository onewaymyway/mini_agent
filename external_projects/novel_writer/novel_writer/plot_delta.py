"""novel_writer/plot_delta.py — 伏笔增量（M9，第一阶段）。

问题背景：sync_plot 过去让 LLM 整体重写整本伏笔账本，越写越慢、越容易截断或静默丢条目，
并且每次改动都会让所有依赖账本的下游步骤过期。

新做法：
- sync_plot 只输出本章的**增量**（埋设 / 推进 / 兑现了哪些伏笔），体积与全书规模无关。
- 合并由 Python 确定性完成（apply_delta），不再让 LLM 复述整本账本。
- 账本只在合并后内容真的变化时才写盘，下游步骤不会因无实质变化而过期。
- 每章的增量会记录「合并前的状态」，重跑同一章时先撤销再应用，不会重复埋设。
- 账本损坏时抛出明确错误，而不是默认为空。

本模块只做纯逻辑，不依赖工作区；磁盘读写由调用方（steps / chapter_ops / validators）负责。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

TRACKER_LIST_KEYS = ("foreshadowing", "threads", "items")
DEFAULT_LIST_KEY = "foreshadowing"
APPLIED_KEY = "applied_deltas"          # 账本内记录每章已应用的增量及其合并前状态
KINDS = ("planted", "advanced", "resolved")


class PlotDeltaError(Exception):
    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        super().__init__("；".join(self.problems))


def canonical(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def list_key(data: Dict[str, Any]) -> str:
    return next((k for k in TRACKER_LIST_KEYS if isinstance(data.get(k), list)), DEFAULT_LIST_KEY)


def load_tracker_strict(path: Path) -> Dict[str, Any]:
    """读取账本。文件不存在返回空账本；文件存在但损坏则抛错（绝不静默当作空）。"""
    if not Path(path).exists():
        return {DEFAULT_LIST_KEY: []}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise PlotDeltaError([f"伏笔账本无法读取（{exc}）：{Path(path).name}。请检查该文件，或从 .bak 备份恢复"])
    if not isinstance(data, dict):
        raise PlotDeltaError([f"伏笔账本顶层必须是 JSON 对象：{Path(path).name}"])
    data.setdefault(list_key(data), [])
    return data


def _index(items: List[Dict[str, Any]], iid: str) -> Optional[int]:
    for i, it in enumerate(items):
        if isinstance(it, dict) and str(it.get("id")) == iid:
            return i
    return None


def _status(item: Dict[str, Any]) -> str:
    return str(item.get("status") or "planted")


def undo_chapter(tracker: Dict[str, Any], chapter: int) -> Dict[str, Any]:
    """撤销某章已应用的增量，返回新账本（不修改入参）。该章没有记录则原样返回副本。"""
    t = copy.deepcopy(tracker)
    applied = t.get(APPLIED_KEY) or {}
    rec = applied.pop(str(chapter), None)
    if rec is None:
        return t
    items = t[list_key(t)]
    for iid, before in rec.get("before", {}).items():
        idx = _index(items, iid)
        if before is None:
            if idx is not None:
                items.pop(idx)
        elif idx is None:
            items.append(before)
        else:
            items[idx] = before
    return t


def delta_problems(delta: Any, base: Dict[str, Any], chapter: int) -> List[str]:
    """校验增量是否能应用到 base（base 已撤销本章旧增量）。返回问题列表，空表示合格。"""
    if not isinstance(delta, dict):
        return ["伏笔增量顶层必须是 JSON 对象"]
    problems: List[str] = []
    for kind in KINDS:
        if kind in delta and not isinstance(delta[kind], list):
            problems.append(f"增量字段 {kind} 必须是数组")
    if problems:
        return problems
    items = base[list_key(base)]
    seen: set = set()
    for kind in KINDS:
        for e in delta.get(kind) or []:
            if not isinstance(e, dict) or not str(e.get("id") or "").strip():
                problems.append(f"{kind} 中有条目缺少 id")
                continue
            iid = str(e["id"]).strip()
            if iid in seen:
                problems.append(f"伏笔 {iid} 在本章增量中出现多次（同一章每条伏笔只能有一个动作）")
                continue
            seen.add(iid)
            idx = _index(items, iid)
            if kind == "planted":
                if idx is not None:
                    problems.append(f"伏笔 {iid} 已存在，不能重复埋设（推进请用 advanced）")
            else:
                if idx is None:
                    problems.append(f"{kind} 引用了不存在的伏笔 {iid}（埋设请放入 planted）")
                    continue
                st = _status(items[idx])
                if st == "resolved":
                    action = "推进" if kind == "advanced" else "兑现"
                    problems.append(f"伏笔 {iid} 已兑现，不能再{action}")
    return problems


def apply_delta(tracker: Dict[str, Any], delta: Dict[str, Any], chapter: int) -> Dict[str, Any]:
    """把第 chapter 章的增量应用到账本，返回新账本（不修改入参）。
    若该章此前已应用过增量，先撤销再应用，因此重跑同一章是幂等的。不合格则抛 PlotDeltaError。"""
    base = undo_chapter(tracker, chapter)
    problems = delta_problems(delta, base, chapter)
    if problems:
        raise PlotDeltaError(problems)
    t = base
    key = list_key(t)
    items = t[key]
    before: Dict[str, Any] = {}

    def touch(iid: str) -> None:
        if iid in before:
            return
        idx = _index(items, iid)
        before[iid] = copy.deepcopy(items[idx]) if idx is not None else None

    for e in delta.get("planted") or []:
        iid = str(e["id"]).strip()
        touch(iid)
        item = {k: v for k, v in e.items() if k != "id"}
        item.update({"id": iid, "status": "planted", "plant_chapter": chapter})
        items.append(item)

    for kind, status in (("advanced", "advanced"), ("resolved", "resolved")):
        for e in delta.get(kind) or []:
            iid = str(e["id"]).strip()
            touch(iid)
            item = items[_index(items, iid)]
            item["status"] = status
            if status == "resolved":
                item["resolve_chapter"] = chapter
            item.setdefault("progress", []).append({"chapter": chapter, "note": str(e.get("note") or "")})

    applied = t.setdefault(APPLIED_KEY, {})
    applied[str(chapter)] = {"before": before, "delta": delta}
    return t


def merge_result(tracker: Dict[str, Any], delta: Any, chapter: int) -> Dict[str, Any]:
    """合并入口：校验并应用，返回新账本。供步骤处理器调用。"""
    return apply_delta(tracker, delta, chapter)
