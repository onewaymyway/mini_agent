"""notification/daemon_crash_escalation.py — 未确认崩溃告警的超时升级
（daemon_hang_detection_and_alert_escalation_plan.md §3.2）。

背景：崩溃/卡死发生时已经会落到 `daemon_crash_store.append_crash_alert()`
（看板专属存储，恒真展示未确认记录）并广播一次外部渠道。但如果用户当时
没打开看板、也没配置外部渠道，这条告警可能安静地躺在
`daemon_crash_alerts.jsonl` 里几天都不会被看到——尤其是配置了自动重启后，
daemon 表面"一直健康"，用户主动去查的动机反而更低。

方案是两个互相独立、成本都不高的手段（见计划 §3.2）：
1. **本模块**：一条独立的轻量后台线程，定期（默认每 30 分钟）扫一次未
   确认的崩溃告警，把"创建超过 `escalation_hours` 仍未 ack"的记录重新
   广播一次到外部渠道（不是 kanban——kanban 横幅本身恒真常驻，不需要
   重复）。同一条告警只升级 `max_escalations` 次（默认 1 次），避免持续
   骚扰。
2. **交互时顺带提示**：见 `cli/daemon.py` 里 HTTP/REPL 入口的调用点（不
   在本模块——那部分是"请求路径上顺手查一下"，不需要独立线程）。

为什么是独立线程、不占用 supervisor 主循环：supervisor 只有在子进程存活
期间才会跑（`hang_detection_enabled=False` 时甚至整段时间都阻塞在
`proc.wait()` 里），而告警升级检查跟"子进程是否存活"没有关系，理应独立
于 supervisor 的生命周期。挂在 `api/server.py::HttpServer` 里（daemon-mode
进程本身，不管前台/detach、不管 supervisor 是否启用，只要 HTTP 服务活着
就跑），与 `evolution/scheduler_heartbeat.py::SchedulerHeartbeat` 是同一种
"独立后台线程"模式。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path


def check_and_escalate_crash_alerts(
    project_root: Path,
    escalation_hours: float = 1.0,
    max_escalations: int = 1,
) -> int:
    """扫描一次未确认崩溃告警，把超时的重新广播到外部渠道并打上升级标记。
    返回本次实际升级的条数。读取/广播失败不抛异常——告警升级本身是锦上
    添花的兜底机制，不应该反过来影响调用方（后台线程/HTTP 请求路径）。"""
    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.notification.daemon_crash_store import (
            list_stale_unacknowledged_alerts,
            mark_escalated,
        )
        from mini_agent.cli.daemon import broadcast_crash_alert_to_external_channels

        paths = AgentPaths(project_root)
        stale = list_stale_unacknowledged_alerts(
            paths, escalation_hours=escalation_hours, max_escalations=max_escalations
        )
        escalated = 0
        for alert in stale:
            alert_id = alert.get("alert_id")
            summary = alert.get("summary", "")
            hang_reason_present = "卡死" in summary
            title = (
                "⏰ 提醒：Daemon 卡死告警仍未确认"
                if hang_reason_present
                else "⏰ 提醒：Daemon 崩溃告警仍未确认"
            )
            broadcast_crash_alert_to_external_channels(
                project_root,
                title=title,
                summary=summary,
                alert_id=alert_id,
                paths=paths,
            )
            if alert_id and mark_escalated(paths, alert_id):
                escalated += 1
        return escalated
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(
            exc, where="mini_agent.notification.daemon_crash_escalation.check_and_escalate_crash_alerts"
        )
        return 0


class CrashAlertEscalationThread(threading.Thread):
    """独立后台线程：按 `poll_interval_seconds` 定期调用
    `check_and_escalate_crash_alerts()`。daemon=True，随主进程退出自然
    结束；也支持显式 `stop()`（`HttpServer.stop()` 优雅关闭时调用，跟
    `SchedulerHeartbeat`/`GatewayPoller` 的停止方式一致）。"""

    def __init__(
        self,
        project_root: Path,
        escalation_hours: float = 1.0,
        max_escalations: int = 1,
        poll_interval_seconds: float = 1800.0,
    ) -> None:
        super().__init__(name="daemon-crash-alert-escalation", daemon=True)
        self._project_root = project_root
        self._escalation_hours = escalation_hours
        self._max_escalations = max_escalations
        # 轮询间隔刻意比 escalation_hours 短很多（默认 30 分钟 vs 默认
        # 1 小时），保证"超时后多久之内会被检测到"这个延迟可控，而不是
        # 拿 escalation_hours 本身当轮询间隔（那样最坏情况下要再等接近
        # 一个 escalation_hours 才会被发现，延迟翻倍）。
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()

    def run(self) -> None:
        # 启动后先等一个轮询间隔再开始检查，避免 daemon 刚启动就立刻扫一遍
        # （此时不太可能已经有超时未读的告警，没必要抢跑）。
        while not self._stop_event.wait(self._poll_interval_seconds):
            try:
                check_and_escalate_crash_alerts(
                    self._project_root,
                    escalation_hours=self._escalation_hours,
                    max_escalations=self._max_escalations,
                )
            except Exception as exc:
                from mini_agent.errors import log_exception
                log_exception(
                    exc,
                    where="mini_agent.notification.daemon_crash_escalation.CrashAlertEscalationThread.run",
                )

    def stop(self) -> None:
        self._stop_event.set()
