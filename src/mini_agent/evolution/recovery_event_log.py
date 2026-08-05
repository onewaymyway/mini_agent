"""
evolution/recovery_event_log.py —
[next_doc/kanban_execution_visibility_and_control_plan.md 阶段 B]

三条"任务卡死后被 watchdog/健康检查强制回收"的链路
（`CronJobRunner.reap_stale_jobs()`、`ObjectiveExecutor.reap_stale_steps()`、
`ObjectiveIsolatedRunner.check_health()`）此前只各自维护一个累计计数器
（`reaped_job_count`/`stale_step_reap_count`/`pool_rebuild_count` 等），
看板只能展示"发生过多少次"，看不出"具体是谁、什么时候"。

这里提供一个共享的、进程内、有容量上限的环形缓冲，三条链路各自在判定
卡死时调用 `record_recovery_event()` 追加一条记录，`execution_model_status`
汇总成 `recent_recoveries` 字段供看板展示。

设计上刻意保持"轻量、纯内存、不持久化"：
  - 这是运维观测辅助（"最近发生过什么，值不值得关注"），不是审计日志，
    daemon 重启后清空是可以接受的，不需要引入额外的文件 IO/数据库依赖。
  - 用 `collections.deque(maxlen=...)` 天然实现"超过容量自动丢弃最老的
    记录"，不需要手动裁剪逻辑。
  - 一把全局锁 + 一个模块级单例：三条链路调用频率都很低（只在真正判定
    卡死时才调用一次），锁竞争可以忽略不计，没必要为每条链路单独维护
    一份状态、再在 `execution_model_status` 里合并。

[daemon_stability_and_ux_improvement_plan.md P0-8] 补充：短时间内同一条
链路的回收事件数异常增长本身是一个值得主动告警的信号（"过去 10 分钟内
有 3 个 cron job 被判定卡死回收"），不应该只是被动落在这个环形缓冲里等
用户自己翻。这里在 `record_recovery_event()` 内部顺带做一次简单的
"短窗口内同 kind 事件数是否达到阈值"判断，命中时通过
`notification/dispatcher.py` 推一条通知；一次"突发"只推一次（用
`_last_burst_notified_at` 做冷却），避免达到阈值后每条新事件都重复推送。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

_MAX_EVENTS = 50
_lock = threading.Lock()
_events: deque = deque(maxlen=_MAX_EVENTS)

# 突发检测参数：默认 10 分钟内同一 kind 达到 3 条即视为"异常增长"。
_BURST_WINDOW_SECONDS = 600
_BURST_THRESHOLD = 3
# 同一 kind 的突发通知冷却时间，避免阈值达到后每条新事件都重复推送。
_BURST_NOTIFY_COOLDOWN_SECONDS = 1800
_last_burst_notified_at: dict[str, float] = {}

_KIND_LABELS = {
    "cron_job": "cron job",
    "objective_step": "Objective 步骤",
    "isolated_pool": "隔离线程池",
}


def record_recovery_event(
    kind: str,
    event_id: str,
    detail: str,
    *,
    now: Optional[float] = None,
    paths: Optional["AgentPaths"] = None,
) -> None:
    """记录一条卡死回收事件。

    kind — "cron_job" | "objective_step" | "isolated_pool"，供看板分类
        展示是哪条链路发生的回收。
    event_id — 该事件对应的具体对象标识（cron job_id / execution_id 或
        execution_id:step_index / isolated_pool 场景下没有单独对象，
        传空字符串即可，因为它本身是"整个共享池"的整体事件）。
    detail — 简短的人类可读说明（比如"超过 1500s 未收到执行结果"）。
    paths — [P0-8] 可选，传入时才会做短窗口突发检测 + 主动通知；不传时
        行为与改造前完全一致（只记录，不检测不通知），保持向后兼容——
        调用方如果暂时没有 paths 可用，不应该因此报错。
    """
    ts = time.time() if now is None else now
    with _lock:
        _events.appendleft({
            "time": ts,
            "kind": kind,
            "id": event_id,
            "detail": detail,
        })
        recent_same_kind = [e for e in _events if e["kind"] == kind and ts - e["time"] <= _BURST_WINDOW_SECONDS]
        burst_count = len(recent_same_kind)

    if paths is None or burst_count < _BURST_THRESHOLD:
        return
    last_notified = _last_burst_notified_at.get(kind, float("-inf"))
    if ts - last_notified < _BURST_NOTIFY_COOLDOWN_SECONDS:
        return
    _last_burst_notified_at[kind] = ts
    _notify_recovery_burst(paths, kind, burst_count)


def _notify_recovery_burst(paths: "AgentPaths", kind: str, count: int) -> None:
    try:
        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        label = _KIND_LABELS.get(kind, kind)
        window_minutes = _BURST_WINDOW_SECONDS // 60
        NotificationDispatcher(paths).dispatch(NotificationMessage(
            title=f"{label} 短时间内多次卡死回收",
            body=f"过去 {window_minutes} 分钟内有 {count} 个{label}被判定卡死并强制回收，可能存在系统性问题",
            source="recovery_burst",
            meta={"kind": kind, "count": count, "window_seconds": _BURST_WINDOW_SECONDS},
        ))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.evolution.recovery_event_log._notify_recovery_burst")


def recent_recovery_events(limit: int = _MAX_EVENTS) -> list[dict]:
    """返回最近的回收事件，按时间倒序（最新的在最前面）。"""
    with _lock:
        snapshot = list(_events)
    return snapshot[:max(0, limit)]


def _reset_for_tests() -> None:
    """仅供测试用：清空全局环形缓冲，避免测试之间互相污染。"""
    with _lock:
        _events.clear()
    _last_burst_notified_at.clear()


__all__ = ["record_recovery_event", "recent_recovery_events"]
