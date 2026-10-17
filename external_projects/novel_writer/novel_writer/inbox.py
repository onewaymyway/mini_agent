"""novel_writer/inbox.py — 收件箱：汇总「需要你处理的事」（M5，方案 6.3 / 5.2 / 11 的通知部分）。

收件箱是**从状态文件推导出来的视图**，不是另一份需要维护的账本：
  * 待批准（waiting_review）、待答复（waiting_user）、升级人工（修改循环的 waiting_user）；
  * 失败 / 未实现（failed / blocked）；
  * 需重跑（stale，合并成一条）；
  * 预算暂停（worker 因预算耗尽而退出，可直接续跑）。

每次 worker 退出时，`refresh()` 把快照写到 `_studio/inbox/index.json`，供界面、批量入口与
将来的外部通知（如推送到 IM）读取。本期**不接入任何外部通知渠道**：渠道未定义，快照已足够。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List

from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace

KIND_APPROVE = "approve"          # 待批准
KIND_ANSWER = "answer"            # 待答复（needs_user）
KIND_ESCALATE = "escalate"        # 修改循环升级人工
KIND_FAILED = "failed"            # 失败
KIND_BLOCKED = "blocked"          # 未实现
KIND_STALE = "stale"              # 需重跑（合并）
KIND_BUDGET = "budget_pause"      # 预算暂停（可续跑）

SEVERITY_BLOCK = "block"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"

_SEVERITY_ORDER = {SEVERITY_BLOCK: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}
_BUDGET_REASONS = {"budget_chapters", "budget_calls", "budget_time"}
_BUDGET_TEXT = {
    "budget_chapters": "本次章数上限已到",
    "budget_calls": "本次 LLM 调用上限已到",
    "budget_time": "本次时长上限已到",
}
SNAPSHOT_FILE = "index.json"


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _item(kind: str, severity: str, step: Dict[str, Any] | None, message: str, action: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "step_id": (step or {}).get("id"),
        "title": (step or {}).get("title") or "",
        "chapter": (step or {}).get("chapter") or None,
        "message": message,
        "action": action,
    }


def collect(ws: Workspace) -> Dict[str, Any]:
    """计算收件箱（只读，不写文件）。"""
    settings = ws.settings()
    plan = P.build_plan(ws)
    states = st.load_state(ws)["steps"]
    rows = {s.id: s for s in plan}
    items: List[Dict[str, Any]] = []
    stale_ids: List[str] = []

    for step in plan:
        entry = states.get(step.id, {})
        status = entry.get("status", st.ST_PENDING)
        view = {"id": step.id, "title": step.title, "chapter": step.chapter}
        if status == st.ST_WAITING_REVIEW:
            items.append(_item(KIND_APPROVE, SEVERITY_WARN, view, "等待你批准这一步的产出", "approve"))
        elif status == st.ST_WAITING_USER:
            if step.handler == "revise_loop":
                items.append(_item(KIND_ESCALATE, SEVERITY_BLOCK, view,
                                   "修改循环 3 轮后仍有 🔴，已停止推进下一章", "answer"))
            else:
                items.append(_item(KIND_ANSWER, SEVERITY_WARN, view, "需要你回答一个问题后才能继续", "answer"))
        elif status == st.ST_FAILED:
            retried = int(entry.get("auto_retries") or 0)
            suffix = f"（已自动重跑 {retried} 次仍失败）" if retried else ""
            items.append(_item(KIND_FAILED, SEVERITY_BLOCK, {**view, "error": entry.get("error")},
                               f"执行失败{suffix}：{entry.get('error') or '未知原因'}", "rerun"))
        elif status == st.ST_BLOCKED:
            items.append(_item(KIND_BLOCKED, SEVERITY_BLOCK, view,
                               entry.get("error") or "执行器尚未实现", "manual"))
        elif status == st.ST_STALE:
            stale_ids.append(step.id)

    if stale_ids:
        items.append(_item(KIND_STALE, SEVERITY_INFO, None,
                           f"{len(stale_ids)} 个步骤因上游变化需要重跑（{'、'.join(stale_ids[:3])}"
                           f"{'…' if len(stale_ids) > 3 else ''}）", "run"))

    worker = st.read_worker(ws)
    if not st.worker_alive(ws) and worker.get("reason") in _BUDGET_REASONS:
        reason = worker.get("reason")
        items.append(_item(KIND_BUDGET, SEVERITY_INFO, None,
                           f"{_BUDGET_TEXT[reason]}，已暂停（可直接续跑）", "run"))

    items.sort(key=lambda it: (_SEVERITY_ORDER.get(it["severity"], 9), it["kind"]))
    counts: Dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return {
        "workspace": str(ws.root),
        "gear": settings.get("gear"),
        "generated_at": _now(),
        "total": len(items),
        "blocking": sum(1 for it in items if it["severity"] == SEVERITY_BLOCK),
        "counts": counts,
        "items": items,
        "unknown_steps": [sid for sid in states if sid not in rows],
    }


def refresh(ws: Workspace) -> Dict[str, Any]:
    """计算并写入快照 `_studio/inbox/index.json`。返回收件箱内容。"""
    data = collect(ws)
    atomic_write_json(ws.path("studio_inbox") / SNAPSHOT_FILE, data)
    return data


def read_snapshot(ws: Workspace) -> Dict[str, Any] | None:
    data = read_json(ws.path("studio_inbox") / SNAPSHOT_FILE, default=None)
    return data if isinstance(data, dict) else None
