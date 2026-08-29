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


# ── daemon_hang_detection_and_alert_escalation_plan.md §3.2：未确认告警 ──
# 超时升级重推 + 交互入口顺带提示 ──────────────────────────────────────────

def list_stale_unacknowledged_alerts(
    paths: "AgentPaths", escalation_hours: float, max_escalations: int = 1,
) -> list[dict]:
    """返回"创建超过 escalation_hours 仍未确认、且升级次数还没到上限"的
    崩溃告警。`escalation_count` 字段不存在时按 0 处理（老数据/阶段一~二
    写入的记录没有这个字段，天然符合"还没升级过"）。"""
    now = time.time()
    cutoff = now - escalation_hours * 3600
    items = list_crash_alerts(paths, unacknowledged_only=True)
    result = []
    for d in items:
        created_at = d.get("created_at") or 0
        if created_at > cutoff:
            continue
        if int(d.get("escalation_count", 0)) >= max_escalations:
            continue
        result.append(d)
    return result


def mark_escalated(paths: "AgentPaths", alert_id: str) -> bool:
    """把一条崩溃告警的 `escalation_count` 加 1，记录"已经升级重推过一次
    "（避免每次定时检查都重复推送同一条——见 append_crash_alert 顶部
    schema 说明里没列出这个字段是因为它只在升级发生后才第一次出现，
    默认不存在等价于 0）。整体重写，跟 acknowledge_crash_alert 处理方式
    一致。"""
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
        if d.get("alert_id") == alert_id:
            d["escalation_count"] = int(d.get("escalation_count", 0)) + 1
            d["last_escalated_at"] = time.time()
            matched = True
        new_lines.append(json.dumps(d, ensure_ascii=False))
    if matched:
        try:
            p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.notification.daemon_crash_store.mark_escalated")
            return False
    return matched


# ── §3.3：崩溃告警文件轮转（跟 append_crash_alert 顶部的 _MAX_LINES 截断 ──
# 是同一个机制，这里单独暴露一个函数供 `daemon status`/看板等读取路径 ────
# 复用，避免各处各写一遍截断逻辑）────────────────────────────────────────

def rotate_crash_alerts_if_needed(
    paths: "AgentPaths", max_entries: int = _MAX_LINES,
) -> int:
    """如果告警文件超过 `max_entries` 条，保留最近的 `max_entries` 条
    （按写入顺序，即文件里靠后的），旧记录直接丢弃（不需要保留完整
    历史，排查用途上"最近 N 条"已经足够，与 §3.3 描述一致）。返回本次
    裁剪掉的条数（未超限时返回 0）。写入失败不抛异常。"""
    p = paths.notification_daemon_crash_alerts
    if not p.exists():
        return 0
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        lines = [l for l in lines if l.strip()]
        if len(lines) <= max_entries:
            return 0
        trimmed = len(lines) - max_entries
        p.write_text("\n".join(lines[-max_entries:]) + "\n", encoding="utf-8")
        return trimmed
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.daemon_crash_store.rotate_crash_alerts_if_needed")
        return 0
