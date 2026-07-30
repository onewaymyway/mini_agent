"""notification/reports_store.py — watchlist_report 汇报记录的独立存储。

跟 `external_input/policy.py` 里 alerts.jsonl 的读写逻辑刻意保持同构（同样
是"小文件、低频写、整体重写"），但物理上是两份完全独立的文件：

  - `.agent/external_input/alerts.jsonl`  ← 只装网关 notify_only 告警
  - `.agent/notification/reports.jsonl`  ← 只装 watchlist_report 分级汇报

分开存储的原因：这两类东西对用户来说语义不同（"外部世界发生了一件需要
你判断的事" vs "你关注的东西按周期打包汇总了一份清单"），过去共用
alerts.jsonl + /v1/inbox 聚合展示，导致：
  1. 汇报的完整正文（NotificationMessage.body，含命中明细）没有专门的
     展示入口，只藏在共享文件的 detail 字段里；
  2. 两者在"全局待办中心"里混在同一个列表，用户分不清哪条是需要处理的
     告警、哪条只是周期性汇总。

现在 KanbanChannel 直接写这份独立文件，看板"关注与通知"tab 用专门的
/v1/notifications/* 端点读取/ack，不再出现在 /v1/inbox 里。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


def append_report(paths: "AgentPaths", record: dict) -> None:
    p = paths.notification_reports
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.reports_store.append_report")


def _load_pending_sorted(paths: "AgentPaths") -> list[dict]:
    p = paths.notification_reports
    if not p.exists():
        return []
    result: list[dict] = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not d.get("acknowledged"):
                    result.append(d)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.reports_store._load_pending_sorted")
        return []
    result.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return result


def list_pending_reports(
    paths: "AgentPaths", limit: Optional[int] = None, offset: int = 0,
) -> list[dict]:
    """读取 reports.jsonl 中尚未 acknowledged 的汇报，供
    /v1/notifications/pending 分页端点使用。每条记录都带完整 `detail`
    （汇报正文，含命中明细），供看板"📋 待处理汇报"面板展开显示——这是
    跟 alerts.jsonl 共用时缺失的能力。"""
    result = _load_pending_sorted(paths)
    if offset:
        result = result[offset:]
    if limit is not None:
        result = result[:limit]
    return result


def count_pending_reports(paths: "AgentPaths") -> int:
    return len(_load_pending_sorted(paths))


def acknowledge_report(paths: "AgentPaths", report_id: str) -> bool:
    """把某条汇报标记为已读。整体重写，跟 policy.py::acknowledge_alert
    的处理方式一致。"""
    p = paths.notification_reports
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue
        if d.get("report_id") == report_id and not d.get("acknowledged"):
            d["acknowledged"] = True
            found = True
        new_lines.append(json.dumps(d, ensure_ascii=False))
    if found:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return found
