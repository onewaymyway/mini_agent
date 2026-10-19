"""evolution/goal_exec_context.py — goal/objective 执行期上下文（thread-local）。

对应 next_doc/goal_output_project_auto_link_plan.md §4.1（S2）。

用途：让执行期内置工具 `link_output_project` 知道"当前这一步属于哪个目标节点、
项目根在哪里"，从而不需要 agent 自己传 goal_id（避免传错）。

与 cron_context / turn_context 完全相同的 thread-local 模式。关键约束：
  - 必须在**执行该 step 的线程**上设置。objective 的 step 由
    `objective_agent_bridge` 的线程池 worker 执行，因此设置点在 bridge 的
    `_run_step` 入口（读取 submit 时放进 meta 的 `goal_exec` 字段），而不是
    `ObjectiveExecutor.start()`——start() 所在线程与执行线程不是同一个。
  - 每个 step 结束必须清除（`goal_exec_scope` 的 finally），worker 线程会被复用。
  - 只有开关 `output_registry.auto_link_tool_enabled=true` 时 submit 才会写入
    `goal_exec` meta，因此开关关闭时上下文永远不存在，工具也不会出现在工具列表中。
"""

from __future__ import annotations

import threading as _threading
from contextlib import contextmanager
from typing import Iterator, Optional

_ctx_local = _threading.local()

META_KEY = "goal_exec"


def set_current_goal_exec(trigger_node_id: str, project_root: str, execution_id: str = "") -> None:
    """记录当前线程正在执行的 goal/objective 节点。

    trigger_node_id：本次执行的 objective 节点 id（cron 周期的 objective 挂在 goal 之下，
    管理项目根的解析从它沿 parent_id 向上查找）。
    project_root：项目根目录（含 .agent/ 与 output_projects_root）。
    """
    _ctx_local.value = {
        "trigger_node_id": trigger_node_id or "",
        "project_root": project_root or "",
        "execution_id": execution_id or "",
    }


def clear_current_goal_exec() -> None:
    _ctx_local.value = None


def get_current_goal_exec() -> Optional[dict]:
    """返回当前线程的执行上下文副本；不在 goal/objective 执行中时返回 None。"""
    value = getattr(_ctx_local, "value", None)
    if not value or not value.get("trigger_node_id") or not value.get("project_root"):
        return None
    return dict(value)


@contextmanager
def goal_exec_scope(meta: Optional[dict]) -> Iterator[None]:
    """在 step 执行期间设置上下文，退出时无条件清除。

    meta 为空或不含 META_KEY 时什么也不做（开关关闭路径）。
    """
    ctx = (meta or {}).get(META_KEY) if isinstance(meta, dict) else None
    if not ctx:
        yield
        return
    set_current_goal_exec(
        ctx.get("trigger_node_id", ""),
        ctx.get("project_root", ""),
        (meta or {}).get("execution_id", ""),
    )
    try:
        yield
    finally:
        clear_current_goal_exec()


__all__ = [
    "META_KEY",
    "set_current_goal_exec",
    "clear_current_goal_exec",
    "get_current_goal_exec",
    "goal_exec_scope",
]
