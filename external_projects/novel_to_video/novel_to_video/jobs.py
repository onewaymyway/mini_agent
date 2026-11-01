"""jobs.py — 后台任务（全书素材、全书合成、性别检索等）。

与 runtime（章节队列）共用项目锁，因此同一项目同一时间只能运行一个任务。
运行状态写入 logs/job_state.json；过程日志写入 logs/job.jsonl（见 joblog）。
停止：写入停止标记，任务在下一个实体/步骤开始前检查并退出。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict

from . import joblog, runlock
from .project_store import read_json, write_json_atomic
from . import runtime as rt

STATE_FILE = "logs/job_state.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def status(project_dir: Path) -> Dict[str, Any]:
    p = Path(project_dir) / STATE_FILE
    return read_json(p) if p.is_file() else {"state": "idle"}


def _set(project_dir: Path, **fields: Any) -> None:
    st = status(project_dir)
    st.update(fields)
    write_json_atomic(Path(project_dir) / STATE_FILE, st)


def request_stop(project_dir: Path) -> Dict[str, Any]:
    if status(project_dir).get("state") != "running":
        return {"ok": False, "error": "当前没有运行中的任务"}
    write_json_atomic(Path(project_dir) / rt.STOP_FILE, {"at": _now()})
    joblog.log(project_dir, status(project_dir).get("scope", "system"),
               "已请求停止：当前实体或步骤完成后停止", level="warn")
    return {"ok": True, "error": None}


def _summarize(result: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ("done", "failed", "skipped"):
        if isinstance(result.get(k), list):
            out[k] = len(result[k])
    if result.get("stopped"):
        out["stopped"] = result["stopped"]
    return out


def start(project_dir: Path, *, name: str, label: str, scope: str,
          fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """启动后台任务。返回 {ok, error}。fn() 返回一个 dict（可含 done/failed/skipped/stopped）。"""
    project_dir = Path(project_dir)
    try:
        runlock.acquire(project_dir)
    except runlock.LockBusy as exc:
        return {"ok": False, "error": str(exc)}
    rt.clear_stop(project_dir)
    _set(project_dir, state="running", name=name, label=label, scope=scope,
         started_at=_now(), message="开始", summary={})
    joblog.log(project_dir, scope, f"开始：{label}", level="info")

    def worker() -> None:
        final: Dict[str, Any] = {"state": "failed"}
        try:
            result = fn()
            summary = _summarize(result)
            final = {"state": "done", "summary": summary}
            if result.get("stopped"):
                final["state"] = "stopped"
                joblog.log(project_dir, scope, f"已停止：{result['stopped']}", level="warn")
            elif any(k in summary for k in ("done", "failed", "skipped")):
                joblog.log(project_dir, scope,
                           f"完成：成功 {summary.get('done', 0)}，失败 {summary.get('failed', 0)}，"
                           f"跳过 {summary.get('skipped', 0)}", level="ok" if not summary.get("failed") else "warn")
            else:
                joblog.log(project_dir, scope, f"完成：{result.get('message', label)}", level="ok")
        except Exception as exc:  # 后台线程必须把异常记录下来，而不是静默消失
            final = {"state": "failed", "error": f"{type(exc).__name__}: {exc}"}
            joblog.log(project_dir, scope, f"运行异常：{exc}", level="error")
        finally:
            try:
                _set(project_dir, **final, finished_at=_now())
            finally:
                rt.clear_stop(project_dir)
                runlock.release(project_dir)

    threading.Thread(target=worker, name=f"n2v-job-{name}", daemon=True).start()
    return {"ok": True, "error": None}
