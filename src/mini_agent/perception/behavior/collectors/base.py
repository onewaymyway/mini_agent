"""
perception/behavior/collectors/base.py — 采集器统一接口

新增一个采集器只需要：
  1. 继承 BaseCollector
  2. 实现 poll() -> list[ActivityEvent] | None（单次采样，返回本次产生的事件）
  3. 在 manager.py 里按配置开关注册

BaseCollector 本身负责轮询线程的启动/停止，子类只关心"采一次样"这件事，
不需要各自管理线程生命周期，方便横向扩展。
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from ..events import ActivityEvent, BehaviorEventStore


class BaseCollector:
    name: str = "base"

    def __init__(self, store: BehaviorEventStore, interval_sec: float = 2.0) -> None:
        self._store = store
        self._interval = interval_sec
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

    def poll(self) -> Optional[list[ActivityEvent]]:
        """子类实现：采一次样，返回本次产生的事件列表（可以为空列表/None）。"""
        raise NotImplementedError

    def _run_loop(self) -> None:
        while not self._stop_flag.is_set():
            try:
                events = self.poll()
                if events:
                    self._store.append_many(events)
            except Exception:
                # 采集器故障不应影响主流程；静默跳过本轮。
                pass
            self._stop_flag.wait(self._interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name=f"behavior-{self.name}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
