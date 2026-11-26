"""E2：托盘健康面板的数据模型（纯逻辑，不依赖 Qt）。

HealthStats 由控制器在连接/离线/收到事件时更新（只做计数，不做 IO）；
build_health_rows 把快照转换成 (标签, 值, 级别) 行，窗口据此着色显示。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"
LEVEL_INFO = "info"


@dataclass
class HealthStats:
    connected_count: int = 0             # 成功连接的次数
    connected_since: Optional[float] = None   # 当前连接开始的时间戳（墙上时钟）；离线时为 None
    last_offline_reason: str = ""
    last_offline_at: Optional[float] = None
    events_received: int = 0             # 本进程内收到的 SSE 事件数（心跳不计）
    attention_events_enabled: Optional[bool] = None   # 来自 /v1/desktop/summary，未取到时为 None

    def on_connected(self, now: float) -> None:
        self.connected_count += 1
        self.connected_since = now

    def on_offline(self, reason: str, now: float) -> None:
        self.connected_since = None
        self.last_offline_reason = reason
        self.last_offline_at = now

    def on_event(self) -> None:
        self.events_received += 1

    @property
    def reconnects(self) -> int:
        return max(self.connected_count - 1, 0)


def _fmt_time(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def build_health_rows(snapshot: dict) -> list[tuple[str, str, str]]:
    """snapshot 字段（缺失时按保守默认值处理）：
      now, stats(HealthStats), crashes(int), toast_enabled(bool), quiet_hours(str),
      hotkey_active(bool), hotkey(str), endpoint(str), project_root(str)
    """
    stats: HealthStats = snapshot.get("stats") or HealthStats()
    now = float(snapshot.get("now") or 0.0)
    rows: list[tuple[str, str, str]] = []

    # 连接
    if stats.connected_since is not None:
        rows.append(("连接状态", f"已连接（已持续 {_fmt_duration(now - stats.connected_since)}）", LEVEL_OK))
    else:
        reason = stats.last_offline_reason or "尚未连接"
        rows.append(("连接状态", f"未连接：{reason}", LEVEL_ERROR))
    rows.append(("本次连接时间", _fmt_time(stats.connected_since), LEVEL_INFO))
    rows.append(("连接次数 / 重连次数", f"{stats.connected_count} / {stats.reconnects}",
                 LEVEL_WARN if stats.reconnects else LEVEL_INFO))
    rows.append(("最近一次离线", _fmt_time(stats.last_offline_at), LEVEL_INFO))
    rows.append(("端点", str(snapshot.get("endpoint") or "—"), LEVEL_INFO))
    rows.append(("项目目录", str(snapshot.get("project_root") or "—"), LEVEL_INFO))
    rows.append(("已收到的实时事件", str(stats.events_received), LEVEL_INFO))

    # 告警与开关
    crashes = int(snapshot.get("crashes") or 0)
    rows.append(("未确认崩溃告警", str(crashes), LEVEL_ERROR if crashes else LEVEL_OK))
    if stats.attention_events_enabled is None:
        push = ("未知（未取到摘要）", LEVEL_WARN)
    elif stats.attention_events_enabled:
        push = ("已开启", LEVEL_OK)
    else:
        push = ("已关闭（agent_config.json → http.desktop_attention_events_enabled）", LEVEL_WARN)
    rows.append(("daemon 事件推送", push[0], push[1]))
    rows.append(("桌面弹窗提醒", "已开启" if snapshot.get("toast_enabled") else "已关闭",
                 LEVEL_OK if snapshot.get("toast_enabled") else LEVEL_WARN))
    quiet = snapshot.get("quiet_hours") or ""
    rows.append(("免打扰时段", quiet or "未设置", LEVEL_INFO))
    if snapshot.get("hotkey_active"):
        rows.append(("全局热键", f"已注册 {snapshot.get('hotkey', '')}", LEVEL_OK))
    else:
        rows.append(("全局热键", "未生效", LEVEL_INFO))
    return rows
