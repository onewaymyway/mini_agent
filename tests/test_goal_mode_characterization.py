"""tests/test_goal_mode_characterization.py

Sprint 0（`next_doc/refactor_plan/02-executable-sprint-plan.md`）
"为 goal_mode 补特征测试"任务的产出。

目的：不追求验证 `goal_mode` 现有逻辑"对不对"，只录制"迁移前的行为"，
作为后续 Sprint（把 `goal_mode/runner.py` 接入 `core/adapter.py` 定义的
`GoalAdapter`）的安全网——迁移完成后重跑这些测试，全部通过即说明外部
可观察行为没有被破坏。

覆盖范围（按 Sprint 0 "覆盖主要分支即可，不要求 100%" 的要求）：
  1. `goal_mode/executor.py::CoarseStepExecutor` —— 完整覆盖，这是
     Sprint 1 GoalAdapter 接入点两侧之一（另一侧见 `tests/test_goal_mode.py`
     里已有的 92 个 GoalRunner 状态机测试，覆盖了主循环 DONE / CONTINUE /
     NEED_COMPACT / stuck / max_rounds 等主要分支，这里不重复）。
  2. `goal_mode/runner.py` 里若干不依赖真实 LLM / Agent 主循环、可独立
     测试的纯函数与轻状态辅助方法：
       - `render_replan_proposal`（模块级纯函数）
       - `GoalRunner._compute_progress_score`
       - `GoalRunner._record_dead_end` / `_render_dead_ends_block`
       - `GoalRunner._extract_replan_proposal`
       - `GoalRunner._build_goal_aware_compact_hint`
     这些方法在 `tests/test_goal_mode.py` 里只是被主循环间接触发，本文件
     对它们做更直接、更细粒度的输入→输出快照，方便后续迁移时快速定位
     具体是哪个辅助函数的行为变了。

注意：本文件里构造 `GoalRunner` 实例一律使用 `GoalRunner.__new__(GoalRunner)`
绕开 `__init__`（避免拉起 role_agents dispatcher 等重依赖），只手动填充
被测方法实际用到的实例属性——这本身就是"特征测试只测行为、不测构造过程"
的体现。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mini_agent.goal_mode.executor import CoarseStepExecutor, GoalStepResult
from mini_agent.goal_mode.runner import GoalRunner, render_replan_proposal


# ── CoarseStepExecutor ──────────────────────────────────────────────────

class _FakeStats:
    def __init__(self, turns=0, tool_calls=0):
        self.turns = turns
        self.tool_calls = tool_calls


class _FakeAgentForExecutor:
    """比 test_goal_mode.py 的 FakeAgent 更轻量，只服务本文件的执行器测试。"""

    def __init__(self, output, hit_max_turns=False, turns_delta=1, tool_calls_delta=0):
        self.stats = _FakeStats()
        self._output = output
        self._hit_max_turns = hit_max_turns
        self._turns_delta = turns_delta
        self._tool_calls_delta = tool_calls_delta
        self.last_turn_hit_max_turns = False
        self.received_prompt = None

    def run_turn(self, prompt):
        self.received_prompt = prompt
        self.stats.turns += self._turns_delta
        self.stats.tool_calls += self._tool_calls_delta
        self.last_turn_hit_max_turns = self._hit_max_turns
        return self._output


def test_coarse_step_executor_basic_result_shape():
    agent = _FakeAgentForExecutor(output="hello world", turns_delta=1, tool_calls_delta=3)
    executor = CoarseStepExecutor()

    result = executor.execute(agent, "do the thing")

    assert agent.received_prompt == "do the thing"
    assert isinstance(result, GoalStepResult)
    assert result.output == "hello world"
    assert result.hit_max_turns is False
    assert result.turns_used == 1
    assert result.tool_calls_made == 3


def test_coarse_step_executor_hit_max_turns_flag_propagates():
    agent = _FakeAgentForExecutor(output="partial", hit_max_turns=True)
    executor = CoarseStepExecutor()

    result = executor.execute(agent, "prompt")

    assert result.hit_max_turns is True


def test_coarse_step_executor_deltas_are_measured_not_absolute():
    """turns_used/tool_calls_made 是"这一步的增量"，不是 stats 的绝对值——
    模拟 agent 在调用前 stats 已经非零的情况（比如这是第 5 步）。"""
    agent = _FakeAgentForExecutor(output="x", turns_delta=2, tool_calls_delta=5)
    agent.stats = _FakeStats(turns=10, tool_calls=20)  # 模拟已经跑过前几步

    result = CoarseStepExecutor().execute(agent, "prompt")

    assert result.turns_used == 2
    assert result.tool_calls_made == 5


def test_coarse_step_executor_missing_hit_max_turns_attr_defaults_false():
    """agent 没有 last_turn_hit_max_turns 属性时（理论上不应发生，但
    executor.py 用 getattr(..., False) 兜底），不应报错。"""

    class _MinimalAgent:
        def __init__(self):
            self.stats = _FakeStats()

        def run_turn(self, prompt):
            self.stats.turns += 1
            return "output"

    result = CoarseStepExecutor().execute(_MinimalAgent(), "p")
    assert result.hit_max_turns is False


# ── render_replan_proposal（模块级纯函数）────────────────────────────────

def test_render_replan_proposal_empty_dict_returns_empty_string():
    assert render_replan_proposal({}) == ""


def test_render_replan_proposal_none_returns_empty_string():
    assert render_replan_proposal(None) == ""


def test_render_replan_proposal_with_all_fields():
    proposal = {
        "suggested_split": ["子目标A", "子目标B"],
        "suggested_criteria_changes": ["放宽标准X"],
        "reason": "原目标范围过大",
    }
    text = render_replan_proposal(proposal)
    assert "建议拆分为子目标" in text
    assert "子目标A" in text and "子目标B" in text
    assert "建议调整验收标准" in text
    assert "放宽标准X" in text
    assert "理由：原目标范围过大" in text


def test_render_replan_proposal_scalar_split_is_wrapped_as_single_item():
    """suggested_split 不是 list 而是单个字符串时，也要能正常渲染
    （不能因为不是 list 就报错或漏渲染）。"""
    text = render_replan_proposal({"suggested_split": "单个子目标"})
    assert "单个子目标" in text


def test_render_replan_proposal_only_marker_no_content():
    """proposal 非空但没有任何已知字段时，走"空壳提议"分支。"""
    text = render_replan_proposal({"unknown_field": "x"})
    assert "提议内容为空" in text


# ── GoalRunner._compute_progress_score ──────────────────────────────────

def _make_bare_runner(**attrs) -> GoalRunner:
    """构造一个跳过 __init__ 的 GoalRunner，只用于测试单个方法。"""
    runner = GoalRunner.__new__(GoalRunner)
    for k, v in attrs.items():
        setattr(runner, k, v)
    return runner


def _gm_cfg(criteria_tracking_enabled=True):
    return SimpleNamespace(criteria_tracking_enabled=criteria_tracking_enabled)


@pytest.mark.parametrize(
    "progress, expected",
    [
        ("SUBSTANTIVE_ADVANCE", 1.0),
        ("SAME_APPROACH_NO_GAIN", 0.0),
        ("REGRESSED", -1.0),
        (None, None),  # 无主观信号 + 无客观信号（criteria 为空）→ None
    ],
)
def test_compute_progress_score_subjective_only(progress, expected):
    runner = _make_bare_runner(
        _gm_cfg=_gm_cfg(),
        _criteria_status=[],
        _last_passed_count=0,
    )
    assert runner._compute_progress_score(progress) == expected


def test_compute_progress_score_objective_delta_positive_floors_subjective():
    """delta > 0 时，分数至少是 0.3 * delta，哪怕主观判断认为没有进展。"""
    runner = _make_bare_runner(
        _gm_cfg=_gm_cfg(),
        _criteria_status=[{"passed": True}, {"passed": True}, {"passed": False}],
        _last_passed_count=0,
    )
    score = runner._compute_progress_score("SAME_APPROACH_NO_GAIN")  # subjective=0.0
    # delta = 2 - 0 = 2 → max(0.0, 0.3*2) = 0.6
    assert score == pytest.approx(0.6)
    assert runner._last_passed_count == 2  # 副作用：更新了 last_passed_count


def test_compute_progress_score_objective_delta_negative_caps_at_minus_half():
    runner = _make_bare_runner(
        _gm_cfg=_gm_cfg(),
        _criteria_status=[{"passed": False}],
        _last_passed_count=1,
    )
    score = runner._compute_progress_score("SUBSTANTIVE_ADVANCE")  # subjective=1.0
    # delta = 0 - 1 = -1 → min(1.0, -0.5) = -0.5
    assert score == pytest.approx(-0.5)


def test_compute_progress_score_zero_delta_uses_subjective_only():
    runner = _make_bare_runner(
        _gm_cfg=_gm_cfg(),
        _criteria_status=[{"passed": True}],
        _last_passed_count=1,
    )
    score = runner._compute_progress_score("REGRESSED")
    assert score == pytest.approx(-1.0)


def test_compute_progress_score_criteria_tracking_disabled_falls_back_to_subjective():
    runner = _make_bare_runner(
        _gm_cfg=_gm_cfg(criteria_tracking_enabled=False),
        _criteria_status=[{"passed": True}],  # 有数据但开关关闭，应被忽略
        _last_passed_count=0,
    )
    score = runner._compute_progress_score("SUBSTANTIVE_ADVANCE")
    assert score == pytest.approx(1.0)
    assert runner._last_passed_count == 0  # 未被更新（开关关闭时不计算 delta）


# ── GoalRunner._record_dead_end / _render_dead_ends_block ───────────────

def test_render_dead_ends_block_empty_returns_empty_string():
    runner = _make_bare_runner(_dead_ends=[])
    assert runner._render_dead_ends_block() == ""


def test_record_dead_end_then_render_includes_reason_and_round():
    runner = _make_bare_runner(_dead_ends=[])
    runner._record_dead_end(round_no=3, progress="SAME_APPROACH_NO_GAIN", reason="尝试用正则解析失败")

    assert len(runner._dead_ends) == 1
    assert runner._dead_ends[0]["stuck_category"] == "unknown"  # 默认值（未开启归因分类）

    text = runner._render_dead_ends_block()
    assert "尝试用正则解析失败" in text
    assert "第 3 轮" in text
    assert "已验证过" in text or "验证过" in text


def test_record_dead_end_deduplicates_near_duplicate_reasons():
    runner = _make_bare_runner(_dead_ends=[])
    runner._record_dead_end(1, "progress", "尝试方案A解析JSON失败")
    runner._record_dead_end(2, "progress", "尝试方案A解析JSON失败")  # 完全重复
    assert len(runner._dead_ends) == 1


def test_record_dead_end_uses_last_stuck_category_when_present():
    runner = _make_bare_runner(_dead_ends=[], _last_stuck_category="genuine_difficulty")
    runner._record_dead_end(1, "progress", "唯一的一条理由")
    assert runner._dead_ends[0]["stuck_category"] == "genuine_difficulty"


# ── GoalRunner._extract_replan_proposal ──────────────────────────────────

def test_extract_replan_proposal_none_on_empty_text():
    runner = _make_bare_runner()
    assert runner._extract_replan_proposal("") is None
    assert runner._extract_replan_proposal(None) is None


def test_extract_replan_proposal_none_when_no_block_present():
    runner = _make_bare_runner()
    assert runner._extract_replan_proposal("普通的输出文本，没有任何代码块") is None


def test_extract_replan_proposal_parses_valid_block():
    runner = _make_bare_runner()
    text = (
        "一些前置说明\n"
        "```replan_proposal\n"
        '{"reason": "范围太大", "suggested_split": ["a", "b"]}\n'
        "```\n"
        "后置说明"
    )
    data = runner._extract_replan_proposal(text)
    assert data == {"reason": "范围太大", "suggested_split": ["a", "b"]}


def test_extract_replan_proposal_none_on_empty_shell():
    """格式对但没有任何已知字段的"空壳"提议，应视为无效。"""
    runner = _make_bare_runner()
    text = "```replan_proposal\n{\"unrelated_field\": 1}\n```"
    assert runner._extract_replan_proposal(text) is None


def test_extract_replan_proposal_none_on_malformed_json():
    runner = _make_bare_runner()
    text = "```replan_proposal\nnot valid json at all {{{\n```"
    assert runner._extract_replan_proposal(text) is None


def test_extract_replan_proposal_none_when_block_is_not_a_json_object():
    runner = _make_bare_runner()
    text = '```replan_proposal\n["just", "a", "list"]\n```'
    assert runner._extract_replan_proposal(text) is None


# ── GoalRunner._build_goal_aware_compact_hint ───────────────────────────

def test_build_goal_aware_compact_hint_disabled_by_default_returns_empty():
    runner = _make_bare_runner(
        _cfg=SimpleNamespace(compress=SimpleNamespace(goal_aware_weighting_enabled=False)),
        _criteria_status=[{"text": "标准1", "passed": False}],
    )
    assert runner._build_goal_aware_compact_hint() == ""


def test_build_goal_aware_compact_hint_missing_compress_cfg_returns_empty():
    """cfg 上根本没有 compress 属性时（getattr 兜底），不应报错。"""
    runner = _make_bare_runner(
        _cfg=SimpleNamespace(),
        _criteria_status=[{"text": "标准1", "passed": False}],
    )
    assert runner._build_goal_aware_compact_hint() == ""


def test_build_goal_aware_compact_hint_all_criteria_passed_returns_empty():
    runner = _make_bare_runner(
        _cfg=SimpleNamespace(compress=SimpleNamespace(goal_aware_weighting_enabled=True)),
        _criteria_status=[{"text": "标准1", "passed": True}],
    )
    assert runner._build_goal_aware_compact_hint() == ""


def test_build_goal_aware_compact_hint_enabled_with_unmet_criteria():
    runner = _make_bare_runner(
        _cfg=SimpleNamespace(compress=SimpleNamespace(goal_aware_weighting_enabled=True)),
        _criteria_status=[
            {"text": "标准1", "passed": True},
            {"text": "标准2：还没做", "passed": False},
        ],
    )
    hint = runner._build_goal_aware_compact_hint()
    assert "标准2：还没做" in hint
    assert "标准1" not in hint  # 已通过的标准不应出现在提示里
