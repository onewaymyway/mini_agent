"""novel_writer/tasks.py — 后台任务总览与管理（书架上所有小说）。

一行 = 一个任务，分四类：
  * running  ：后台 worker 正在运行（心跳正常）。可「停止」（协作式，当前步骤结束后生效）。
  * stale    ：worker 状态仍是「运行中」但心跳已超时（进程已不在）。可「清除残留」。
  * queued   ：已排队的单步运行指令（worker 忙或已退出）。可「取消排队」。
  * request  ：未完成的按需请求（修订 / 灵感 / AI 辅助），等待 ▶ 运行。可「删除」。

所有写操作都先检查状态：运行中的步骤不能删除，存活的 worker 不能被清除。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import batch as _batch
from novel_writer import ondemand as _ondemand
from novel_writer import shelf
from novel_writer import state as st
from novel_writer.workspace import Workspace, WorkspaceError

KIND_RUNNING = "running"
KIND_STALE = "stale"
KIND_QUEUED = "queued"
KIND_REQUEST = "request"

KIND_LABEL = {
    KIND_RUNNING: "运行中",
    KIND_STALE: "残留（心跳超时）",
    KIND_QUEUED: "排队中",
    KIND_REQUEST: "待运行请求",
}


class TaskError(WorkspaceError):
    pass


def _task(ws: Workspace, title: str, kind: str, **fields: Any) -> Dict[str, Any]:
    base = {
        "workspace": str(ws.root), "title": title, "kind": kind, "kind_label": KIND_LABEL[kind],
        "step": None, "step_title": None, "started_at": None, "mode": None, "reason": None,
        "request_kind": None, "request_id": None, "status": None, "detail": "",
    }
    base.update(fields)
    return base


def _request_rows(ws: Workspace, title: str) -> List[Dict[str, Any]]:
    rows = []
    for req in _ondemand.list_requests(ws):
        kind = req.get("kind")
        if kind not in _ondemand.REQUEST_STEP_PREFIX:
            continue
        sid = _ondemand.request_step_id(kind, req["id"])
        entry = st.get_step_state(ws, sid)
        status = entry.get("status", st.ST_PENDING)
        if status in st.FINISHED_STATUSES:
            continue
        label = {"revision": "修订", "idea": "灵感评估", "assist": "AI 辅助"}[kind]
        rows.append(_task(
            ws, title, KIND_REQUEST, step=sid, status=status,
            request_kind=kind, request_id=req["id"],
            step_title=req.get("instruction") or req.get("text") or req.get("mode") or "",
            detail=f"{label} · 创建于 {req.get('created_at', '')[:16]}",
        ))
    return rows


def list_tasks(novels_dir: Optional[Path] = None, index_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for root in shelf.discover(novels_dir, index_file):
        ws = Workspace(root)
        title = _batch._title(ws)
        info = st.read_worker(ws)
        alive = st.worker_alive(ws)
        if info.get("state") == "running":
            kind = KIND_RUNNING if alive else KIND_STALE
            rows.append(_task(
                ws, title, kind, step=info.get("step"), step_title=info.get("step_title"),
                started_at=info.get("step_started_at") or info.get("started_at"), mode=info.get("mode"),
                detail=f"心跳 {str(info.get('heartbeat') or '')[11:19]} · 第 {info.get('attempt') or 1} 次尝试",
            ))
        control = st.read_control(ws)
        queued = control.get("run_step")
        if queued:
            entry = st.get_step_state(ws, queued)
            if entry.get("status") not in st.FINISHED_STATUSES:
                rows.append(_task(
                    ws, title, KIND_QUEUED, step=queued, status=entry.get("status", st.ST_PENDING),
                    detail="单步运行指令已写入，等待执行",
                ))
        rows.extend(_request_rows(ws, title))
    return rows


def _resolve(workspace: str) -> Workspace:
    ws = Workspace(workspace)
    if not ws.is_initialized():
        raise TaskError(f"不是已初始化的工作目录：{workspace}")
    return ws


def cancel_queued(workspace: str) -> Dict[str, Any]:
    """取消排队中的单步运行指令（不影响正在运行的任务）。"""
    ws = _resolve(workspace)
    if not st.read_control(ws).get("run_step"):
        raise TaskError("当前没有排队中的运行指令")
    st.write_control(ws, run_step=None)
    st.log_line(ws, "⏹ 已取消排队中的运行指令（作者手动）")
    return {"cancelled": True}


def clear_stale_worker(workspace: str) -> Dict[str, Any]:
    """清除心跳超时的残留 worker 状态。存活的 worker 不能清除。"""
    ws = _resolve(workspace)
    info = st.read_worker(ws)
    if info.get("state") != "running":
        raise TaskError("没有残留的运行状态")
    if st.worker_alive(ws):
        raise TaskError("worker 仍在运行（心跳正常），不能清除；请使用「停止」")
    st.release_worker(ws, "manual_clear")
    st.log_line(ws, "⏹ 已清除残留的运行状态（心跳超时，作者手动）")
    return {"cleared": True}


def delete_request(workspace: str, request_kind: str, request_id: str) -> Dict[str, Any]:
    """删除一个未完成的按需请求。正在运行的请求不能删除。"""
    ws = _resolve(workspace)
    sid = _ondemand.request_step_id(request_kind, request_id)
    if st.get_step_state(ws, sid).get("status") == st.ST_RUNNING and st.worker_alive(ws):
        raise TaskError("这个请求正在运行，不能删除；请先停止当前任务")
    if st.read_control(ws).get("run_step") == sid:
        st.write_control(ws, run_step=None)
    try:
        _ondemand.delete_request(ws, request_kind, request_id)
    except _ondemand.OnDemandError as exc:
        raise TaskError(str(exc)) from exc
    st.log_line(ws, f"🗑 已删除按需请求 {sid}（作者手动）")
    return {"deleted": sid}
