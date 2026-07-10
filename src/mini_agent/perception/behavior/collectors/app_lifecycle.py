"""
perception/behavior/collectors/app_lifecycle.py — 应用启动/退出事件

跟前台窗口采集器互补：前台窗口只知道"当前在看哪个"，这个采集器能看到
"后台常驻程序什么时候起来/退出"（比如后台一直开着的聊天软件、下载工具），
只记录进程名 + 启停时间，不记录命令行参数（命令行参数可能带敏感信息，如
密码/token），不记录任何输出。
"""

from __future__ import annotations

import time
from typing import Optional

from ..events import ActivityEvent
from .base import BaseCollector


# 一些常见系统/内核进程会频繁短暂出现，噪音大且价值低，直接过滤掉
_NOISE_PREFIXES = ("kworker", "ksoftirqd", "migration", "rcu_", "watchdog", "conhost", "dllhost")


class AppLifecycleCollector(BaseCollector):
    name = "app_lifecycle"

    def __init__(self, store, interval_sec: float = 5.0) -> None:
        super().__init__(store, interval_sec)
        self._known: set[str] = set()
        self._initialized = False

    def _snapshot(self) -> set[str]:
        try:
            import psutil  # type: ignore
        except Exception:
            return set()
        names = set()
        for p in psutil.process_iter(["name"]):
            try:
                n = p.info.get("name") or ""
            except Exception:
                continue
            if not n or n.lower().startswith(_NOISE_PREFIXES):
                continue
            names.add(n)
        return names

    def poll(self):
        current = self._snapshot()
        if not current and not self._known:
            return None

        if not self._initialized:
            # 第一次采样只建立基线，不把"当前已经在跑的所有程序"都当作刚启动
            self._known = current
            self._initialized = True
            return None

        started = current - self._known
        stopped = self._known - current
        self._known = current

        events = []
        now = time.time()
        for name in started:
            events.append(ActivityEvent(timestamp=now, source="app_lifecycle", event_type="app_start", app_name=name))
        for name in stopped:
            events.append(ActivityEvent(timestamp=now, source="app_lifecycle", event_type="app_stop", app_name=name))
        return events or None

    def is_available(self) -> bool:
        try:
            import psutil  # noqa: F401
            return True
        except ImportError:
            return False
