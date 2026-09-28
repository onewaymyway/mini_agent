"""core/capability_projector.py — 把三个旧注册表投影成 `CapabilityState`（只读）。

三个来源都是**可选**的：哪个没传就是空列表；某一个来源读取失败也只影响它自己那一类
（记 debug 日志），永远不抛异常——这是旁路观察，不能影响主流程。

注意 `WorkflowStore.__init__` 会 `mkdir`（有副作用），所以本模块**不会**自己构造它，
只在调用方显式传入 `workflow_store` 时才读取 workflows。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .capability import CapabilityState

_logger = logging.getLogger("mini_agent.core.trace")


def build_capability_state(
    registry: Optional[Any] = None,
    skill_loader: Optional[Any] = None,
    workflow_store: Optional[Any] = None,
) -> CapabilityState:
    """读取三个注册表的名字，生成一份 `CapabilityState`（不写入任何地方）。"""
    state = CapabilityState(refreshed_at=time.time())

    if registry is not None:
        try:
            state.tools = sorted(str(n) for n in registry.names())
        except Exception as exc:  # noqa: BLE001
            _logger.debug("capability_projector: 读取 ToolRegistry 失败：%r", exc)

    if skill_loader is not None:
        try:
            state.skills_available = sorted(str(n) for n in skill_loader.available)
            state.skills_active = sorted(str(n) for n in skill_loader.active)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("capability_projector: 读取 SkillLoader 失败：%r", exc)

    if workflow_store is not None:
        try:
            state.workflows = sorted(
                str(item["name"]) for item in workflow_store.list_all() if "name" in item
            )
        except Exception as exc:  # noqa: BLE001
            _logger.debug("capability_projector: 读取 WorkflowStore 失败：%r", exc)

    return state


def refresh_capability_state(
    registry: Optional[Any] = None,
    skill_loader: Optional[Any] = None,
    workflow_store: Optional[Any] = None,
    state_manager: Optional[Any] = None,
) -> CapabilityState:
    """生成快照并交给 `StateManager` 托管（kind=\"capability\"）。返回生成的快照。"""
    from .state_manager import get_state_manager

    state = build_capability_state(registry, skill_loader, workflow_store)
    (state_manager or get_state_manager()).update_state("capability", state)
    return state
