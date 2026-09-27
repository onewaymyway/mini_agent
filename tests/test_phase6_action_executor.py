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


# ── 未知 type 仍然显式抛错（不是静默假成功）──────────────────────────────────


def test_execute_truly_unknown_type_raises_not_implemented():
    """`tool`/`workflow`/`subagent` 三个已知取值之外的 type 仍然显式报错——
    Sprint 6-2 之后 `ActionType` 的取值空间已经全部实现，这里验证的是
    "非法 type"（`ActionSpec` 是普通 dataclass，运行时不强制 Literal），
    而不是 Sprint 6-1 时"合法但未实现"的旧行为。
    """
    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    executor = ActionExecutor(registry=registry, guard=guard, event_bus=EventBus())

    spec = ActionSpec(type="bogus_type", capability="whatever", arguments={})

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


# ── Sprint 6-2：type="workflow" ──────────────────────────────────────────────


class _FakeCfg:
    """duck-typed AppConfig：ActionExecutor 只读取这几个属性。"""

    def __init__(self, project_root="/tmp/does-not-matter"):
        self.project_root = project_root
        self.llm_provider = "fake-provider"
        self.llm_base_url = None
        self.api_key = "fake-key"


class _FakeWorkflowStore:
    def __init__(self, workflows: dict):
        self._workflows = workflows

    def load(self, name):
        return self._workflows.get(name)


def test_execute_workflow_missing_cfg_returns_failure_not_exception():
    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    executor = ActionExecutor(registry=registry, guard=guard, event_bus=EventBus())

    spec = ActionSpec(type="workflow", capability="some_workflow", arguments={})
    result = executor.execute(spec)

    assert result.success is False
    assert result.error.startswith("missing_cfg")


def test_execute_workflow_not_found_returns_failure():
    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    store = _FakeWorkflowStore({})
    executor = ActionExecutor(
        registry=registry, guard=guard, event_bus=EventBus(),
        cfg=_FakeCfg(), workflow_store=store,
    )

    spec = ActionSpec(type="workflow", capability="does_not_exist", arguments={})
    result = executor.execute(spec)

    assert result.success is False
    assert result.error == "workflow_not_found: does_not_exist"


def test_execute_workflow_success_forwards_to_workflow_runner(monkeypatch):
    sentinel_wf = object()
    store = _FakeWorkflowStore({"my_wf": sentinel_wf})
    bus = EventBus()
    events = []
    for kind in ("ActionStarted", "ActionCompleted", "ActionFailed"):
        bus.subscribe(kind, lambda e, _k=kind: events.append(e))

    captured = {}

    class _FakeRunResult:
        status = "done"
        error = None
        step_results = []

    class _FakeWorkflowRunner:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        def run(self, wf, inputs=None):
            captured["wf"] = wf
            captured["inputs"] = inputs
            return _FakeRunResult()

    monkeypatch.setattr(
        "mini_agent.workflow.runner.WorkflowRunner", _FakeWorkflowRunner
    )

    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    cfg = _FakeCfg()
    executor = ActionExecutor(
        registry=registry, guard=guard, event_bus=bus, cfg=cfg, workflow_store=store,
    )

    spec = ActionSpec(type="workflow", capability="my_wf", arguments={"code": "print(1)"})
    result = executor.execute(spec, correlation_id="corr-wf")

    assert result.success is True
    assert isinstance(result.output, _FakeRunResult)
    assert result.action_type == "workflow"
    assert result.capability == "my_wf"
    assert captured["wf"] is sentinel_wf
    assert captured["inputs"] == {"code": "print(1)"}
    assert captured["cfg"] is cfg
    assert [e.kind for e in events] == ["ActionStarted", "ActionCompleted"]


def test_execute_workflow_non_done_status_maps_to_failure(monkeypatch):
    store = _FakeWorkflowStore({"my_wf": object()})

    class _FakeRunResult:
        status = "failed"
        error = "step s1 blew up"

    class _FakeWorkflowRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs=None):
            return _FakeRunResult()

    monkeypatch.setattr(
        "mini_agent.workflow.runner.WorkflowRunner", _FakeWorkflowRunner
    )

    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    executor = ActionExecutor(
        registry=registry, guard=guard, event_bus=EventBus(),
        cfg=_FakeCfg(), workflow_store=store,
    )

    spec = ActionSpec(type="workflow", capability="my_wf", arguments={})
    result = executor.execute(spec)

    assert result.success is False
    assert "workflow_status_not_done: failed" in result.error
    assert "step s1 blew up" in result.error
    # 失败也不丢信息：output 仍然是完整的 WorkflowRunResult。
    assert isinstance(result.output, _FakeRunResult)


# ── Sprint 6-2：type="subagent" ──────────────────────────────────────────────


def test_execute_subagent_missing_prompt_returns_failure():
    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    executor = ActionExecutor(
        registry=registry, guard=guard, event_bus=EventBus(), cfg=_FakeCfg(),
    )

    spec = ActionSpec(type="subagent", capability="anything", arguments={})
    result = executor.execute(spec)

    assert result.success is False
    assert result.error.startswith("missing_argument")


def test_execute_subagent_success_forwards_to_build_minimal_agent(monkeypatch):
    captured = {}

    class _FakeAgent:
        def run_turn(self, prompt):
            captured["prompt"] = prompt
            return f"answer to: {prompt}"

    def _fake_build_minimal_agent(**kwargs):
        captured["kwargs"] = kwargs
        return _FakeAgent()

    monkeypatch.setattr(
        "mini_agent.workflow.agent_spawn.build_minimal_agent",
        _fake_build_minimal_agent,
    )

    bus = EventBus()
    events = []
    for kind in ("ActionStarted", "ActionCompleted", "ActionFailed"):
        bus.subscribe(kind, lambda e, _k=kind: events.append(e))

    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    executor = ActionExecutor(
        registry=registry, guard=guard, event_bus=bus, cfg=_FakeCfg(),
    )

    spec = ActionSpec(
        type="subagent", capability="research_subtask",
        arguments={"prompt": "总结一下 README"},
    )
    result = executor.execute(spec, correlation_id="corr-sub")

    assert result.success is True
    assert result.output == "answer to: 总结一下 README"
    assert result.action_type == "subagent"
    assert result.capability == "research_subtask"
    assert captured["prompt"] == "总结一下 README"
    assert captured["kwargs"]["sandbox"] is False
    assert [e.kind for e in events] == ["ActionStarted", "ActionCompleted"]


# ── Sprint 6-2 验收标准：三种 type 产出的 ActionResult 形状一致 ─────────────


def test_all_three_action_types_produce_uniform_result_shape(monkeypatch):
    """验收标准：'产出的 Experience 记录格式一致，不需要按 Action 类型
    特判处理'——这里直接断言三种 type 返回的 ActionResult 都具备同一组
    字段（dataclasses.fields 一致），不是靠某个分支多出/少了字段。
    """
    import dataclasses

    class _FakeRunResult:
        status = "done"
        error = None

    class _FakeWorkflowRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs=None):
            return _FakeRunResult()

    class _FakeAgent:
        def run_turn(self, prompt):
            return "ok"

    monkeypatch.setattr(
        "mini_agent.workflow.runner.WorkflowRunner", _FakeWorkflowRunner
    )
    monkeypatch.setattr(
        "mini_agent.workflow.agent_spawn.build_minimal_agent",
        lambda **kwargs: _FakeAgent(),
    )

    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    store = _FakeWorkflowStore({"wf": object()})
    executor = ActionExecutor(
        registry=registry, guard=guard, event_bus=EventBus(),
        cfg=_FakeCfg(), workflow_store=store,
    )

    tool_result = executor.execute(
        ActionSpec(type="tool", capability="echo_tool", arguments={"text": "x"})
    )
    workflow_result = executor.execute(
        ActionSpec(type="workflow", capability="wf", arguments={})
    )
    subagent_result = executor.execute(
        ActionSpec(type="subagent", capability="x", arguments={"prompt": "hi"})
    )

    field_names = {f.name for f in dataclasses.fields(ActionResult)}
    for result in (tool_result, workflow_result, subagent_result):
        assert result.success is True
        assert {f.name for f in dataclasses.fields(result)} == field_names
