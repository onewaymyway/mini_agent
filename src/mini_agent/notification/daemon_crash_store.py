"""notification/daemon_crash_store.py — daemon 崩溃告警的独立存储（阶段一）。

设计背景见 next_doc/daemon_crash_recovery_and_alert_plan.md §3.2。

跟 `reports_store.py`（watchlist_report 周期性汇报，可以慢慢看、可以批量
已读）刻意分开：这里装的是"daemon 进程意外退出"这一类高时效性事件，物理
上独立存一份文件（`.agent/notification/daemon_crash_alerts.jsonl`），看板
用专门的常驻横幅展示，不进入 `/v1/notifications/pending` 的通用列表、
不参与分类筛选。

一条记录的典型 schema（由 `cli/daemon.py::record_daemon_crash()` 产出）：
    {
        "alert_id": "...",          # uuid4
        "created_at": 1735500000.0,
        "acknowledged": false,
        "pid": 1234,
        "exit_code": -9,
        "uptime_seconds": 3821.0,
        "restart_attempt": 0,
        "restart_decision": "no_restart" | "restarted" | "giveup" | "stopped_by_user",
        "last_exception": {...} | None,
        "log_tail": ["...", ...],
        "summary": "人类可读的一句话摘要，供横幅直接展示",
    }
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

_MAX_LINES = 500  # 跟 dispatch_log.jsonl 同样的截断策略，纯诊断用途不需要无限保留


def append_crash_alert(paths: "AgentPaths", record: dict) -> dict:
    """追加一条崩溃告警记录，返回补全了 alert_id/created_at/acknowledged
    的完整记录（供调用方顺带拿去做 dispatch 广播）。写入失败不抛异常
    ——告警落盘本身不应该反过来影响崩溃处理流程的其它步骤。"""
    record = dict(record)
    record.setdefault("alert_id", uuid.uuid4().hex)
    record.setdefault("created_at", time.time())
    record.setdefault("acknowledged", False)
    try:
        p = paths.notification_daemon_crash_alerts
        p.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps(record, ensure_ascii=False))
        if len(lines) > _MAX_LINES:
            lines = lines[-_MAX_LINES:]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.daemon_crash_store.append_crash_alert")
    return record


def _load_all(paths: "AgentPaths") -> list[dict]:
    p = paths.notification_daemon_crash_alerts
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
                    result.append(json.loads(line))
                except Exception:
                    continue
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.daemon_crash_store._load_all")
        return []
    result.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return result


def list_crash_alerts(
    paths: "AgentPaths", limit: Optional[int] = None, unacknowledged_only: bool = True,
) -> list[dict]:
    """按时间倒序返回崩溃告警。默认只返回未确认的（供横幅展示），
    `unacknowledged_only=False` 时返回全部（供"历史崩溃记录"查看）。"""
    items = _load_all(paths)
    if unacknowledged_only:
        items = [d for d in items if not d.get("acknowledged")]
    if limit is not None:
        items = items[:limit]
    return items


def count_unacknowledged_crash_alerts(paths: "AgentPaths") -> int:
    return len(list_crash_alerts(paths, unacknowledged_only=True))


def acknowledge_crash_alert(paths: "AgentPaths", alert_id: str) -> bool:
    """把一条崩溃告警标记为已读。整体重写，跟 reports_store.py 的处理
    方式一致。"""
    p = paths.notification_daemon_crash_alerts
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    matched = False
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
        if d.get("alert_id") == alert_id and not d.get("acknowledged"):
            d["acknowledged"] = True
            matched = True
        new_lines.append(json.dumps(d, ensure_ascii=False))
    if matched:
        try:
            p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.notification.daemon_crash_store.acknowledge_crash_alert")
            return False
    return matched
