"""core/objective_adapter.py — Phase 10 S-A A5：Objective → Goal 的只读投影。

见 `next_doc/refactor_plan/14-phase10-sa-item-plan.md` 第三节（A5）。

Old = `mini_agent.evolution.objective_executor.ObjectiveExecution`
      （可选补充 `mini_agent.perception.goal_backlog.GoalNode`）
New = `mini_agent.core.goal.GoalState`

**拉取式、只读、不写回、不订阅**：
  - 不修改 `objective_executor.py`（2366 行、时序敏感，Sprint 8-5 评为高风险
    区域），只使用它已有的两个只读公开方法 `get_status_summary()` 与
    `get_execution()`；
  - 不写入 `StateManager` 的 `"goal"` kind（那是 `GoalRunner` 当前 Goal 的
    托管位，写入会互相覆盖）；
  - 事件只在**被读取时**发布，且只在投影状态相对上一次发生变化时才发布，
    避免看板轮询把 `events.jsonl` 刷满。

`to_old` 显式 `NotImplementedError`：没有任何调用方需要从 `GoalState` 反向构造
`ObjectiveExecution`（它带有 `turn_id`/`submitted_message` 等只属于执行引擎的
运行时字段，反向构造必然是编造数据）。

已知局限（如实记录）：
  - `get_status_summary()` 会跳过“已终止且超过 1 小时”的执行记录，所以
    `project_objective_executions()` 只能看到活跃 + 最近 1 小时内终止的；
    需要更早的记录时，调用方自己 `get_execution(id)` 后传给 `to_new()`。
  - `GoalStatus` 没有 paused/pending 取值，这几种都映射为 `"running"`，
    原始状态保留在 `evidence["objective_status"]`，不丢信息。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

from .adapter import Adapter
from .event_bus import EventBus, get_event_bus
from .events import Event
from .goal import GoalState

if TYPE_CHECKING:
    from mini_agent.evolution.objective_executor import (
        ObjectiveExecution,
        ObjectiveExecutor,
    )
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode

_logger = logging.getLogger("mini_agent.core.trace")

# ObjectiveExecution.status → core.types.GoalStatus。
# GoalStatus 没有 pending/paused*，统一落到 "running"，原值放进 evidence。
_STATUS_MAP = {
    "pending": "running",
    "running": "running",
    "paused": "running",
    "paused_for_fairness": "running",
    "paused_by_user": "running",
    "completed": "done",
    "failed": "failed",
    "cancelled": "cancelled",
}

_MAX_PROBLEMS = 5
_MAX_TEXT = 200


class ObjectiveAdapter(Adapter["ObjectiveExecution", GoalState]):
    """`ObjectiveExecution` → `GoalState`（单向）。"""

    @staticmethod
    def to_new(
        old: "ObjectiveExecution", node: Optional["GoalNode"] = None
    ) -> GoalState:
        """比协议多一个可选 `node`：提供时补充 description/priority/父 Goal。

        不提供 `node` 也能转换（只用执行记录自身的字段），保证协议签名
        `to_new(old)` 仍然可用。
        """
        done = sum(1 for s in old.steps if s.status == "done")
        total = len(old.steps)
        cur = old.current_step
        problems: list[str] = []
        for s in old.steps:
            if s.status in ("failed", "blocked") and (s.error_msg or "").strip():
                problems.append(s.error_msg.strip()[:_MAX_TEXT])
            if len(problems) >= _MAX_PROBLEMS:
                break

        evidence: dict[str, Any] = {
            "source": "objective_executor",
            "execution_id": old.execution_id,
            "objective_id": old.objective_id,
            "objective_status": old.status,
            "level": "objective",
            "steps_total": total,
            "steps_done": done,
            "current_step_idx": old.current_step_idx,
        }
        description = ""
        priority: Optional[int] = None
        if node is not None:
            evidence["parent_goal_id"] = getattr(node, "parent_id", None)
            description = (getattr(node, "description", "") or "").strip()
            priority = getattr(node, "priority", None)
            if not isinstance(priority, int):
                priority = None

        started = old.started_at or time.time()
        updated = old.finished_at or old.started_at or time.time()
        return GoalState(
            goal_text=old.objective_title,
            status=_STATUS_MAP.get(old.status, "running"),  # type: ignore[arg-type]
            round=old.current_step_idx,
            created_at=started,
            updated_at=updated,
            current_state=(cur.description[:_MAX_TEXT] if cur else ""),
            ideal_state=description[:_MAX_TEXT * 2],
            problems=problems,
            priority=priority,
            evidence=evidence,
        )

    @staticmethod
    def to_old(new: GoalState) -> "ObjectiveExecution":
        raise NotImplementedError(
            "ObjectiveAdapter.to_old 未实现：ObjectiveExecution 含 turn_id/"
            "submitted_message 等执行引擎私有运行时字段，从 GoalState 反向"
            "构造只能是编造数据；当前没有任何调用方需要这个方向，见 "
            "core/objective_adapter.py 模块文档字符串。"
        )


# ── 拉取式投影 + 变化时发布事件 ─────────────────────────────────────────

# execution_id → 上一次发布事件时的 (status, round, steps_done)。
# 进程内、仅用于去重；不落盘，进程重启后第一次读取会重新发一次（可接受）。
_last_projected: dict[str, tuple] = {}
_last_lock = threading.Lock()


def reset_objective_projection_cache() -> None:
    """清空去重缓存（仅供测试）。"""
    with _last_lock:
        _last_projected.clear()


def _maybe_publish(state: GoalState, bus: EventBus) -> bool:
    ev = state.evidence
    exec_id = ev.get("execution_id", "")
    sig = (ev.get("objective_status"), state.round, ev.get("steps_done"))
    with _last_lock:
        if _last_projected.get(exec_id) == sig:
            return False
        _last_projected[exec_id] = sig
    bus.publish(
        Event(
            kind="ObjectiveProjected",
            actor="objective_adapter",
            correlation_id=exec_id or None,
            payload={
                "execution_id": exec_id,
                "objective_id": ev.get("objective_id"),
                "goal_text": state.goal_text,
                "status": state.status,
                "objective_status": ev.get("objective_status"),
                "round": state.round,
                "steps_done": ev.get("steps_done"),
                "steps_total": ev.get("steps_total"),
            },
        )
    )
    return True


def project_objective_executions(
    executor: "ObjectiveExecutor",
    backlog: Optional["GoalBacklog"] = None,
    *,
    publish_events: bool = True,
    bus: Optional[EventBus] = None,
) -> list[GoalState]:
    """把 `executor` 当前可见的 Objective 执行记录投影成 `GoalState` 列表。

    永不抛异常：单条记录读取/转换失败只记 warning 并跳过，其余照常返回。
    `publish_events=True` 时，对相对上次发生变化的记录发布 `ObjectiveProjected`。
    """
    out: list[GoalState] = []
    try:
        summaries = executor.get_status_summary()
    except Exception:  # noqa: BLE001
        _logger.warning("project_objective_executions: get_status_summary 失败", exc_info=True)
        return out
    bus = bus or get_event_bus()
    for row in summaries:
        try:
            ex = executor.get_execution(row["execution_id"])
            if ex is None:
                continue
            node = None
            if backlog is not None:
                try:
                    node = backlog.get(ex.objective_id)
                except Exception:  # noqa: BLE001
                    node = None
            state = ObjectiveAdapter.to_new(ex, node)
            out.append(state)
            if publish_events:
                _maybe_publish(state, bus)
        except Exception:  # noqa: BLE001
            _logger.warning("project_objective_executions: 单条投影失败，已跳过", exc_info=True)
    return out
