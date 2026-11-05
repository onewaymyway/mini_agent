"""状态恢复：清除误判的「需重跑」标记，回到流水线正确的位置。

界面（驾驶舱「🛟 状态恢复」）与 entrypoints/recover_state.py 共用这里的逻辑。
约定：
  - preview() 只读，不改任何东西。
  - apply() 先备份，再清除修改自动重跑标记、清理残留运行状态，并可选地把
    「上游产物已变化」类的需重跑步骤以当前文件重新基线为已通过。
  - 不运行任何步骤，也不改写任何产物文件内容。
  - worker 仍在运行时拒绝操作。
"""

from __future__ import annotations

import datetime as _dt
import shutil
from pathlib import Path
from typing import Any, Dict, List

from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer.workspace import Workspace

BACKUP_KEYS = ("studio_state", "studio_worker", "studio_control")


class RecoveryError(RuntimeError):
    pass


def _upstream_note() -> str:
    return st._UPSTREAM_NOTE


def _stale_rows(ws: Workspace) -> List[Dict[str, Any]]:
    rows = []
    for sid, entry in st.load_state(ws)["steps"].items():
        if entry.get("status") == st.ST_STALE:
            note = str(entry.get("note") or "")
            rows.append({
                "id": sid,
                "note": note,
                "updated_at": entry.get("updated_at") or "",
                "upstream": note.startswith(_upstream_note()),
            })
    return sorted(rows, key=lambda r: r["id"])


def preview(ws: Workspace) -> Dict[str, Any]:
    """现状（只读）。"""
    from novel_writer import tool_api  # 延迟导入，避免循环依赖

    ctl = st.read_control(ws)
    worker = st.read_worker(ws)
    rows = _stale_rows(ws)
    nxt = tool_api.next_step(str(ws.root))
    data = nxt.get("data") or {}
    return {
        "revision_autorun": bool(ctl.get("revision_autorun")),
        "revision_autorun_chapter": ctl.get("revision_autorun_chapter"),
        "worker_state": worker.get("state"),
        "worker_alive": st.worker_alive(ws),
        "stale": rows,
        "stale_upstream_count": sum(1 for r in rows if r["upstream"]),
        "next_step": data.get("step"),
        "next_title": data.get("title"),
        "next_action": data.get("action"),
    }


def backup(ws: Workspace) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = ws.studio / f"recovery_backup_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for key in BACKUP_KEYS:
        src = ws.path(key)
        if src.exists():
            shutil.copy2(src, dest / src.name)
    return dest


def apply(ws: Workspace, *, accept_stale: bool) -> Dict[str, Any]:
    """备份后清除标记。accept_stale=True 时，把「上游产物已变化」的需重跑步骤重新基线为已通过。"""
    if st.worker_alive(ws):
        raise RecoveryError("worker 仍在运行（心跳正常）。请先点「停止」，等它结束后再操作。")

    backup_dir = backup(ws)
    result: Dict[str, Any] = {"backup": str(backup_dir), "cleared_flags": False,
                              "cleared_worker": False, "rebaselined": []}

    st.write_control(ws, revision_autorun=False, revision_autorun_chapter=None)
    result["cleared_flags"] = True

    if st.read_worker(ws).get("state") == "running":
        st.release_worker(ws, "manual_clear")
        st.log_line(ws, "⏹ 已清除残留的运行状态（状态恢复，作者手动）")
        result["cleared_worker"] = True

    if accept_stale:
        plan = P.build_plan(ws)
        ids = [r["id"] for r in _stale_rows(ws) if r["upstream"]]
        result["rebaselined"] = st.rebaseline_stale(ws, plan, ids)
        if result["rebaselined"]:
            st.log_line(ws, f"🩹 状态恢复：作者确认 {len(result['rebaselined'])} 个需重跑步骤的产物无误，已重新基线")
    return result


# ── 跳到最新进度 ──────────────────────────────────────────────────────────

JUMP_NOTE = "状态恢复：跳到最新进度，视为已完成（未运行）"
_BLOCKING = (st.ST_WAITING_REVIEW, st.ST_WAITING_USER, st.ST_RUNNING)


def jump_preview(ws: Workspace) -> Dict[str, Any]:
    """计算「跳到最新进度」的范围（只读）。

    最新进度 = 计划顺序中最后一个已完成的步骤；目标 = 它之后第一个未完成的步骤（即下一步要运行的）。
    目标之前所有未完成的步骤（待运行、需重跑、失败、中断）都会被视为已完成，不会运行。
    """
    plan = P.build_plan(ws)
    states = st.load_state(ws)["steps"]

    def status_of(i: int) -> str:
        return (states.get(plan[i].id) or {}).get("status", st.ST_PENDING)

    finished = [i for i in range(len(plan)) if status_of(i) in st.FINISHED_STATUSES]
    if not finished:
        return {"target": None, "reason": "还没有任何已完成的步骤，无法判断最新进度。", "to_mark": [], "blockers": []}
    last = max(finished)
    target = next((i for i in range(last + 1, len(plan)) if status_of(i) not in st.FINISHED_STATUSES), None)
    if target is None:
        return {"target": None, "reason": "最新已完成步骤之后没有待运行的步骤。", "to_mark": [], "blockers": []}

    blockers = [{"id": plan[i].id, "title": plan[i].title, "status": status_of(i)}
                for i in range(target) if status_of(i) in _BLOCKING]
    to_mark = [{"id": plan[i].id, "title": plan[i].title, "status": status_of(i)}
               for i in range(target) if status_of(i) not in st.FINISHED_STATUSES and status_of(i) not in _BLOCKING]
    return {
        "target": plan[target].id,
        "target_title": plan[target].title,
        "last_finished": plan[last].id,
        "last_finished_title": plan[last].title,
        "to_mark": to_mark,
        "blockers": blockers,
        "reason": None,
    }


def jump_apply(ws: Workspace) -> Dict[str, Any]:
    """备份后，把目标之前的未完成步骤标为已完成。不运行任何步骤，不改写产物文件。"""
    if st.worker_alive(ws):
        raise RecoveryError("worker 仍在运行（心跳正常）。请先点「停止」，等它结束后再操作。")
    info = jump_preview(ws)
    if info["target"] is None:
        raise RecoveryError(info["reason"])
    if info["blockers"]:
        names = "、".join(b["title"] for b in info["blockers"][:5])
        raise RecoveryError(f"目标之前还有等待你处理的步骤（{names}），请先在步骤表中处理它们。")

    backup_dir = backup(ws)
    for item in info["to_mark"]:
        st.update_step(ws, item["id"], status=st.ST_PASSED, error=None, note=JUMP_NOTE)
    st.write_control(ws, revision_autorun=False, revision_autorun_chapter=None)
    st.log_line(ws, f"⏩ 状态恢复：跳到最新进度，{len(info['to_mark'])} 个步骤视为已完成，下一步 {info['target']}")
    return {"backup": str(backup_dir), "marked": [i["id"] for i in info["to_mark"]], "target": info["target"]}
