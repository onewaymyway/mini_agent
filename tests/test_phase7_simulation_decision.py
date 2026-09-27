"""tests/test_phase7_simulation_decision.py

对应 `next_doc/refactor_plan/08-phase7-decision-simulation-sprint-plan.md`

Sprint 7-1 验收标准："给定一个真实 Goal 的 gap，系统能生成至少 2 个
候选 Action，并为每个候选生成一段包含'短期成本/长期影响/风险'的自然
语言描述，且这段描述里确实引用了 Phase 3 检索到的历史 Experience
（不是纯凭空生成）。"

Sprint 7-2 验收标准："一次真实 Goal 执行中，能看到完整的决策 trace：
为什么生成了这几个候选、每个候选的权衡是什么、最终为什么选了其中
一个。这个 trace 应该是可读的自然语言，而不是一堆内部对象的 dump。"
以及任务表"接入 Phase 6：选中的 ActionSpec 直接交给 ActionExecutor
执行"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.actions.executor import ActionExecutor
from mini_agent.cognition.decision import DecisionEngine, DecisionTrace
from mini_agent.core.action import ActionResult, ActionSpec
from mini_agent.core.event_bus import EventBus
from mini_agent.core.experience import Experience
from mini_agent.core.experience_store import ExperienceStore
from mini_agent.core.goal import GoalState
from mini_agent.core.simulation import SimulationResult, SimulationScenario
from mini_agent.core.state_manager import StateManager, reset_state_manager
from mini_agent.goals.gap import detect_gap
from mini_agent.permissions import PermissionGuard
from mini_agent.simulation.engine import generate_candidate_actions, simulate_candidates
from mini_agent.tools import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_manager():
    reset_state_manager()
    yield
    reset_state_manager()


def _echo_tool(text: str) -> str:
    return f"echo: {text}"


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry(namespace="test-phase7")
    registry.register_fn(_echo_tool, name="echo_tool", requires_approval=False)
    return registry


def _seed_experience_store(tmp_path: Path, goal_text: str) -> ExperienceStore:
    store = ExperienceStore(path=tmp_path / "experience_store.jsonl")
    store.append(Experience(
        source="goal_mode",
        goal_text=goal_text,
        status="done",
        rounds_used=3,
        final_report="上次用 echo_tool 直接替换占位符，一次成功",
        lesson="替换前先确认占位符的确切文本，避免误替换",
    ))
    return store


# ── Sprint 7-1：Candidate Actions 生成（拒绝无 llm_generate 的静默行为）──────


def test_generate_candidate_actions_requires_llm_generate():
    with pytest.raises(ValueError):
        generate_candidate_actions("gap-1", "goal", [], llm_generate=None)


def test_generate_candidate_actions_caps_at_max_candidates():
    def _fake_llm_generate(gap_item, goal_text, constraints):
        return [
            ActionSpec(type="tool", capability=f"candidate_{i}", arguments={})
            for i in range(5)
        ]

    candidates = generate_candidate_actions(
        "gap-1", "goal", [], llm_generate=_fake_llm_generate, max_candidates=3,
    )
    assert len(candidates) == 3


# ── Sprint 7-1：Simulation 必须引用检索到的历史 Experience ──────────────────


def test_simulate_candidates_cites_retrieved_experience(tmp_path):
    goal_text = "把 README 里的占位符替换掉"
    store = _seed_experience_store(tmp_path, goal_text)

    candidates = [
        ActionSpec(type="tool", capability="echo_tool", arguments={"text": "TODO"}),
        ActionSpec(type="tool", capability="bash", arguments={"command": "sed ..."}),
    ]
    scenario = SimulationScenario(
        current_state="README 里还有 TODO 占位符",
        goal=goal_text,
        candidate_actions=candidates,
        constraints=["不要破坏其它章节"],
    )

    captured_contexts = []

    def _fake_llm_narrate(action, goal, current_state, experience_context):
        captured_contexts.append(experience_context)
        return {
            "possible_futures": [f"用 {action.capability} 大概率能完成替换"],
            "risks": ["可能误替换非目标位置的相同文本"],
            "tradeoffs": (
                f"短期成本低（{action.capability} 一次调用即可），"
                "长期影响：" + (experience_context[:20] if experience_context else "无历史参考")
            ),
        }

    results = simulate_candidates(
        scenario, llm_narrate=_fake_llm_narrate, experience_store=store,
    )

    assert len(results) == 2
    for result in results:
        assert isinstance(result, SimulationResult)
        assert result.tradeoffs
        assert result.risks
        # 验收标准要求"确实引用了 Phase 3 检索到的历史 Experience"——
        # 用 experience_refs（可核验的 Experience.id 列表）而不是只靠人工
        # 读 tradeoffs 文本来确认，这里两个候选检索文本不同但都应命中
        # 同一条种子 Experience（goal 相同、capability 不同只影响关键词
        # 重叠度，不影响是否命中）。
        assert result.experience_refs, "应至少引用一条历史 Experience"
    # 两个候选各自独立检索，llm_narrate 被调用时收到的 experience_context
    # 不是空字符串（真的把检索结果传给了 LLM，不是摆设参数）。
    assert all(ctx for ctx in captured_contexts)


def test_simulate_candidates_requires_llm_narrate():
    scenario = SimulationScenario(
        current_state="x", goal="y",
        candidate_actions=[ActionSpec(type="tool", capability="echo_tool", arguments={})],
    )
    with pytest.raises(ValueError):
        simulate_candidates(scenario, llm_narrate=None)


# ── Sprint 7-2：DecisionEngine ───────────────────────────────────────────────


def test_decision_engine_requires_a_strategy():
    engine = DecisionEngine()
    results = [SimulationResult(action=ActionSpec(type="tool", capability="x", arguments={}))]
    with pytest.raises(ValueError):
        engine.select(results)


def test_decision_engine_human_confirm_takes_priority_over_llm_select():
    """同时传入两者时优先用 human_confirm（人工优先于 LLM 自动选择）。"""
    called = {"llm": False}

    def _llm_select(candidates, goal_text, gap_item):
        called["llm"] = True
        return 0, "不应该被调用"

    def _human_confirm(candidates):
        return 1, "人工选择第二个方案，风险更低"

    action_a = ActionSpec(type="tool", capability="candidate_a", arguments={})
    action_b = ActionSpec(type="tool", capability="candidate_b", arguments={})
    results = [
        SimulationResult(action=action_a, tradeoffs="快但风险高", risks=["可能失败"]),
        SimulationResult(action=action_b, tradeoffs="慢但稳妥", risks=[]),
    ]

    engine = DecisionEngine(llm_select=_llm_select, human_confirm=_human_confirm)
    chosen, trace = engine.select(results, goal_text="goal", gap_item="gap-1")

    assert called["llm"] is False
    assert chosen is action_b
    assert isinstance(trace, DecisionTrace)
    assert trace.selected_capability == "candidate_b"
    assert "人工选择" in trace.reason


def test_decision_trace_renders_readable_natural_language_not_object_dump():
    action_a = ActionSpec(type="tool", capability="candidate_a", arguments={})
    action_b = ActionSpec(type="tool", capability="candidate_b", arguments={})
    results = [
        SimulationResult(action=action_a, tradeoffs="快但风险高", risks=["可能失败"]),
        SimulationResult(action=action_b, tradeoffs="慢但稳妥", risks=[]),
    ]

    engine = DecisionEngine(llm_select=lambda c, g, gap: (1, "综合权衡后选择更稳妥的方案"))
    _, trace = engine.select(results, goal_text="把 README 修好", gap_item="TODO 未替换")

    text = trace.to_text()
    # 可读自然语言的最低要求：不是 repr()/dataclass dump（不含 "SimulationResult("
    # 这种内部对象痕迹），且确实包含 gap/目标/候选权衡/最终选择/理由。
    assert "SimulationResult(" not in text
    assert "TODO 未替换" in text
    assert "把 README 修好" in text
    assert "快但风险高" in text
    assert "慢但稳妥" in text
    assert "candidate_b" in text
    assert "综合权衡后选择更稳妥的方案" in text


# ── Sprint 7-2 全链路：Gap → Candidate Actions → Simulation → Decision →
#    Action → Outcome → Experience ──────────────────────────────────────────


def test_full_chain_gap_to_candidates_to_simulation_to_decision_to_action(tmp_path):
    goal = GoalState(
        goal_text="把 README 里的占位符替换掉",
        current_state="README 里还有 TODO 占位符",
        ideal_state="README 内容已经完整",
        acceptance_criteria=["替换所有 TODO 占位符"],
    )
    manager = StateManager()
    detect_gap(goal, state_manager=manager)
    assert goal.gap, "gap 检测应产出至少一条待处理的 gap"
    gap_item = goal.gap[0]

    store = _seed_experience_store(tmp_path, goal.goal_text)

    # 1) Candidate Actions 生成（fake llm_generate，产出 2 个候选）。
    def _fake_llm_generate(gap_item_, goal_text, constraints):
        return [
            ActionSpec(type="tool", capability="echo_tool",
                       arguments={"text": gap_item_}, expected_outcome="占位符已处理"),
            ActionSpec(type="tool", capability="echo_tool",
                       arguments={"text": f"手动处理：{gap_item_}"}),
        ]

    candidates = generate_candidate_actions(
        gap_item, goal.goal_text, goal.constraints, llm_generate=_fake_llm_generate,
    )
    assert len(candidates) == 2

    # 2) Simulation：对每个候选生成权衡描述，引用历史 Experience。
    scenario = SimulationScenario(
        current_state=goal.current_state,
        goal=goal.goal_text,
        candidate_actions=candidates,
        constraints=goal.constraints,
    )

    def _fake_llm_narrate(action, goal_text, current_state, experience_context):
        has_history = bool(experience_context)
        return {
            "possible_futures": [f"用 {action.capability} 处理「{action.arguments.get('text')}」"],
            "risks": ["可能误替换"] if not has_history else ["按历史经验规避了误替换风险"],
            "tradeoffs": f"短期成本低；参考历史：{'有' if has_history else '无'}",
        }

    sim_results = simulate_candidates(
        scenario, llm_narrate=_fake_llm_narrate, experience_store=store,
    )
    assert all(r.experience_refs for r in sim_results)

    # 3) Decision：选出最终 ActionSpec，产出可读 trace。
    def _llm_select(results, goal_text, gap_item_):
        # 简单策略：选第一个（真实场景由 LLM 判断，这里只验证链路打通）。
        return 0, "第一个候选历史成功率高，优先采用"

    engine = DecisionEngine(llm_select=_llm_select)
    chosen_action, trace = engine.select(sim_results, goal_text=goal.goal_text, gap_item=gap_item)
    assert chosen_action is candidates[0]
    trace_text = trace.to_text()
    assert gap_item in trace_text
    assert "第一个候选历史成功率高" in trace_text

    # 4) Action：交给 Phase 6 的 ActionExecutor 执行。
    correlation_id = "phase7-full-chain"
    bus = EventBus()
    events = []
    for kind in ("ActionStarted", "ActionCompleted", "ActionFailed"):
        bus.subscribe(kind, lambda e, _k=kind: events.append(e))

    registry = _make_registry()
    guard = PermissionGuard(auto_approve=True)
    executor = ActionExecutor(registry=registry, guard=guard, event_bus=bus)

    result = executor.execute(chosen_action, correlation_id=correlation_id)

    assert isinstance(result, ActionResult)
    assert result.success is True
    assert gap_item in str(result.output)
    assert [e.kind for e in events] == ["ActionStarted", "ActionCompleted"]

    # 5) Outcome → Experience：本测试不重复 Phase 3 ExperienceRecorder 的
    # 订阅逻辑（那部分已由 `tests/test_phase3_experience_recorder.py`
    # 覆盖），这里只验证"这次 Action 的产出可以被组织成一条 Experience"，
    # 证明链路的最后一环在数据形状上是通的。
    experience = Experience(
        source="phase7_decision",
        goal_text=goal.goal_text,
        status="done" if result.success else "failed",
        rounds_used=1,
        final_report=str(result.output),
        action=chosen_action.capability,
        reason=trace.reason,
        lesson="" if result.success else str(result.error),
    )
    store.append(experience)
    all_experiences = store.all()
    assert any(e.id == experience.id for e in all_experiences)
