"""桌面客户端控制器：后台线程里维护与 daemon 的连接与状态。

流程（对应方案"先取快照，再订阅事件"）：
  循环：health → 取 status+summary 快照 → 订阅 SSE →（断线）→ 退避后重来。
  - SSE 事件到来：按需立即刷新 status/summary；`attention` 事件同时回调
    `on_attention`（S3 的 Toast 入口）；
  - 心跳（~20s 一次）顺带做兜底刷新：距上次 summary ≥ poll_interval 就重取，
    覆盖"别的客户端已读了汇报"以及 `attention` 事件被关闭的情况；
  - 断线后立刻读本地崩溃告警文件：daemon 已死时 HTTP 不可用，这是唯一
    能发现"刚崩溃过"的办法（崩溃告警不走 SSE，见 desktop-attention-events-guide）。

控制器不依赖 Qt；`on_change` 在后台线程中调用，UI 层需自行切回主线程。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import httpx

from mini_agent_desktop.api import DaemonApi
from mini_agent_desktop.state import REASON_AUTH, REASON_LOST, REASON_NOT_RUNNING, TrayModel

DEFAULT_BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
log = logging.getLogger("mini_agent_desktop.controller")


class DesktopController:
    def __init__(
        self,
        model: TrayModel,
        api_factory: Callable[[], DaemonApi],
        offline_crashes: Callable[[], int] = lambda: 0,
        on_change: Callable[[], None] = lambda: None,
        on_attention: Optional[Callable[[dict], None]] = None,
        start_daemon: Optional[Callable[[], None]] = None,
        auto_start_daemon: bool = False,
        poll_interval: float = 30.0,
        backoff: tuple = DEFAULT_BACKOFF,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self._api_factory = api_factory
        self._offline_crashes = offline_crashes
        self._on_change = on_change
        self._on_attention = on_attention
        self._start_daemon = start_daemon
        self._auto_start = auto_start_daemon
        self._poll_interval = poll_interval
        self._backoff = backoff
        self._clock = clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_summary_at = 0.0
        self._auto_start_tried = False
        # S3：崩溃提醒的基线。启动时记下已有的未确认告警数，之后只在数量增长时提醒，
        # 避免每次启动都为旧告警弹一次 Toast。
        self._crash_baseline: Optional[int] = None

    # ── 生命周期 ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._crash_baseline = self._read_crashes()
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="desktop-controller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def reconnect_now(self) -> None:
        """跳过当前退避等待，立刻重试（托盘"重新连接"/"启动 Agent"之后用）。"""
        self._wake.set()

    def manual_start_daemon(self) -> None:
        if self._start_daemon is not None:
            self._start_daemon()
        self.reconnect_now()

    # ── 主循环 ────────────────────────────────────────────────────────────
    def run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            was_connected = False
            try:
                api = self._api_factory()
            except Exception:  # noqa: BLE001 - 发现阶段出错也不能让线程死掉
                self._go_offline(REASON_LOST)
                api = None
            if api is not None:
                try:
                    was_connected = self._session(api)
                finally:
                    api.close()
            attempt = 0 if was_connected else attempt + 1
            self._sleep(self._backoff[min(max(attempt - 1, 0), len(self._backoff) - 1)])

    def _sleep(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _read_crashes(self) -> int:
        try:
            return int(self._offline_crashes())
        except Exception:  # noqa: BLE001
            return 0

    def _go_offline(self, reason: str) -> None:
        crashes = self._read_crashes()
        log.info("离线：%s（未确认崩溃告警 %d 条）", reason, crashes)
        self._model.set_offline(reason, crashes)
        if (self._on_attention is not None and self._crash_baseline is not None
                and crashes > self._crash_baseline):
            self._crash_baseline = crashes
            try:
                self._on_attention({
                    "kind": "daemon_crash",
                    "title": f"daemon 异常退出，未确认告警 {crashes} 条",
                    "ref_id": f"crash-{crashes}",
                    "level": "blocking",
                    "extra": {},
                })
            except Exception:  # noqa: BLE001
                pass
        self._on_change()

    def _refresh(self, api: DaemonApi, *, status: bool, summary: bool) -> None:
        if status:
            self._model.apply_status(api.status())
        if summary:
            self._model.apply_summary(api.summary())
            self._last_summary_at = self._clock()

    def _session(self, api: DaemonApi) -> bool:
        """一次完整连接周期。返回是否曾成功连上（用于重置退避）。"""
        if not api.health():
            self._go_offline(REASON_NOT_RUNNING)
            if self._auto_start and self._start_daemon and not self._auto_start_tried:
                self._auto_start_tried = True      # 每次离线只尝试一次，避免反复拉起
                try:
                    self._start_daemon()
                except Exception:  # noqa: BLE001
                    pass
            return False
        try:
            self._refresh(api, status=True, summary=True)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            self._go_offline(REASON_AUTH if code in (401, 403) else REASON_LOST)
            return False
        except httpx.HTTPError:
            self._go_offline(REASON_LOST)
            return False

        self._auto_start_tried = False
        log.info("已连接 daemon")
        self._model.set_connected()
        self._on_change()
        try:
            for ev in api.stream_events(self._stop):
                if self._stop.is_set():
                    return True
                self._handle_event(api, ev)
        except httpx.HTTPError:
            pass
        if not self._stop.is_set():
            self._go_offline(REASON_LOST)
        return True

    def _handle_event(self, api: DaemonApi, ev) -> None:
        if ev.event == "heartbeat":
            if self._clock() - self._last_summary_at >= self._poll_interval:
                self._refresh(api, status=True, summary=True)
                self._on_change()
            return
        need = self._model.apply_event(ev.event, ev.data)
        if ev.event == "attention" and self._on_attention is not None:
            try:
                self._on_attention(ev.data)
            except Exception:  # noqa: BLE001 - 回调失败不能打断事件循环
                pass
        if need:
            self._refresh(api, status="status" in need, summary="summary" in need)
        self._on_change()
