"""cancel.py — 立即停止：项目级取消信号、可中断的阻塞调用、可终止的子进程。

问题背景：原来的停止只在“章节之间 / 实体之间”检查停止文件，正在进行的视频轮询、
模型请求、配音或 ffmpeg 会一直跑到自己结束，界面上的“请求停止”不会马上生效。

本模块提供四个机制：

  * begin(project_dir)：每轮运行开始时换一个新的取消信号对象。旧信号保持置位，
    因此上一轮遗留的后台线程仍然看到“已停止”，不会在新一轮里继续发请求或写文件。
  * request(project_dir)：立即置位当前信号，并终止该项目登记的所有子进程（ffmpeg 等）。
  * call(fn, ...)：在 helper 线程里执行一个阻塞调用（模型请求、视频/图像/TTS 接口），
    调用线程每 0.2 秒检查一次信号；一旦停止，立即抛出 Cancelled，不再等待网络返回。
    helper 线程的结果被丢弃；helper 线程继承调用方的 joblog 绑定，日志照常写入。
  * run_process(args)：替代 subprocess.run，等待时检查信号，停止时 kill 子进程。

Cancelled 继承 BaseException：流水线里大量 `except Exception` 不会把它当作普通失败吞掉，
它会一直冒泡到 run_chapter / batch / 后台任务，由那里统一按“已手动停止”处理。
"""

from __future__ import annotations

import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import joblog

POLL_SEC = 0.2
STOP_MESSAGE = "用户请求停止"


class Cancelled(BaseException):
    """用户请求停止。BaseException：不会被各处 except Exception 当作普通失败吞掉。"""


_LOCK = threading.Lock()
_EVENTS: Dict[str, threading.Event] = {}
_PROCS: Dict[str, List[subprocess.Popen]] = {}
_TLS = threading.local()


def _key(project_dir: Any) -> str:
    return str(Path(project_dir).resolve())


def begin(project_dir: Any) -> threading.Event:
    """新一轮运行开始：换一个新的信号对象（旧对象保持原状态，旧线程不会“复活”）。"""
    k = _key(project_dir)
    with _LOCK:
        ev = threading.Event()
        _EVENTS[k] = ev
        _PROCS[k] = []
    return ev


def event_for(project_dir: Any) -> threading.Event:
    k = _key(project_dir)
    with _LOCK:
        return _EVENTS.setdefault(k, threading.Event())


def is_requested(project_dir: Any) -> bool:
    """当前项目是否已请求停止（信号置位，或跨进程停止文件存在）。"""
    if event_for(project_dir).is_set():
        return True
    return (Path(project_dir) / "logs" / "stop_request.json").is_file()


def request(project_dir: Any) -> int:
    """立即请求停止：置位信号并终止登记的子进程。返回被终止的子进程数。"""
    k = _key(project_dir)
    with _LOCK:
        ev = _EVENTS.setdefault(k, threading.Event())
        procs = list(_PROCS.get(k, []))
    ev.set()
    killed = 0
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.kill()
                killed += 1
            except OSError:
                pass
    return killed


@contextmanager
def bind(project_dir: Any) -> Iterator[threading.Event]:
    """在当前线程绑定项目的取消信号（后台任务与队列线程使用）。"""
    prev = getattr(_TLS, "ctx", None)
    key = _key(project_dir)
    ev = event_for(project_dir)
    _TLS.ctx = (ev, key)
    try:
        yield ev
    finally:
        _TLS.ctx = prev


def current_event() -> Optional[threading.Event]:
    ctx = getattr(_TLS, "ctx", None)
    return ctx[0] if ctx else None


def _current_key() -> Optional[str]:
    ctx = getattr(_TLS, "ctx", None)
    return ctx[1] if ctx else None


def check() -> None:
    """检查点：已请求停止则立即抛出 Cancelled。未绑定项目的线程不受影响。"""
    ev = current_event()
    if ev is not None and ev.is_set():
        raise Cancelled(STOP_MESSAGE)


def sleep(seconds: float) -> None:
    """可中断的等待：停止时立即抛出 Cancelled。"""
    ev = current_event()
    if ev is None:
        time.sleep(seconds)
        return
    if ev.wait(seconds):
        raise Cancelled(STOP_MESSAGE)


def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在 helper 线程执行阻塞调用；停止时立即返回（抛出 Cancelled），结果被丢弃。

    未绑定项目时直接同步调用，行为与普通函数一致。
    """
    ev = current_event()
    if ev is None:
        return fn(*args, **kwargs)
    check()
    ctx = joblog.current_ctx()
    box: Dict[str, Any] = {}
    done = threading.Event()

    def runner() -> None:
        try:
            if ctx is not None:
                with joblog.bind(*ctx):
                    box["value"] = fn(*args, **kwargs)
            else:
                box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - 原样转交调用线程
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=runner, name="n2v-call", daemon=True).start()
    while not done.wait(POLL_SEC):
        if ev.is_set():
            raise Cancelled(STOP_MESSAGE + "（已中断等待中的请求，返回结果将被丢弃）")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _register(key: Optional[str], proc: subprocess.Popen) -> None:
    if key is None:
        return
    with _LOCK:
        _PROCS.setdefault(key, []).append(proc)


def _unregister(key: Optional[str], proc: subprocess.Popen) -> None:
    if key is None:
        return
    with _LOCK:
        procs = _PROCS.get(key, [])
        if proc in procs:
            procs.remove(proc)


def run_process(args: List[str]) -> Tuple[int, str, str]:
    """运行外部命令（ffmpeg / ffprobe）。停止时立即终止子进程并抛出 Cancelled。

    返回 (returncode, stdout, stderr)，与 subprocess.run(capture_output=True, text=True) 一致。
    """
    ev = current_event()
    if ev is None:
        proc = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout, proc.stderr
    check()
    key = _current_key()
    proc = subprocess.Popen(list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    _register(key, proc)
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=POLL_SEC)
                if ev.is_set():   # 被 request() 终止的进程也会正常返回：此时不能当作普通结果
                    raise Cancelled(STOP_MESSAGE + "（已终止正在运行的子进程）")
                return proc.returncode, out or "", err or ""
            except subprocess.TimeoutExpired:
                if ev.is_set():
                    proc.kill()
                    try:
                        proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    raise Cancelled(STOP_MESSAGE + "（已终止正在运行的子进程）")
    finally:
        _unregister(key, proc)
