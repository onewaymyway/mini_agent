"""notification/channels/kanban.py — KanbanChannel（P1）。

直接复用现有 alerts.jsonl + /v1/inbox 机制：落成一条跟 external_input
的 notify_only 同构的记录，看板"待处理告警"面板直接就能看到，不需要新造
UI。见 next_doc/watchlist_notification_goal_design.md §5、§9.3 #10。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from mini_agent.notification.dispatcher import NotificationChannel, NotificationMessage, register_channel

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


@register_channel("kanban")
class KanbanChannel(NotificationChannel):
    def send(self, message: NotificationMessage, cfg: dict, paths: "AgentPaths") -> bool:
        p = paths.external_input_alerts
        p.parent.mkdir(parents=True, exist_ok=True)
        alert = {
            "alert_id": f"notif:{message.source}:{int(message.created_at * 1000)}",
            "event_id": message.meta.get("event_id", ""),
            "source_id": message.source,
            # §9.3 #10：source 字段原样带进 alerts.jsonl 记录，看板侧可以按它
            # 区分"关注命中/分级汇报"和网关原有的 notify_only 告警，而不是
            # 两者在展示层混成一样的东西。
            "source_type": "notification",
            "signal": message.source,
            "title": message.title,
            "detail": message.body,
            "url": message.url,
            "fields": message.meta,
            "occurred_at": message.created_at,
            "created_at": time.time(),
            "acknowledged": False,
        }
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(alert, ensure_ascii=False) + "\n")
            return True
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.notification.channels.kanban.send")
            return False
