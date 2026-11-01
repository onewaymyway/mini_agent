"""runtime.py — 队列的后台运行与实时状态（驾驶舱读取）。

  * start_background：在后台线程中运行队列，持有项目锁（runlock）；锁被占用时立即拒绝；
  * 运行状态写入 logs/runtime.json：state、当前章节、当前消息、各章结果、时间戳；
  * request_stop：写入停止请求，队列在下一章开始前检查并停止（已开始的章节会完整跑完，不会中途切断）；
  * 进程若在运行中退出，遗留锁与 running 状态由 runlock 在下次运行前回收。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import runlock
from .project_store import read_json, write_json_atomic

STATE_FILE = "logs/runtime.json"
STOP_FILE = "logs/stop_request.json"
HISTORY_LIMIT = 50


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_state(project_dir: Path) -> Dict[str, Any]:
    p = project_dir / STATE_FILE
    if not p.is_file():
        return {"state": "idle"}
    return read_json(p)


def update(project_dir: Path, **fields: Any) -> Dict[str, Any]:
    st = read_state(project_dir)
    st.update(fields)
    st["updated_at"] = _now()
    write_json_atomic(project_dir / STATE_FILE, st)
    return st


def note_chapter(project_dir: Path, no: int, res: Dict[str, Any]) -> None:
    """每章结束时调用：记录结果、刷新锁心跳。"""
    st = read_state(project_dir)
    hist = st.get("history", [])
    hist.append({"chapter": no, "status": res.get("status"), "message": res.get("message", ""), "at": _now()})
    st.update(chapter=None, phase="between", message=f"第 {no} 章：{res.get('status')}",
              history=hist[-HISTORY_LIMIT:], updated_at=_now())
    write_json_atomic(project_dir / STATE_FILE, st)
    runlock.heartbeat(project_dir)


def begin_chapter(project_dir: Path, no: int) -> None:
    update(project_dir, chapter=no, phase="running", message=f"正在运行第 {no} 章")
    runlock.heartbeat(project_dir)


def stop_requested(project_dir: Path) -> bool:
    return (project_dir / STOP_FILE).is_file()


def request_stop(project_dir: Path) -> None:
    write_json_atomic(project_dir / STOP_FILE, {"at": _now()})
    st = read_state(project_dir)
    if st.get("state") == "running":
        update(project_dir, state="stopping", message="已请求停止，将在当前章节完成后停止")


def clear_stop(project_dir: Path) -> None:
    (project_dir / STOP_FILE).unlink(missing_ok=True)


_clear_stop = clear_stop  # 兼容旧名称


def start_background(project_dir: Path, numbers: List[int], gear: str,
                     run_fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """启动后台运行。run_fn() 执行实际队列并返回 tool_api 风格的结果（{ok, data, error}）。
    返回 {ok, error}：锁被占用时 ok=False，不启动线程。"""
    try:
        runlock.acquire(project_dir)
    except runlock.LockBusy as exc:
        return {"ok": False, "error": str(exc)}
    _clear_stop(project_dir)
    update(project_dir, state="running", phase="starting", gear=gear, numbers=numbers,
           chapter=None, message="准备运行", started_at=_now(), history=[], results={})

    def worker() -> None:
        final: Dict[str, Any] = {"state": "failed", "phase": "finished", "chapter": None}
        try:
            res = run_fn()
            if res.get("ok"):
                data = res["data"]
                results = {str(k): v.get("status") for k, v in data.get("results", {}).items()}
                halted = data.get("halted")
                final.update(results=results, halted=halted,
                             state="halted" if halted else "done",
                             message=f"停止：{halted}" if halted else "队列已完成")
            else:
                final.update(message=res.get("error", {}).get("message", "运行失败"), error=True)
        except Exception as exc:  # 后台线程不能静默失败
            final.update(message=f"运行异常：{type(exc).__name__}: {exc}", error=True)
        finally:
            try:
                update(project_dir, **final, finished_at=_now())
            finally:
                _clear_stop(project_dir)
                runlock.release(project_dir)

    threading.Thread(target=worker, name=f"n2v-run-{project_dir.name}", daemon=True).start()
    return {"ok": True, "error": None}
