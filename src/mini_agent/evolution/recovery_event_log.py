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
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

_MAX_EVENTS = 50
_lock = threading.Lock()
_events: deque = deque(maxlen=_MAX_EVENTS)


def record_recovery_event(kind: str, event_id: str, detail: str, *, now: Optional[float] = None) -> None:
    """记录一条卡死回收事件。

    kind — "cron_job" | "objective_step" | "isolated_pool"，供看板分类
        展示是哪条链路发生的回收。
    event_id — 该事件对应的具体对象标识（cron job_id / execution_id 或
        execution_id:step_index / isolated_pool 场景下没有单独对象，
        传空字符串即可，因为它本身是"整个共享池"的整体事件）。
    detail — 简短的人类可读说明（比如"超过 1500s 未收到执行结果"）。
    """
    with _lock:
        _events.appendleft({
            "time": time.time() if now is None else now,
            "kind": kind,
            "id": event_id,
            "detail": detail,
        })


def recent_recovery_events(limit: int = _MAX_EVENTS) -> list[dict]:
    """返回最近的回收事件，按时间倒序（最新的在最前面）。"""
    with _lock:
        snapshot = list(_events)
    return snapshot[:max(0, limit)]


def _reset_for_tests() -> None:
    """仅供测试用：清空全局环形缓冲，避免测试之间互相污染。"""
    with _lock:
        _events.clear()


__all__ = ["record_recovery_event", "recent_recovery_events"]
