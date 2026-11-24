"""world_forge/cancel.py — 协作式停止信号。

后台任务线程绑定一个停止事件；引擎在安全点（每次模型调用前、每个步骤与每一格之间）调用 checkpoint()，
若已请求停止则抛出 Cancelled。Cancelled 继承 BaseException，确保不会被业务代码里的 `except Exception` 吞掉。
未绑定任务的线程（如命令行直接调用）调用 checkpoint() 是空操作。
"""

from __future__ import annotations

import threading
from typing import Optional


class Cancelled(BaseException):
    """用户请求停止。"""


_TLS = threading.local()


def bind(event: Optional[threading.Event]) -> None:
    _TLS.event = event


def unbind() -> None:
    _TLS.event = None


def requested() -> bool:
    ev = getattr(_TLS, "event", None)
    return bool(ev is not None and ev.is_set())


def checkpoint() -> None:
    if requested():
        raise Cancelled()
