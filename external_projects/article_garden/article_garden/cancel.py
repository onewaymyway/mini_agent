"""cancel.py — 立即停止：按项目登记的停止信号；阻塞调用（模型请求）每 0.2 秒检查一次。

  * begin(project_dir)：一轮运行开始时换一个新信号，旧线程仍看到旧信号（已置位），不会“复活”。
  * request(project_dir)：置位当前信号。
  * bind(project_dir)：后台线程开始时绑定信号；之后该线程里的 call()/check() 都受其控制。
  * call(fn, ...)：在 helper 线程执行阻塞调用，调用线程每 0.2 秒检查信号，停止即抛 Cancelled，结果丢弃。

Cancelled 继承 BaseException：流水线中的 except Exception 不会把它当作普通失败吞掉。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict

POLL_SEC = 0.2
_LOCK = threading.Lock()
_EVENTS: Dict[str, threading.Event] = {}
_TLS = threading.local()


class Cancelled(BaseException):
    """用户请求停止。"""


def _key(project_dir: Any) -> str:
    return str(Path(project_dir).resolve())


def begin(project_dir: Any) -> threading.Event:
    with _LOCK:
        ev = threading.Event()
        _EVENTS[_key(project_dir)] = ev
        return ev


def request(project_dir: Any) -> None:
    with _LOCK:
        ev = _EVENTS.get(_key(project_dir))
    if ev is not None:
        ev.set()


def is_requested(project_dir: Any) -> bool:
    with _LOCK:
        ev = _EVENTS.get(_key(project_dir))
    return bool(ev and ev.is_set())


def bind(event: threading.Event) -> None:
    _TLS.event = event


def _current() -> threading.Event:
    return getattr(_TLS, "event", None) or threading.Event()


def check() -> None:
    if _current().is_set():
        raise Cancelled("用户请求停止")


def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在 helper 线程执行 fn；调用线程轮询停止信号。停止后丢弃结果并抛 Cancelled。"""
    ev = _current()
    if ev.is_set():
        raise Cancelled("用户请求停止")
    box: Dict[str, Any] = {}

    def run() -> None:
        bind(ev)
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — 原样转交给调用线程
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    while t.is_alive():
        t.join(POLL_SEC)
        if ev.is_set():
            raise Cancelled("用户请求停止")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def sleep(seconds: float) -> None:
    """可中断的等待（重试等待用）：停止时立即抛出 Cancelled。"""
    ev = _current()
    if ev.wait(seconds):
        raise Cancelled("用户请求停止")
