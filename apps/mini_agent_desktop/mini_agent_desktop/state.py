"""托盘状态模型（纯逻辑，不依赖 Qt，便于单测）。

输入：连接状态、`/v1/status`、`/v1/desktop/summary`、SSE 事件。
输出：托盘图标状态、提示文字、菜单文案。线程安全由调用方（controller）保证：
所有写入都在 controller 的后台线程里，UI 线程只读 `menu_state()` 返回的快照。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum


class TrayIcon(str, Enum):
    IDLE = "idle"            # 正常，空闲
    WORKING = "working"      # 正在执行任务
    ATTENTION = "attention"  # 有事项需要用户处理
    ERROR = "error"          # 服务崩溃过（未确认的崩溃告警）
    OFFLINE = "offline"      # 连不上 daemon


# 触发"重新取 /v1/status"的事件
_STATUS_EVENTS = {"turn_start", "turn_done", "interrupt", "error", "status"}
# 触发"重新取 /v1/desktop/summary"的事件（待处理事项可能变化）。
# 刻意不含 objective_progress：它可能很频繁，失败状态靠兜底轮询发现即可。
_SUMMARY_EVENTS = {
    "permission_req", "permission_done", "interaction_req", "interaction_done",
    "attention", "cron_job_finished",
}

_COUNT_LABELS = (
    ("pending_permissions", "待审批"),
    ("pending_interactions", "待回答"),
    ("cron_questions_pending", "待反馈"),
    ("pending_reports", "未读汇报"),
    ("failed_objectives", "失败目标"),
    ("crash_alerts_unacked", "崩溃告警"),
)


@dataclass
class MenuState:
    icon: TrayIcon
    badge: int
    status_text: str
    tasks_text: str
    attention_text: str
    tooltip: str
    connected: bool
    can_start_daemon: bool   # 未连接且原因是"daemon 未运行"时才提供"启动 Agent"


@dataclass
class TrayModel:
    connected: bool = False
    reason: str = "连接中…"
    agent_state: str = "idle"           # idle | running | waiting_permission
    queue_depth: int = 0
    counts: dict = field(default_factory=dict)
    events_enabled: bool = False
    offline_crashes: int = 0            # 离线时直接读文件得到的未确认崩溃数
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── 输入 ──────────────────────────────────────────────────────────────
    def set_connected(self) -> None:
        with self._lock:
            self.connected = True
            self.reason = ""

    def set_offline(self, reason: str, crash_unacked: int = 0) -> None:
        with self._lock:
            self.connected = False
            self.reason = reason
            self.agent_state = "idle"
            self.queue_depth = 0
            self.counts = {}          # 离线时旧计数已不可信，别继续显示
            self.offline_crashes = max(0, int(crash_unacked))

    def apply_status(self, status: dict) -> None:
        with self._lock:
            self.agent_state = str(status.get("state") or "idle")
            self.queue_depth = int(status.get("queue_depth") or 0)

    def apply_summary(self, summary: dict) -> None:
        with self._lock:
            counts = summary.get("counts")
            self.counts = {k: int(v) for k, v in counts.items()} if isinstance(counts, dict) else {}
            self.events_enabled = bool(summary.get("attention_events_enabled"))

    def apply_event(self, event_type: str, data: dict) -> set[str]:
        """吸收一个 SSE 事件，返回需要刷新的数据集合 {"status","summary"}。
        turn_start/turn_done 先乐观更新 working 状态，随后的 status 刷新会校正。"""
        with self._lock:
            if event_type == "turn_start":
                self.agent_state = "running"
            elif event_type in ("turn_done", "interrupt", "error"):
                self.agent_state = "idle"
        need: set[str] = set()
        if event_type in _STATUS_EVENTS:
            need.add("status")
        if event_type in _SUMMARY_EVENTS:
            need.add("summary")
        return need

    # ── 输出 ──────────────────────────────────────────────────────────────
    def _attention_total(self) -> int:
        return sum(self.counts.values())

    def icon(self) -> TrayIcon:
        with self._lock:
            return self._icon_locked()

    def _icon_locked(self) -> TrayIcon:
        if not self.connected:
            return TrayIcon.ERROR if self.offline_crashes > 0 else TrayIcon.OFFLINE
        if self.counts.get("crash_alerts_unacked", 0) > 0:
            return TrayIcon.ERROR
        if self._attention_total() > 0:
            return TrayIcon.ATTENTION
        if self.agent_state == "running":
            return TrayIcon.WORKING
        return TrayIcon.IDLE

    def menu_state(self) -> MenuState:
        with self._lock:
            icon = self._icon_locked()
            total = self._attention_total()
            if not self.connected:
                status_text = f"状态：未连接（{self.reason}）"
                if self.offline_crashes > 0:
                    status_text += f"，有 {self.offline_crashes} 条未确认崩溃告警"
                tasks_text = "当前任务：—"
                attention_text = "待处理事项：—"
            else:
                status_text = {
                    "running": "状态：正在执行任务",
                    "waiting_permission": "状态：等待你的授权",
                }.get(self.agent_state, "状态：运行正常")
                running = 1 if self.agent_state in ("running", "waiting_permission") else 0
                tasks_text = f"当前任务：{running + self.queue_depth} 个"
                attention_text = f"待处理事项：{total} 个"
            parts = [f"{label} {self.counts[key]}" for key, label in _COUNT_LABELS
                     if self.counts.get(key, 0) > 0]
            tooltip = "mini_agent — " + status_text.removeprefix("状态：")
            if parts:
                tooltip += "\n" + "，".join(parts)
            return MenuState(
                icon=icon, badge=total if self.connected else 0,
                status_text=status_text, tasks_text=tasks_text,
                attention_text=attention_text, tooltip=tooltip,
                connected=self.connected,
                can_start_daemon=(not self.connected and self.reason == REASON_NOT_RUNNING),
            )


REASON_NOT_RUNNING = "daemon 未运行"
REASON_AUTH = "认证失败，请检查 token"
REASON_LOST = "连接中断"
