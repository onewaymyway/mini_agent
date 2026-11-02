"""runtime.py — 队列的后台运行与实时状态（驾驶舱读取）。

  * start_background：在后台线程中运行队列，持有项目锁（runlock）；锁被占用时立即拒绝；
  * 运行状态写入 logs/runtime.json：state、当前章节、当前消息、各章结果、时间戳；
  * request_stop：立即请求停止：置位取消信号、终止正在运行的 ffmpeg 等子进程，并中断等待中的模型/视频/配音请求；
    运行线程在下一个检查点退出，章节状态回到可重跑的断点；
  * 进程若在运行中退出，遗留锁与 running 状态由 runlock 在下次运行前回收。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import cancel, runlock
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
    return cancel.is_requested(project_dir)


def request_stop(project_dir: Path) -> None:
    """立即停止：不再等待当前章节跑完。"""
    write_json_atomic(project_dir / STOP_FILE, {"at": _now(), "immediate": True})
    killed = cancel.request(project_dir)
    st = read_state(project_dir)
    if st.get("state") in ("running", "stopping"):
        update(project_dir, state="stopping",
               message=f"已请求立即停止，正在中断当前请求（终止子进程 {killed} 个）")


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
    cancel.begin(project_dir)   # 新一轮：新的取消信号，旧线程仍看到上一轮的停止
    update(project_dir, state="running", phase="starting", gear=gear, numbers=numbers,
           chapter=None, message="准备运行", started_at=_now(), history=[], results={})

    def worker() -> None:
        final: Dict[str, Any] = {"state": "failed", "phase": "finished", "chapter": None}
        try:
            from . import joblog
            with cancel.bind(project_dir), joblog.bind(project_dir, "chapter"):  # 章节运行中 skill 的输出写入章节日志
                res = run_fn()
            if res.get("ok"):
                data = res["data"]
                results = {str(k): v.get("status") for k, v in data.get("results", {}).items()}
                halted = data.get("halted")
                if halted == cancel.STOP_MESSAGE:
                    final.update(results=results, halted=halted, state="stopped",
                                 message="已立即停止；运行中的章节已回到断点，重新运行即可继续")
                else:
                    final.update(results=results, halted=halted,
                                 state="halted" if halted else "done",
                                 message=f"停止：{halted}" if halted else "队列已完成")
            else:
                final.update(message=res.get("error", {}).get("message", "运行失败"), error=True)
        except cancel.Cancelled:  # 用户立即停止：不是错误，不写入失败原因
            from . import joblog as _jl
            final.update(state="stopped", message="已立即停止；运行中的章节已回到断点，重新运行即可继续")
            _jl.log(project_dir, "queue", "队列已立即停止（用户请求）", level="warn")
        except Exception as exc:  # 后台线程不能静默失败：原因同时写入章节/队列日志
            import traceback as _tb
            from . import joblog as _jl
            final.update(message=f"运行异常：{type(exc).__name__}: {exc}", error=True)
            _jl.log(project_dir, "queue", f"队列异常中止：{type(exc).__name__}：{exc}\n"
                    + "\n".join(_tb.format_exc().strip().splitlines()[-12:]), level="error")
        finally:
            try:
                update(project_dir, **final, finished_at=_now())
            finally:
                _clear_stop(project_dir)
                runlock.release(project_dir)

    threading.Thread(target=worker, name=f"n2v-run-{project_dir.name}", daemon=True).start()
    return {"ok": True, "error": None}
