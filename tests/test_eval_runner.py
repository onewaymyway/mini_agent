"""
tests/test_eval_runner.py — Stage 3.2 验证

对应 self_evolution_implementation_plan.md Stage 3.2：
  Phase D（eval 反馈环）核心引擎——场景加载（复用 test_cases/ 既有 .txt 格式）、
  单场景执行（统计 turns/tokens/tool 失败率）、with-skill vs without-skill 对比报告。

本文件只测试 evolution/eval_runner.py 的纯逻辑部分，使用 MagicMock 替代真实
LLM client（参考 tests/test_session.py 的既有模式），不发起任何网络请求。
CLI 层（cli/commands/eval_cmd.py）的参数解析与默认 Agent 工厂在
tests/test_eval_cli.py 中单独验证。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mini_agent.tools.builtin  # noqa: F401  注册内置工具（Agent 构造依赖）

from mini_agent.evolution.eval_runner import (
    EvalRunnerError,
    EvalScenario,
    load_scenarios,
    run_scenario,
    run_eval,
    AgentFactoryContext,
    ScenarioResult,
    ComparisonRow,
    EvalReport,
)
from mini_agent.llm.base import LLMResponse, LLMUsage


# ══════════════════════════════════════════════════════════════════════════════
# 场景加载 / 解析
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadScenarios:

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(EvalRunnerError):
            load_scenarios(tmp_path / "does_not_exist")

    def test_loads_single_turn_file(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello there", encoding="utf-8")
        scenarios = load_scenarios(tmp_path)
        assert len(scenarios) == 1
        assert scenarios[0].name == "a"
        assert scenarios[0].turns == ["hello there"]

    def test_splits_on_blank_lines(self, tmp_path):
        (tmp_path / "multi.txt").write_text(
            "first turn text\n\nsecond turn text\n\n\nthird turn text\n",
            encoding="utf-8",
        )
        scenarios = load_scenarios(tmp_path)
        assert scenarios[0].turns == ["first turn text", "second turn text", "third turn text"]

    def test_multiline_block_kept_as_one_turn(self, tmp_path):
        (tmp_path / "block.txt").write_text(
            "line one\nline two\nline three\n\nsecond turn\n",
            encoding="utf-8",
        )
        scenarios = load_scenarios(tmp_path)
        assert scenarios[0].turns[0] == "line one\nline two\nline three"
        assert scenarios[0].turns[1] == "second turn"

    def test_empty_file_skipped(self, tmp_path):
        (tmp_path / "empty.txt").write_text("   \n\n  \n", encoding="utf-8")
        (tmp_path / "real.txt").write_text("content", encoding="utf-8")
        scenarios = load_scenarios(tmp_path)
        assert [s.name for s in scenarios] == ["real"]

    def test_sorted_by_filename(self, tmp_path):
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        scenarios = load_scenarios(tmp_path)
        assert [s.name for s in scenarios] == ["a", "b"]

    def test_pattern_filters_extension(self, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "notes.md").write_text("should not load", encoding="utf-8")
        scenarios = load_scenarios(tmp_path)
        assert [s.name for s in scenarios] == ["a"]

    def test_inputs_subdir_ignored_by_default_pattern(self, tmp_path):
        (tmp_path / "inputs").mkdir()
        (tmp_path / "inputs" / "helper.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "real.txt").write_text("content", encoding="utf-8")
        scenarios = load_scenarios(tmp_path)
        assert [s.name for s in scenarios] == ["real"]

    def test_max_turns_skips_long_scenarios(self, tmp_path, capsys):
        (tmp_path / "short.txt").write_text("only one turn", encoding="utf-8")
        (tmp_path / "long.txt").write_text("\n\n".join(f"turn {i}" for i in range(20)), encoding="utf-8")
        scenarios = load_scenarios(tmp_path, max_turns=5)
        assert [s.name for s in scenarios] == ["short"]
        captured = capsys.readouterr()
        assert "long.txt" in captured.err
        assert "exceeds" in captured.err

    def test_max_turns_none_keeps_everything(self, tmp_path):
        (tmp_path / "long.txt").write_text("\n\n".join(f"turn {i}" for i in range(20)), encoding="utf-8")
        scenarios = load_scenarios(tmp_path, max_turns=None)
        assert len(scenarios) == 1
        assert len(scenarios[0].turns) == 20

    def test_real_test_cases_directory_loads_without_error(self):
        """回归保护：确保仓库自带的 test_cases/ 目录始终能被 load_scenarios 解析，
        不因为某个手写场景文件的格式变化而在生产环境抛异常。"""
        real_dir = PROJECT_ROOT / "test_cases"
        if not real_dir.is_dir():
            pytest.skip("test_cases/ not present in this checkout")
        scenarios = load_scenarios(real_dir, max_turns=None)
        assert len(scenarios) > 0
        assert all(s.turns for s in scenarios)


# ══════════════════════════════════════════════════════════════════════════════
# 单场景执行（run_scenario）
# ══════════════════════════════════════════════════════════════════════════════

def _make_agent(tmp_path, text="ok", tool_calls=None, side_effect=None):
    """构造一个真实 Agent，用 MagicMock 顶替 LLM client，避免网络调用。"""
    from mini_agent.agent import Agent
    from mini_agent.config import load_config
    from mini_agent.permissions import PermissionGuard

    cfg = load_config(project_root=tmp_path, sandbox=True, auto_approve=True, auto_save_session=False)
    cfg.stream = False
    # 收紧重试配置：单测里故意触发的异常不应该真的等待 15×5s 的生产级退避。
    cfg.retry.max_retries = 0
    cfg.retry.delay = 0.0

    mock_llm = MagicMock()
    if side_effect is not None:
        mock_llm.chat.side_effect = side_effect
    else:
        mock_llm.chat.return_value = LLMResponse(
            text=text, tool_calls=tool_calls or [], usage=LLMUsage(5, 10, 15), stop_reason="end_turn",
        )
    guard = PermissionGuard(auto_approve=True, sandbox=True, project_root=tmp_path)
    return Agent(cfg=cfg, guard=guard, llm_client=mock_llm)


class TestRunScenario:

    def test_single_turn_success(self, tmp_path):
        scenario = EvalScenario(name="s1", path=tmp_path / "s1.txt", turns=["hello"])
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        def factory(ctx, exclude_skill):
            return _make_agent(tmp_path, text="Hi!")

        result = run_scenario(factory, ctx, scenario, mode="baseline")
        assert result.ok is True
        assert result.turns == 1
        assert result.input_tokens == 5
        assert result.output_tokens == 10
        assert result.tool_calls == 0
        assert result.final_response == "Hi!"
        assert result.mode == "baseline"
        assert result.scenario == "s1"

    def test_multi_turn_accumulates_stats(self, tmp_path):
        scenario = EvalScenario(name="s2", path=tmp_path / "s2.txt", turns=["a", "b", "c"])
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        def factory(ctx, exclude_skill):
            return _make_agent(tmp_path, text="reply")

        result = run_scenario(factory, ctx, scenario, mode="baseline")
        assert result.ok is True
        assert result.turns == 3
        assert result.input_tokens == 15   # 5 tokens × 3 turns
        assert result.output_tokens == 30  # 10 tokens × 3 turns

    def test_exception_marks_not_ok_but_keeps_partial_stats(self, tmp_path):
        scenario = EvalScenario(name="s3", path=tmp_path / "s3.txt", turns=["a", "b"])
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return LLMResponse(text="ok", tool_calls=[], usage=LLMUsage(5, 10, 15), stop_reason="end_turn")
            raise RuntimeError("simulated LLM failure")

        def factory(ctx, exclude_skill):
            return _make_agent(tmp_path, side_effect=side_effect)

        result = run_scenario(factory, ctx, scenario, mode="baseline")
        assert result.ok is False
        assert "simulated LLM failure" in result.error
        # turns 计数在 run_turn() 内部于调用 LLM 之前就自增，所以第二轮抛异常时
        # turns 已经记为 2——这里验证的是"统计没有丢失"，而不是具体数值为 1。
        assert result.turns >= 1

    def test_tool_failure_rate_computed_from_stats(self, tmp_path):
        scenario = EvalScenario(name="s4", path=tmp_path / "s4.txt", turns=["use a tool"])
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        def factory(ctx, exclude_skill):
            agent = _make_agent(tmp_path, text="done")
            # 直接操纵 stats 模拟"工具调用过程中有失败"的场景，
            # 不依赖真实工具执行链路（那部分由 tool_executor 自己的测试覆盖）。
            agent.stats.record_tool_call("bash", False, 0)
            agent.stats.record_tool_call("bash", True, 100)
            agent.stats.record_tool_call("read_file", True, 50)
            agent.stats.tool_calls = 3
            return agent

        result = run_scenario(factory, ctx, scenario, mode="baseline")
        assert result.tool_calls == 3
        assert result.tool_failures == 1
        assert result.tool_failure_rate == pytest.approx(1 / 3)

    def test_zero_tool_calls_failure_rate_is_zero(self, tmp_path):
        scenario = EvalScenario(name="s5", path=tmp_path / "s5.txt", turns=["hi"])
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        def factory(ctx, exclude_skill):
            return _make_agent(tmp_path)

        result = run_scenario(factory, ctx, scenario, mode="baseline")
        assert result.tool_calls == 0
        assert result.tool_failure_rate == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# with-skill / without-skill 对比（run_eval）
# ══════════════════════════════════════════════════════════════════════════════

class TestRunEval:

    def test_baseline_mode_when_skill_is_none(self, tmp_path):
        scenarios = [EvalScenario(name="s1", path=tmp_path / "s1.txt", turns=["hi"])]
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        def factory(ctx, exclude_skill):
            assert exclude_skill is None  # baseline 模式不应该排除任何 skill
            return _make_agent(tmp_path)

        report = run_eval(factory, ctx, scenarios, skill=None)
        assert report.skill is None
        row = report.rows[0]
        assert row.with_skill.mode == "baseline"
        assert row.with_skill is row.without_skill  # 同一次运行结果

    def test_with_skill_excludes_nothing_without_skill_excludes_named_skill(self, tmp_path):
        scenarios = [EvalScenario(name="s1", path=tmp_path / "s1.txt", turns=["hi"])]
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        seen_excludes = []

        def factory(ctx, exclude_skill):
            seen_excludes.append(exclude_skill)
            return _make_agent(tmp_path)

        run_eval(factory, ctx, scenarios, skill="docx")
        assert seen_excludes == [None, "docx"]

    def test_run_eval_constructs_independent_agents_per_call(self, tmp_path):
        """同一场景的 with/without 两次运行必须是独立 Agent（独立 history/stats），
        不能共享状态——否则对比数据会被前一次运行污染。"""
        scenarios = [EvalScenario(name="s1", path=tmp_path / "s1.txt", turns=["hi", "again"])]
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        constructed_agents = []

        def factory(ctx, exclude_skill):
            agent = _make_agent(tmp_path)
            constructed_agents.append(agent)
            return agent

        run_eval(factory, ctx, scenarios, skill="x")
        assert len(constructed_agents) == 2
        assert constructed_agents[0] is not constructed_agents[1]
        assert constructed_agents[0].stats is not constructed_agents[1].stats

    def test_multiple_scenarios_each_get_own_row(self, tmp_path):
        scenarios = [
            EvalScenario(name="s1", path=tmp_path / "s1.txt", turns=["a"]),
            EvalScenario(name="s2", path=tmp_path / "s2.txt", turns=["b"]),
        ]
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        def factory(ctx, exclude_skill):
            return _make_agent(tmp_path)

        report = run_eval(factory, ctx, scenarios, skill="docx")
        assert [r.scenario for r in report.rows] == ["s1", "s2"]

    def test_report_scenario_dir_reflects_input(self, tmp_path):
        scenarios = [EvalScenario(name="s1", path=tmp_path / "scendir" / "s1.txt", turns=["a"])]
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        def factory(ctx, exclude_skill):
            return _make_agent(tmp_path)

        report = run_eval(factory, ctx, scenarios, skill=None)
        assert report.scenario_dir == str(tmp_path / "scendir")

    def test_empty_scenario_list_produces_empty_report(self, tmp_path):
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)

        def factory(ctx, exclude_skill):
            return _make_agent(tmp_path)

        report = run_eval(factory, ctx, [], skill="docx")
        assert report.rows == []
        assert report.scenario_dir == ""
        summary = report.summary()
        assert summary["with_skill"]["scenarios_total"] == 0
        assert summary["with_skill"]["tool_failure_rate"] == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 报告结构 / 序列化 / 落盘
# ══════════════════════════════════════════════════════════════════════════════

class TestEvalReportSerialization:

    def _make_result(self, mode, turns=1, in_tok=10, out_tok=20, calls=2, fails=1):
        return ScenarioResult(
            scenario="s1", mode=mode, ok=True, turns=turns,
            input_tokens=in_tok, output_tokens=out_tok,
            tool_calls=calls, tool_failures=fails,
            tool_failure_rate=(fails / calls if calls else 0.0),
        )

    def test_comparison_row_delta_computed_correctly(self):
        with_r = self._make_result("with_skill", turns=2, in_tok=20, out_tok=40, calls=4, fails=1)
        without_r = self._make_result("without_skill", turns=3, in_tok=30, out_tok=60, calls=4, fails=2)
        row = ComparisonRow(scenario="s1", with_skill=with_r, without_skill=without_r)
        d = row.to_dict()["delta"]
        assert d["turns"] == -1
        assert d["input_tokens"] == -10
        assert d["output_tokens"] == -20
        assert d["tool_failures"] == -1
        # with: 1/4=0.25, without: 2/4=0.5 → delta = -0.25
        assert d["tool_failure_rate"] == pytest.approx(-0.25)

    def test_report_to_dict_has_expected_top_level_keys(self):
        with_r = self._make_result("with_skill")
        without_r = self._make_result("without_skill")
        row = ComparisonRow(scenario="s1", with_skill=with_r, without_skill=without_r)
        report = EvalReport(skill="docx", scenario_dir="/tmp/x", generated_at="2026-06-20T00:00:00", rows=[row])
        d = report.to_dict()
        assert set(d.keys()) == {"skill", "scenario_dir", "generated_at", "scenarios", "summary"}
        assert d["skill"] == "docx"
        assert len(d["scenarios"]) == 1

    def test_summary_aggregates_across_rows(self):
        rows = [
            ComparisonRow(
                scenario=f"s{i}",
                with_skill=self._make_result("with_skill", calls=2, fails=1),
                without_skill=self._make_result("without_skill", calls=2, fails=0),
            )
            for i in range(3)
        ]
        report = EvalReport(skill="docx", scenario_dir="", generated_at="", rows=rows)
        summary = report.summary()
        assert summary["with_skill"]["total_tool_calls"] == 6
        assert summary["with_skill"]["total_tool_failures"] == 3
        assert summary["with_skill"]["tool_failure_rate"] == pytest.approx(0.5)
        assert summary["without_skill"]["total_tool_failures"] == 0
        assert summary["without_skill"]["tool_failure_rate"] == 0.0

    def test_write_creates_file_with_valid_json(self, tmp_path):
        import json
        with_r = self._make_result("with_skill")
        without_r = self._make_result("without_skill")
        row = ComparisonRow(scenario="s1", with_skill=with_r, without_skill=without_r)
        report = EvalReport(skill="docx", scenario_dir="/tmp/x", generated_at="2026-06-20T00:00:00", rows=[row])

        out_path = tmp_path / "nested" / "eval_result.json"
        written = report.write(out_path)
        assert written == out_path
        assert out_path.exists()
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert loaded["skill"] == "docx"

    def test_write_creates_parent_dirs(self, tmp_path):
        with_r = self._make_result("with_skill")
        report = EvalReport(skill=None, scenario_dir="", generated_at="",
                             rows=[ComparisonRow(scenario="s1", with_skill=with_r, without_skill=with_r)])
        out_path = tmp_path / "a" / "b" / "c" / "result.json"
        report.write(out_path)
        assert out_path.exists()
