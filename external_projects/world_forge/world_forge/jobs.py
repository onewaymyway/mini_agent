"""world_forge/jobs.py — 后台任务：在独立线程中运行长操作，支持实时进度、停止与结果回收。

看板通过 jobs.start() 提交任务，通过 jobs.latest_for() / jobs.get() 轮询状态与日志，
通过 jobs.request_stop() 发送停止信号。同一个世界同一时间只允许一个运行中的任务，避免写入冲突。
任务状态：running → done / stopped / error。
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from . import cancel
from .runlog import RunLog


class JobBusy(RuntimeError):
    """同一世界已有运行中的任务。"""


class Job:
    def __init__(self, wid: str, kind: str, label: str, result_key: Optional[str]):
        self.id = uuid.uuid4().hex[:8]
        self.wid = wid
        self.kind = kind
        self.label = label
        self.result_key = result_key
        self.status = "running"
        self.entries: List[dict] = []
        self.result: Any = None
        self.error: Optional[str] = None
        self.started = time.time()
        self.finished: Optional[float] = None
        self.delivered = False
        self.stop_requested = False
        self.log: Optional[RunLog] = None
        self._event = threading.Event()

    def push(self, entry: dict) -> None:
        self.entries.append(entry)

    @property
    def running(self) -> bool:
        return self.status == "running"

    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started


_JOBS: Dict[str, Job] = {}
_LOCK = threading.Lock()


def start(wid: str, kind: str, label: str, fn: Callable[..., Any], *args,
          result_key: Optional[str] = None, **kwargs) -> Job:
    """在后台线程中运行 fn(*args, log=..., **kwargs)。fn 需接受 log 参数。"""
    with _LOCK:
        busy = [j for j in _JOBS.values() if j.wid == wid and j.running]
        if busy:
            raise JobBusy(f"「{busy[0].label}」仍在运行，请先等待或停止它")
        job = Job(wid, kind, label, result_key)
        _JOBS[job.id] = job

    log = RunLog(wid, kind, sink=job.push)
    job.log = log

    def target() -> None:
        cancel.bind(job._event)
        try:
            job.result = fn(*args, log=log, **kwargs)
            stopped = isinstance(job.result, dict) and bool(job.result.get("cancelled"))
            job.status = "stopped" if stopped else "done"
        except cancel.Cancelled:
            job.status = "stopped"
            log.log("cancel", "已停止：操作已在安全点结束，已完成的部分保留", level="warn")
        except Exception as exc:  # noqa: BLE001 - 后台线程的异常要转为任务状态
            job.status = "error"
            job.error = f"{type(exc).__name__}：{exc}"
            log.log("error", f"任务失败：{job.error}", level="error")
        finally:
            job.finished = time.time()
            cancel.unbind()

    threading.Thread(target=target, daemon=True, name=f"wf-job-{job.id}").start()
    return job


def get(job_id: str) -> Optional[Job]:
    return _JOBS.get(job_id)


def latest_for(wid: Optional[str]) -> Optional[Job]:
    """某世界最近的任务：优先返回运行中的，否则返回最近结束且尚未交付结果的，再否则最近一个。"""
    if not wid:
        return None
    with _LOCK:
        mine = [j for j in _JOBS.values() if j.wid == wid]
    if not mine:
        return None
    running = [j for j in mine if j.running]
    if running:
        return max(running, key=lambda j: j.started)
    return max(mine, key=lambda j: j.started)


def is_busy(wid: str) -> bool:
    with _LOCK:
        return any(j.wid == wid and j.running for j in _JOBS.values())


def request_stop(job_id: str) -> bool:
    job = _JOBS.get(job_id)
    if job is None or not job.running:
        return False
    job.stop_requested = True
    job._event.set()
    if job.log is not None:
        job.log.log("cancel", "已请求停止：将在当前模型调用或当前格完成后生效", level="warn")
    return True
