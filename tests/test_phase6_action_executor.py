"""tests/test_phase6_action_executor.py

对应 `next_doc/refactor_plan/07-phase6-action-model-sprint-plan.md`
Sprint 6-1 验收标准："一个真实 Goal 从'检测到 gap'到'通过 ActionExecutor
调用一个 Tool 并拿到结果'全流程打通，且过程中权限检查确实生效（可以用
一个故意越权的场景验证拦截是否工作）"。
"""

from __future__ import annotations

import pytest

from mini_agent.actions.executor import ActionExecutor
from mini_agent.core.action import ActionResult, ActionSpec
from mini_agent.core.event_bus import EventBus
from mini_agent.core.goal import GoalState
from mini_agent.core.state_manager import StateManager, reset_state_manager
from mini_agent.goals.gap import detect_gap
from mini_agent.permissions import PermissionGuard
from mini_agent.tools import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_manager():
    reset_state_manager()
    yield
    reset_state_manager()


def _echo_tool(text: str) -> str:
    """一个不需要审批的测试用工具，直接回显参数。"""
    return f"echo: {text}"


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry(namespace="test-phase6")
    registry.register_fn(_echo_tool, name="echo_tool", requires_approval=False)
    return registry


# ── Sprint 6-1：Tool 调用成功路径 ────────────────────────────────────────────


def test_execute_tool_success_returns_output_and_publishes_events():
    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    bus = EventBus()
    received = []
    for kind in ("ActionStarted", "ActionCompleted", "ActionFailed"):
        bus.subscribe(kind, lambda e, _k=kind: received.append(e))

    executor = ActionExecutor(registry=registry, guard=guard, event_bus=bus)
    spec = ActionSpec(type="tool", capability="echo_tool", arguments={"text": "hi"})

    result = executor.execute(spec, correlation_id="corr-1")

    assert isinstance(result, ActionResult)
    assert result.success is True
    assert result.output == "echo: hi"
    assert result.error is None
    assert result.action_type == "tool"
    assert result.capability == "echo_tool"

    kinds = [e.kind for e in received]
    assert kinds == ["ActionStarted", "ActionCompleted"]
    assert all(e.correlation_id == "corr-1" for e in received)
    # ActionCompleted 的 causation_id 指向 ActionStarted 的 id
    assert received[1].causation_id == received[0].id


# ── Sprint 6-1：权限拦截（"故意越权"场景）────────────────────────────────────


def test_execute_tool_blocked_by_sandbox_permission_returns_failure_not_exception():
    registry = ToolRegistry(namespace="test-phase6-risky")
    # bash 本身不在测试环境注册也没关系：sandbox 检查发生在
    # ToolRegistry.call() 之前，权限被拒绝时根本不会走到查找工具这一步。
    guard = PermissionGuard(sandbox=True)  # sandbox 模式拦截所有 _RISKY_TOOLS
    bus = EventBus()
    failed_events = []
    bus.subscribe("ActionFailed", lambda e: failed_events.append(e))
    bus.subscribe("ActionCompleted", lambda e: pytest.fail("不应该执行到成功分支"))

    executor = ActionExecutor(registry=registry, guard=guard, event_bus=bus)
    spec = ActionSpec(type="tool", capability="bash", arguments={"command": "rm -rf /"})

    result = executor.execute(spec, correlation_id="corr-2")

    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("permission_denied: bash")
    assert len(failed_events) == 1
    assert failed_events[0].correlation_id == "corr-2"


# ── Sprint 6-1：workflow/subagent 显式未实现（不是静默假成功）───────────────


@pytest.mark.parametrize("action_type", ["workflow", "subagent"])
def test_execute_unsupported_type_raises_not_implemented(action_type):
    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    executor = ActionExecutor(registry=registry, guard=guard, event_bus=EventBus())

    spec = ActionSpec(type=action_type, capability="whatever", arguments={})

    with pytest.raises(NotImplementedError):
        executor.execute(spec)


# ── Sprint 6-1：Gap → ActionSpec → ActionExecutor 全链路 ────────────────────


def test_goal_gap_to_action_executor_full_chain_with_shared_correlation_id():
    """模拟"检测到 gap → 通过 ActionExecutor 调用一个 Tool 并拿到结果"全流程。

    真实 Goal 链路（`goal_mode/runner.py`）里 GoalCreated/ActionStarted/
    ActionCompleted 共享同一个 correlation_id（见该文件 `# [Phase 2
    Sprint 2-1]` 标注的接入点）；本测试复用同样的模式，验证
    Phase 5 的 gap 检测结果可以真的驱动一次 ActionExecutor 调用。
    """
    goal = GoalState(
        goal_text="把 README 里的占位符替换掉",
        current_state="README 里还有 TODO 占位符",
        ideal_state="README 内容已经完整",
        acceptance_criteria=["替换所有 TODO 占位符"],
    )
    manager = StateManager()
    detect_gap(goal, state_manager=manager)
    assert goal.gap, "gap 检测应产出至少一条待处理的 gap"

    correlation_id = "goal-corr-full-chain"
    bus = EventBus()
    events = []
    for kind in ("ActionStarted", "ActionCompleted", "ActionFailed"):
        bus.subscribe(kind, lambda e, _k=kind: events.append(e))

    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    executor = ActionExecutor(registry=registry, guard=guard, event_bus=bus)

    # 把 gap 里的第一条转成一次 ActionSpec（Sprint 6-1 只要求打通链路，
    # 不实现真正的"从 gap 自动规划出 ActionSpec"——那是更完整的 Planner，
    # 不在本 Sprint 范围内）。
    spec = ActionSpec(
        type="tool",
        capability="echo_tool",
        arguments={"text": goal.gap[0]},
        expected_outcome="gap 对应的占位符已被处理",
    )
    result = executor.execute(spec, correlation_id=correlation_id)

    assert result.success is True
    assert goal.gap[0] in str(result.output)
    assert [e.kind for e in events] == ["ActionStarted", "ActionCompleted"]
    assert all(e.correlation_id == correlation_id for e in events)
