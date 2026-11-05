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
