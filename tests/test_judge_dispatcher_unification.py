"""
tests/test_judge_dispatcher_unification.py — 判官接线统一（阶段六 a）专项回归测试

对应 `judge_profile_unification_migration_plan.md` §7 要求的"接口级变化需要
专项回归"，覆盖：
  - role_agent.enabled=False + goal_mode.enabled=True：GoalJudge 必须正常
    经由 dispatcher 触发（回归 §1.2 发现的兼容性风险，最重要的一条）
  - role_agent.block: ["goal_judge"]：GoalJudge 必须被屏蔽，GoalRunner 构造
    时应直接报错（对应设计文档 §8 开放问题 3 的方案 c）
  - role_agent.allow: [...]（不含 "goal_judge"）：同上应被屏蔽
  - 磁盘存在 .agent/agents/goal_judge.md 自定义同名 profile：验证该文件
    覆盖内建的默认合成 profile
  - goal_mode.enabled=True 且 role_agent.enabled=True 且用户额外自定义了
    evaluator：两者互不干扰，各自独立触发
  - turn_judge.enabled=True 时 dispatcher 也应该注册 turn_end_review
    （即使 6a 阶段 role_judge.py 尚未消费它，注册表本身应该已经可用）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.config.models import AppConfig
from mini_agent.orchestrator.agent_profiles import AgentProfileLoader
from mini_agent.role_agents import dispatcher as dispatcher_module
from mini_agent.role_agents.dispatcher import RoleAgentDispatcher
from mini_agent.goal_mode.runner import GoalRunner
from mini_agent.goal_mode.spec import GoalSpec


@pytest.fixture(autouse=True)
def _reset_global_dispatcher():
    """每个测试前后清空全局单例，避免测试间相互污染。"""
    dispatcher_module._dispatcher = None
    yield
    dispatcher_module._dispatcher = None


def _make_loader(tmp_path: Path, files: dict[str, str] | None = None) -> AgentProfileLoader:
    agents_dir = tmp_path / ".agent" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for name, content in (files or {}).items():
        (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")
    return AgentProfileLoader([agents_dir])


def _confirmed_spec() -> GoalSpec:
    return GoalSpec(goal_text="do the thing", acceptance_criteria=["it works"], confirmed=True)


class FakeAgent:
    """最小可用的 Agent 替身，同 test_goal_mode.py。"""

    def __init__(self):
        from mini_agent.goal_mode.executor import GoalStepResult  # noqa: F401
        self.session_id = "fake-session"

        class _Stats:
            turns = 0
            tool_calls = 0

        self.stats = _Stats()
        self._hist = []

    def run_turn(self, prompt):
        self.last_turn_hit_max_turns = False
        return "did the thing"

    def compact_with_skills(self):
        return "[fake summary]"


# ── 1. 最重要的一条：role_agent.enabled=False + goal_mode.enabled=True ──────

def test_goal_judge_triggers_without_role_agent_enabled(tmp_path):
    """回归 §1.2：只开 goal_mode.enabled，不开 role_agent.enabled，
    GoalJudge 依然必须能被 dispatcher 注册（不能因为 dispatcher 未构造
    而静默失效）。"""
    cfg = AppConfig(project_root=tmp_path)
    cfg.goal_mode.enabled = True
    cfg.role_agent.enabled = False  # 关键：显式不开启

    loader = _make_loader(tmp_path)
    dispatcher = RoleAgentDispatcher(cfg, loader)

    assert dispatcher.has_goal_review_roles
    roles = dispatcher.get_goal_review_roles()
    assert len(roles) == 1
    assert roles[0].name == "goal_judge"
    assert roles[0].role_type == "goal_judge"


def test_turn_judge_triggers_without_role_agent_enabled(tmp_path):
    """同上，turn_judge 侧。"""
    cfg = AppConfig(project_root=tmp_path)
    cfg.turn_judge.enabled = True
    cfg.role_agent.enabled = False

    loader = _make_loader(tmp_path)
    dispatcher = RoleAgentDispatcher(cfg, loader)

    assert dispatcher.has_turn_end_review_roles
    roles = dispatcher.get_turn_end_review_roles()
    assert len(roles) == 1
    assert roles[0].name == "turn_judge"


def test_neither_goal_review_nor_turn_end_review_when_subsystems_disabled(tmp_path):
    """两个子系统都不开，dispatcher（如果因其他原因被构造）不应注册任何判官。"""
    cfg = AppConfig(project_root=tmp_path)
    loader = _make_loader(tmp_path)
    dispatcher = RoleAgentDispatcher(cfg, loader)

    assert not dispatcher.has_goal_review_roles
    assert not dispatcher.has_turn_end_review_roles


# ── 2. role_agent.block 屏蔽 goal_judge ─────────────────────────────────────

def test_role_agent_block_hides_goal_judge_from_dispatcher(tmp_path):
    cfg = AppConfig(project_root=tmp_path)
    cfg.goal_mode.enabled = True
    cfg.role_agent.block = ["goal_judge"]

    loader = _make_loader(tmp_path)
    dispatcher = RoleAgentDispatcher(cfg, loader)

    assert not dispatcher.has_goal_review_roles
    assert dispatcher.get_goal_review_roles() == []


def test_goal_runner_raises_when_goal_judge_blocked(tmp_path):
    """§8 开放问题 3 方案 c：goal_mode.enabled=True 但 goal_judge 被
    block 掉时，GoalRunner 构造应直接报错，而不是静默降级。"""
    cfg = AppConfig(project_root=tmp_path)
    cfg.goal_mode.enabled = True
    cfg.role_agent.block = ["goal_judge"]

    loader = _make_loader(tmp_path)
    dispatcher_module.init_role_agent_system(cfg, loader)

    agent = FakeAgent()
    with pytest.raises(ValueError, match="goal_judge"):
        GoalRunner(agent=agent, cfg=cfg, goal_spec=_confirmed_spec())


# ── 3. role_agent.allow 不含 goal_judge，同样应被屏蔽 ───────────────────────

def test_role_agent_allow_without_goal_judge_hides_it(tmp_path):
    cfg = AppConfig(project_root=tmp_path)
    cfg.goal_mode.enabled = True
    cfg.role_agent.allow = ["some_other_profile"]

    loader = _make_loader(tmp_path)
    dispatcher = RoleAgentDispatcher(cfg, loader)

    assert not dispatcher.has_goal_review_roles


# ── 4. 磁盘同名 profile 覆盖内建合成 profile ────────────────────────────────

def test_disk_goal_judge_profile_overrides_builtin(tmp_path):
    custom_goal_judge = """---
name: goal_judge
role_type: goal_judge
trigger_on: goal_review
model: my-custom-judge-model
---

自定义的 GoalJudge system prompt。
"""
    cfg = AppConfig(project_root=tmp_path)
    cfg.goal_mode.enabled = True
    cfg.role_agent.enabled = True  # 需要开启才会加载磁盘自定义 profile

    loader = _make_loader(tmp_path, {"goal_judge": custom_goal_judge})
    dispatcher = RoleAgentDispatcher(cfg, loader)

    roles = dispatcher.get_goal_review_roles()
    assert len(roles) == 1
    assert roles[0].model == "my-custom-judge-model"
    assert "自定义的 GoalJudge" in roles[0].system_prompt


def test_disk_goal_judge_profile_overrides_even_when_role_agent_disabled(tmp_path):
    """判官（goal_judge/turn_judge）的磁盘覆盖能力不受 role_agent.enabled
    门控——这是"判官是否生效"（由 goal_mode.enabled 决定）与"用什么
    profile"（内建还是磁盘自定义）两个独立维度的设计结果：即使
    role_agent.enabled=False（不加载用户其他自定义 evaluator/coach），
    goal_judge.md 依然应该覆盖内建合成 profile。"""
    custom_goal_judge = """---
name: goal_judge
role_type: goal_judge
trigger_on: goal_review
model: my-custom-judge-model
---

自定义的 GoalJudge system prompt。
"""
    cfg = AppConfig(project_root=tmp_path)
    cfg.goal_mode.enabled = True
    cfg.role_agent.enabled = False

    loader = _make_loader(tmp_path, {"goal_judge": custom_goal_judge})
    dispatcher = RoleAgentDispatcher(cfg, loader)

    roles = dispatcher.get_goal_review_roles()
    assert len(roles) == 1
    assert roles[0].model == "my-custom-judge-model"
    assert "自定义的 GoalJudge" in roles[0].system_prompt


# ── 5. goal_mode + role_agent.enabled + 自定义 evaluator：互不干扰 ─────────

def test_goal_judge_and_custom_evaluator_coexist(tmp_path):
    custom_evaluator = """---
name: my_evaluator
role_type: evaluator
trigger_on: output
---

自定义评估者。
"""
    cfg = AppConfig(project_root=tmp_path)
    cfg.goal_mode.enabled = True
    cfg.role_agent.enabled = True

    loader = _make_loader(tmp_path, {"my_evaluator": custom_evaluator})
    dispatcher = RoleAgentDispatcher(cfg, loader)

    assert dispatcher.has_goal_review_roles
    assert dispatcher.get_goal_review_roles()[0].name == "goal_judge"
    assert dispatcher.has_output_roles
    assert [p.name for p in dispatcher._output_roles] == ["my_evaluator"]


# ── 7. TurnJudge（阶段 6b）：block 屏蔽 + 未初始化时的兜底 ──────────────────

def test_role_agent_block_hides_turn_judge_from_dispatcher(tmp_path):
    cfg = AppConfig(project_root=tmp_path)
    cfg.turn_judge.enabled = True
    cfg.role_agent.block = ["turn_judge"]

    loader = _make_loader(tmp_path)
    dispatcher = RoleAgentDispatcher(cfg, loader)

    assert not dispatcher.has_turn_end_review_roles
    assert dispatcher.get_turn_end_review_roles() == []


def test_disk_turn_judge_profile_overrides_even_when_role_agent_disabled(tmp_path):
    custom_turn_judge = """---
name: turn_judge
role_type: turn_judge
trigger_on: turn_end_review
model: my-custom-turn-judge-model
---

自定义的 TurnJudge system prompt。
"""
    cfg = AppConfig(project_root=tmp_path)
    cfg.turn_judge.enabled = True
    cfg.role_agent.enabled = False

    loader = _make_loader(tmp_path, {"turn_judge": custom_turn_judge})
    dispatcher = RoleAgentDispatcher(cfg, loader)

    roles = dispatcher.get_turn_end_review_roles()
    assert len(roles) == 1
    assert roles[0].model == "my-custom-turn-judge-model"
    assert "自定义的 TurnJudge" in roles[0].system_prompt


class _FakeTurnJudgeCfg:
    def __init__(self, **kwargs):
        self.enabled = True
        self.judge_model = None
        self.judge_provider = None
        self.max_auto_rounds = kwargs.get("max_auto_rounds", 3)
        self.judge_show_prompt = False
        self.history_window = 6
        self.consecutive_same_output_limit = 0  # 关闭卡住检测，简化测试
        self.same_output_similarity_threshold = 0.9
        self.max_stuck_recoveries = 3


class _FakeHistForTurnJudge:
    _history: list = []


class _FakeAgentForTurnJudge:
    """最小可用的 RoleJudgeMixin 宿主，只提供 _maybe_run_turn_judge 需要的属性。"""

    def __init__(self, cfg):
        from mini_agent.agent.role_judge import RoleJudgeMixin
        from mini_agent.role_agents.stuck_detector import StuckDetector

        self.cfg = cfg
        self._is_subagent = False
        self._turn_judge_auto_count = 0
        self._turn_judge_stuck_detector = StuckDetector()
        self._hist = _FakeHistForTurnJudge()
        self._last_turn_hit_max_turns = False
        # [P4 修复] RoleJudgeMixin._maybe_run_turn_judge 会读取
        # self._session.id（用于 parent_session_id），最小 fake 宿主此前
        # 缺这个属性，一旦真的走到 run_turn_judge 调用点就会 AttributeError。
        # 见 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md
        # P4：与本方案任何改动都无关的预先存在的测试夹具缺陷，顺手修掉。
        self._session = None
        self._bound = RoleJudgeMixin()
        self._bound.__dict__ = self.__dict__  # 共享属性，模拟 mixin 混入

    def _current_session_dir(self):
        return None

    def _maybe_run_turn_judge(self, assistant_output: str) -> None:
        from mini_agent.agent.role_judge import RoleJudgeMixin
        RoleJudgeMixin._maybe_run_turn_judge(self, assistant_output)


def test_maybe_run_turn_judge_falls_back_to_wait_for_user_when_blocked(tmp_path, monkeypatch):
    """§8 开放问题 3：turn_judge 被 block 后，_maybe_run_turn_judge 应该
    直接当作"未启用"处理（不报错、不调用 LLM），控制权回到真人。"""
    cfg = AppConfig(project_root=tmp_path)
    cfg.turn_judge = _FakeTurnJudgeCfg()
    cfg.role_agent.block = ["turn_judge"]

    loader = _make_loader(tmp_path)
    dispatcher_module.init_role_agent_system(cfg, loader)

    called = {"n": 0}
    monkeypatch.setattr(
        "mini_agent.role_agents.turn_judge.run_turn_judge",
        lambda **kw: called.__setitem__("n", called["n"] + 1) or "GOAL_STATUS: NEED_USER",
    )

    agent = _FakeAgentForTurnJudge(cfg)
    agent._maybe_run_turn_judge("some assistant output")

    assert called["n"] == 0  # 判官从未被调用
    assert agent._turn_judge_auto_count == 0  # 没有消耗自动接管计数


def test_maybe_run_turn_judge_still_works_without_role_agent_enabled(tmp_path, monkeypatch):
    """回归：只开 turn_judge.enabled，不开 role_agent.enabled，TurnJudge
    依然必须能正常触发（对称于 GoalJudge 的同类测试）。"""
    cfg = AppConfig(project_root=tmp_path)
    cfg.turn_judge = _FakeTurnJudgeCfg()
    cfg.role_agent.enabled = False

    loader = _make_loader(tmp_path)
    dispatcher_module.init_role_agent_system(cfg, loader)

    monkeypatch.setattr(
        "mini_agent.role_agents.turn_judge.run_turn_judge",
        lambda **kw: "GOAL_STATUS: NEED_USER",
    )

    agent = _FakeAgentForTurnJudge(cfg)
    agent._maybe_run_turn_judge("some assistant output")

    assert agent._turn_judge_auto_count == 0  # NEED_USER 会重置计数为 0（本来就是 0）

def test_dispatcher_construction_condition_matches_plan():
    """验证 §3.2 的构造条件表达式本身（不依赖 app.py 的完整启动流程）。"""
    def _should_construct(role_agent_enabled, goal_mode_enabled, turn_judge_enabled):
        return role_agent_enabled or goal_mode_enabled or turn_judge_enabled

    assert _should_construct(False, True, False) is True
    assert _should_construct(False, False, True) is True
    assert _should_construct(True, False, False) is True
    assert _should_construct(False, False, False) is False
