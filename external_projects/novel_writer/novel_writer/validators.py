"""novel_writer/validators.py — 回执契约与产出文件校验（零 LLM 成本，方案 7.2 的基础部分）。

回执（receipt.json）契约，由每个 skill 步骤在结束时写出：
  {
    "status": "done" | "needs_user",   # needs_user 表示需要作者决定，引擎会挂起该步
    "summary": "一句话说明做了什么",
    "outputs": ["相对工作目录的路径", ...],   # 可选，仅供展示
    "needs_user": [{"id": "...", "question": "...", "options": ["..."]}]   # needs_user 时必填
  }
M1 只校验结构；内容质量（字数、伏笔、人物名白名单等）由 M3 的机器校验负责。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from novel_writer import plot_delta as PD
from novel_writer.fsutil import atomic_write_json
from novel_writer.textio import read_text_utf8
from novel_writer.workspace import Workspace

RECEIPT_STATUSES = ("done", "needs_user")


def receipt_problems(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return [f"回执顶层不是 JSON 对象，而是 {type(data).__name__}"]
    problems: List[str] = []
    status = data.get("status")
    if status not in RECEIPT_STATUSES:
        problems.append(f"回执 status 必须是 {list(RECEIPT_STATUSES)} 之一，当前 {status!r}")
    if status == "needs_user":
        items = data.get("needs_user")
        if not isinstance(items, list) or not items:
            problems.append("status 为 needs_user 时必须给出非空的 needs_user 列表")
        else:
            for i, item in enumerate(items):
                if not isinstance(item, dict) or not str(item.get("question", "")).strip():
                    problems.append(f"needs_user[{i}] 缺少 question")
    return problems


def _is_glob(spec: str) -> bool:
    return spec.startswith("glob:") or "*" in spec


def missing_outputs(ws: Workspace, outputs: Sequence[str]) -> List[str]:
    """返回缺失的产出（相对路径）。`glob:` 前缀或含 `*` 的按模式匹配，至少要有一个文件。"""
    missing: List[str] = []
    for spec in outputs:
        if _is_glob(spec):
            pattern = spec[len("glob:"):] if spec.startswith("glob:") else spec
            if not any(p.is_file() for p in ws.root.glob(pattern)):
                missing.append(spec)
        else:
            if not (ws.root / spec).is_file():
                missing.append(spec)
    return missing


# ── 产出内容校验（M8）──────────────────────────────────────────────
# 框架只检查产出文件「存在」；对于下游要读取的结构化产出（如择优报告），还要检查内容。
# 内容不合格时，与回执问题一样作为提示交给 agent 重写（见 runner.MAX_RECEIPT_ATTEMPTS）。
# 校验可以顺便做规范化（如 "a"→"A"），规范化结果写回文件，避免下游因写法不同而失败。

PICK_WINNER_ALIASES: Dict[str, str] = {
    "A": "A", "A稿": "A", "正文": "A", "正文稿": "A", "原稿": "A",
    "B": "B", "B稿": "B", "候选": "B", "候选稿": "B", "候选稿B": "B",
}


def normalize_pick_winner(value: Any) -> Optional[str]:
    """把择优结论规范成 "A" / "B"；无法识别返回 None（调用方不得猜测）。"""
    if not isinstance(value, str):
        return None
    return PICK_WINNER_ALIASES.get(value.strip().replace(" ", "").upper())


_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)


def best_pick_report_problems(path: Path) -> List[str]:
    """检查择优报告：必须是 JSON 对象，且 winner 能规范为 A/B。合格时写回规范化结果。"""
    if not path.is_file():
        return [f"择优报告不存在：{path.name}"]
    data, err, fenced = _read_json_loose(path)
    if err is not None:
        return [f"择优报告不是合法 JSON（{err}）。请只输出一个 JSON 对象，不要夹杂说明文字。"]
    if not isinstance(data, dict):
        return [f"择优报告顶层必须是 JSON 对象，当前是 {type(data).__name__}"]
    winner = normalize_pick_winner(data.get("winner"))
    if winner is None:
        return [f"择优报告的 winner 必须是 \"A\"（正文）或 \"B\"（候选稿），当前为 {data.get('winner')!r}"]
    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        return ["择优报告的 reason 必须是字符串"]
    if data.get("winner") != winner or fenced:
        data["winner"] = winner
        atomic_write_json(path, data)
    return []


def _check_best_pick(ws: Workspace, step: Any) -> List[str]:
    return best_pick_report_problems(ws.path("best_pick_report", nn=step.chapter))


def _read_json_loose(path: Path) -> tuple:
    """读取 JSON，兼容 ```json 代码围栏。返回 (data, error, fenced)。"""
    raw = read_text_utf8(path).lstrip("\ufeff").strip()
    fenced = False
    m = _FENCE.match(raw)
    if m:
        raw, fenced = m.group(1).strip(), True
    try:
        return json.loads(raw), None, fenced
    except ValueError as exc:
        return None, str(exc), fenced


_CHAPTER_NO = re.compile(r"^\s*(?:第\s*)?(\d+)\s*(?:章)?\s*$")


def as_chapter_no(value: Any) -> Optional[int]:
    """章号规范化：整数、"12"、"第12章" 均视为 12；其它返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = _CHAPTER_NO.match(value)
        if m:
            return int(m.group(1))
    return None


def as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def blueprint_problems(path: Path) -> List[str]:
    """蓝图：必须有非空 chapters 列表；每章的 id 须能规范为唯一章号；word_budget 若给出须为非负整数。
    章号格式不对时，下游 chapter_entry 会找不到条目，字数配额静默变为 0，因此这里必须拦住。"""
    data, err, fenced = _read_json_loose(path)
    if err is not None:
        return [f"蓝图不是合法 JSON（{err}）。请只输出一个 JSON 对象。"]
    if not isinstance(data, dict):
        return [f"蓝图顶层必须是 JSON 对象，当前是 {type(data).__name__}"]
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        return ["蓝图缺少非空的 chapters 列表（章节表）"]
    problems: List[str] = []
    seen: Dict[int, int] = {}
    changed = fenced
    for i, ch in enumerate(chapters, start=1):
        if not isinstance(ch, dict):
            problems.append(f"第 {i} 条章节条目必须是对象")
            continue
        no = as_chapter_no(ch.get("id"))
        if no is None:
            problems.append(f"第 {i} 条章节的 id 必须是章号整数（如 1），当前为 {ch.get('id')!r}")
            continue
        if no in seen:
            problems.append(f"章号 {no} 重复出现（第 {seen[no]} 条与第 {i} 条）")
            continue
        seen[no] = i
        if ch.get("id") != no:
            ch["id"] = no
            changed = True
        if "word_budget" in ch and ch["word_budget"] is not None:
            wb = as_int(ch["word_budget"])
            if wb is None or wb < 0:
                problems.append(f"第 {no} 章的 word_budget 必须是非负整数，当前为 {ch['word_budget']!r}")
            elif ch["word_budget"] != wb:
                ch["word_budget"] = wb
                changed = True
    if problems:
        return problems
    if changed:
        atomic_write_json(path, data)
    return []


TRACKER_LIST_KEYS = ("foreshadowing", "threads", "items")
TRACKER_CHAPTER_KEYS = ("plant_chapter", "resolve_chapter", "payoff_chapter")


def plot_tracker_problems(path: Path) -> List[str]:
    """伏笔账本：必须含非空的伏笔列表（foreshadowing/threads/items 之一）；每条须有 id；章号字段须可规范为整数。
    格式不对时下游 h_plot_tasks 会读成空列表，伏笔就静默地不被埋设或兑现。"""
    data, err, fenced = _read_json_loose(path)
    if err is not None:
        return [f"伏笔账本不是合法 JSON（{err}）。请只输出一个 JSON 对象。"]
    if not isinstance(data, dict):
        return [f"伏笔账本顶层必须是 JSON 对象，当前是 {type(data).__name__}"]
    key = next((k for k in TRACKER_LIST_KEYS if isinstance(data.get(k), list)), None)
    if key is None:
        return [f"伏笔账本缺少伏笔列表（应为 {' / '.join(TRACKER_LIST_KEYS)} 之一，且为数组）"]
    items = data[key]
    if not items:
        return [f"伏笔列表 {key} 为空：至少需要登记一条伏笔"]
    problems: List[str] = []
    changed = fenced
    for i, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            problems.append(f"第 {i} 条伏笔必须是对象")
            continue
        if not str(it.get("id") or "").strip():
            problems.append(f"第 {i} 条伏笔缺少 id")
        for ck in TRACKER_CHAPTER_KEYS:
            if it.get(ck) is None:
                continue
            no = as_chapter_no(it[ck])
            if no is None:
                problems.append(f"伏笔 {it.get('id') or i} 的 {ck} 必须是章号整数，当前为 {it[ck]!r}")
            elif it[ck] != no:
                it[ck] = no
                changed = True
    if problems:
        return problems
    if changed:
        atomic_write_json(path, data)
    return []


def _check_blueprint(ws: Workspace, step: Any) -> List[str]:
    return blueprint_problems(ws.path("blueprint_draft"))   # 草稿是 plan_blueprint 的产出；拆分前校验


def _check_plot_register(ws: Workspace, step: Any) -> List[str]:
    return plot_tracker_problems(ws.path("plot_register"))


def _check_plot_tracker(ws: Workspace, step: Any) -> List[str]:
    """伏笔账本（小文件存储）：可读、非空、每条有 id、章号合法。"""
    from novel_writer import plot_store as PS
    try:
        data = PS.read_store(ws.path("plot_items_dir"))
    except PS.LedgerError as exc:
        return exc.problems
    items = data.get(PD_LIST_KEY, [])
    if not items:
        return ["伏笔账本为空：至少需要登记一条伏笔"]
    problems = []
    for it in items:
        if not str(it.get("id") or "").strip():
            problems.append("有伏笔缺少 id")
        for ck in TRACKER_CHAPTER_KEYS:
            if it.get(ck) is not None and as_chapter_no(it[ck]) is None:
                problems.append(f"伏笔 {it.get('id')} 的 {ck} 必须是章号整数")
    return problems


def _check_plot_delta(ws: Workspace, step: Any) -> List[str]:
    """伏笔增量：JSON 结构合法，且能应用到当前账本（引用的 id 存在、状态流转合法）。"""
    from novel_writer import plot_delta as PD
    path = ws.path("plot_delta", nn=step.chapter)
    if not path.is_file():
        return [f"伏笔增量不存在：{path.name}"]
    data, err, fenced = _read_json_loose(path)
    if err is not None:
        return [f"伏笔增量不是合法 JSON（{err}）。请只输出一个 JSON 对象。"]
    try:
        base = PD.undo_chapter(_load_ledger(ws), step.chapter)
    except PD.PlotDeltaError as exc:
        return exc.problems
    problems = PD.delta_problems(data, base, step.chapter)
    if problems:
        return problems
    if fenced:
        atomic_write_json(path, data)
    return []


def _check_character_files(ws: Workspace, step: Any) -> List[str]:
    """人物更新的产出校验（第四阶段）：本次写过的角色文件必须全部合法。"""
    from novel_writer import character_store as CS
    return CS.problems_for_dir(ws.path("characters_dir"))


def _check_patrol_findings(ws: Workspace, step: Any) -> List[str]:
    """巡检整改项：JSON 结构合法、字段齐全。带代码围栏时写回规范化结果。"""
    from novel_writer import patrol_findings as PF
    n = int((getattr(step, "extra", None) or {}).get("through_chapter") or 0)
    path = ws.path("patrol_findings", nn=n)
    if not path.is_file():
        return [f"整改项文件不存在：{path.name}"]
    data, err, fenced = _read_json_loose(path)
    if err is not None:
        return [f"整改项不是合法 JSON（{err}）。请只输出一个 JSON 对象。"]
    probs = PF.problems(data)
    if probs:
        return probs
    if fenced:
        atomic_write_json(path, data)
    return []


OUTPUT_CHECKS: Dict[str, Callable[[Workspace, Any], List[str]]] = {
    "patrol_findings": _check_patrol_findings,
    "character_files": _check_character_files,
    "best_pick_report": _check_best_pick,
    "blueprint": _check_blueprint,
    "plot_tracker": _check_plot_tracker,
    "plot_delta": _check_plot_delta,
    "plot_register": _check_plot_register,
}


def content_problems(ws: Workspace, step: Any) -> List[str]:
    """步骤声明的产出内容校验（step.extra["output_check"]）。未声明则不校验。"""
    raw = (getattr(step, "extra", None) or {}).get("output_check")
    if not raw:
        return []
    names = [raw] if isinstance(raw, str) else list(raw)
    problems: List[str] = []
    for name in names:
        checker = OUTPUT_CHECKS.get(name)
        if checker is None:
            raise KeyError(f"未知的产出校验：{name}")
        problems.extend(checker(ws, step))
    return problems


PD_LIST_KEY = PD.DEFAULT_LIST_KEY


def _load_ledger(ws: Workspace) -> Dict[str, Any]:
    """读取伏笔账本：已迁移为小文件则读目录，否则读旧版 JSON（兼容未迁移的项目）。损坏则抛 PlotDeltaError。"""
    from novel_writer import plot_store as PS
    if PS.is_store(ws.path("plot_items_dir")):
        return PS.read_store(ws.path("plot_items_dir"))
    return PD.load_tracker_strict(ws.path("plot_legacy"))
