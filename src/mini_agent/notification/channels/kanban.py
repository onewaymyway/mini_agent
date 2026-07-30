"""notification/channels/kanban.py — KanbanChannel（P1，汇报独立存储改造）。

[汇报独立存储 变更] 不再写入 external_input 的 alerts.jsonl / 走 /v1/inbox 全局待办
中心——那份文件和聚合入口是网关 notify_only 告警专用的。watchlist_report
分级汇报现在写入独立的 `notification_reports`（reports.jsonl），看板
"关注与通知"tab 的"📋 待处理汇报"面板通过专门的 /v1/notifications/* 端点
读取（含完整 detail 正文），彻底跟网关告警在存储和展示上都分开。
见 next_doc/watchlist_notification_goal_design.md §5。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from mini_agent.notification.dispatcher import NotificationChannel, NotificationMessage, register_channel
from mini_agent.notification.reports_store import append_report

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


@register_channel("kanban")
class KanbanChannel(NotificationChannel):
    def send(self, message: NotificationMessage, cfg: dict, paths: "AgentPaths") -> bool:
        record = {
            "report_id": f"notif:{message.source}:{int(message.created_at * 1000)}",
            "source": message.source,   # 如 "watchlist_report"
            "title": message.title,
            "detail": message.body,     # 完整 Markdown 正文（含命中明细），
                                         # 这是相比旧 alerts.jsonl 共用方案
                                         # 新增的可展示字段。
            "url": message.url,
            "fields": message.meta,
            "occurred_at": message.created_at,
            "created_at": time.time(),
            "acknowledged": False,
        }
        try:
            append_report(paths, record)
            return True
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.notification.channels.kanban.send")
            return False
