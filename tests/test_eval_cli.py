"""
tests/test_eval_cli.py — Stage 3.2 验证（CLI 层）

对应 self_evolution_implementation_plan.md Stage 3.2：
  `mini-agent eval --scenario <dir> --skill <name>` 子命令。

本文件只测试 cli/commands/eval_cmd.py 的参数解析、错误处理、报告打印逻辑，
通过 monkeypatch 替换掉真正会发起 LLM 调用的 _make_default_agent_factory，
确保单测不依赖网络/API key。Agent 工厂本身在 test_eval_runner.py 里
通过另一条路径（直接构造 Agent + MagicMock LLM client）验证。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.cli.commands.eval_cmd import build_eval_parser, run_eval_cli
from mini_agent.evolution.eval_runner import (
    EvalScenario, EvalReport, ComparisonRow, ScenarioResult, AgentFactoryContext,
)


# ══════════════════════════════════════════════════════════════════════════════
# 参数解析
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildEvalParser:

    def test_scenario_is_required(self):
        parser = build_eval_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_minimal_args(self):
        parser = build_eval_parser()
        args = parser.parse_args(["--scenario", "test_cases/"])
        assert args.scenario == "test_cases/"
        assert args.skill is None
        assert args.pattern == "*.txt"
        assert args.max_scenario_turns == 10
        assert args.no_sandbox is False
        assert args.quiet is False

    def test_full_args(self):
        parser = build_eval_parser()
        args = parser.parse_args([
            "--scenario", "test_cases/",
            "--skill", "docx",
            "--pattern", "*.scenario",
            "--project", "/tmp/proj",
            "--skills-dir", "/tmp/proj/skills",
            "--output", "/tmp/out.json",
            "--no-sandbox",
            "--max-turns", "5",
            "--max-scenario-turns", "20",
            "--quiet",
        ])
        assert args.skill == "docx"
        assert args.pattern == "*.scenario"
        assert args.project == "/tmp/proj"
        assert args.skills_dir == "/tmp/proj/skills"
        assert args.output == "/tmp/out.json"
        assert args.no_sandbox is True
        assert args.max_turns == 5
        assert args.max_scenario_turns == 20
        assert args.quiet is True

    def test_max_scenario_turns_zero_means_disable(self):
        parser = build_eval_parser()
        args = parser.parse_args(["--scenario", "x", "--max-scenario-turns", "0"])
        assert args.max_scenario_turns == 0


# ══════════════════════════════════════════════════════════════════════════════
# run_eval_cli — 错误路径
# ══════════════════════════════════════════════════════════════════════════════

class TestRunEvalCliErrorPaths:

    def test_missing_scenario_dir_returns_nonzero(self, tmp_path, capsys):
        rc = run_eval_cli(["--scenario", str(tmp_path / "nope")])
        assert rc != 0
        assert "ERROR" in capsys.readouterr().err

    def test_empty_scenario_dir_returns_nonzero(self, tmp_path, capsys):
        rc = run_eval_cli(["--scenario", str(tmp_path)])
        assert rc != 0
        assert "No scenario files" in capsys.readouterr().err

    def test_all_scenarios_filtered_by_max_turns_returns_nonzero(self, tmp_path, capsys):
        (tmp_path / "long.txt").write_text("\n\n".join(f"t{i}" for i in range(20)), encoding="utf-8")
        rc = run_eval_cli(["--scenario", str(tmp_path), "--max-scenario-turns", "5"])
        assert rc != 0


# ══════════════════════════════════════════════════════════════════════════════
# run_eval_cli — 正常路径（monkeypatch 掉真实 Agent 工厂）
# ══════════════════════════════════════════════════════════════════════════════

def _fake_report(skill, scenario_dir):
    with_r = ScenarioResult(
        scenario="s1", mode="with_skill" if skill else "baseline", ok=True,
        turns=2, input_tokens=10, output_tokens=20, tool_calls=4, tool_failures=1,
        tool_failure_rate=0.25,
    )
    without_r = with_r if skill is None else ScenarioResult(
        scenario="s1", mode="without_skill", ok=True,
        turns=3, input_tokens=15, output_tokens=30, tool_calls=4, tool_failures=2,
        tool_failure_rate=0.5,
    )
    row = ComparisonRow(scenario="s1", with_skill=with_r, without_skill=without_r)
    return EvalReport(skill=skill, scenario_dir=scenario_dir, generated_at="2026-06-20T00:00:00", rows=[row])


@pytest.fixture
def scenario_dir(tmp_path):
    d = tmp_path / "scenarios"
    d.mkdir()
    (d / "s1.txt").write_text("hello world", encoding="utf-8")
    return d


class TestRunEvalCliHappyPath:

    def test_writes_default_output_path(self, scenario_dir, tmp_path, monkeypatch, capsys):
        captured = {}

        def fake_run_eval(factory, ctx, scenarios, skill=None):
            captured["skill"] = skill
            captured["n_scenarios"] = len(scenarios)
            return _fake_report(skill, str(scenario_dir))

        monkeypatch.setattr("mini_agent.evolution.eval_runner.run_eval", fake_run_eval)
        rc = run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path)])

        assert rc == 0
        assert captured["skill"] is None
        assert captured["n_scenarios"] == 1
        out_path = tmp_path / ".agent" / "eval_result.json"
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["skill"] is None

    def test_custom_output_path(self, scenario_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "mini_agent.evolution.eval_runner.run_eval",
            lambda factory, ctx, scenarios, skill=None: _fake_report(skill, str(scenario_dir)),
        )
        out_path = tmp_path / "custom" / "report.json"
        rc = run_eval_cli([
            "--scenario", str(scenario_dir),
            "--project", str(tmp_path),
            "--output", str(out_path),
        ])
        assert rc == 0
        assert out_path.exists()

    def test_skill_flag_passed_through(self, scenario_dir, tmp_path, monkeypatch):
        captured = {}

        def fake_run_eval(factory, ctx, scenarios, skill=None):
            captured["skill"] = skill
            return _fake_report(skill, str(scenario_dir))

        monkeypatch.setattr("mini_agent.evolution.eval_runner.run_eval", fake_run_eval)
        rc = run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path), "--skill", "docx"])
        assert rc == 0
        assert captured["skill"] == "docx"

    def test_quiet_suppresses_progress_line(self, scenario_dir, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "mini_agent.evolution.eval_runner.run_eval",
            lambda factory, ctx, scenarios, skill=None: _fake_report(skill, str(scenario_dir)),
        )
        run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path), "--quiet"])
        out = capsys.readouterr().out
        assert "scenario(s) from" not in out

    def test_non_quiet_prints_progress_line(self, scenario_dir, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "mini_agent.evolution.eval_runner.run_eval",
            lambda factory, ctx, scenarios, skill=None: _fake_report(skill, str(scenario_dir)),
        )
        run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path)])
        out = capsys.readouterr().out
        assert "scenario(s) from" in out

    def test_summary_printed_with_skill_comparison(self, scenario_dir, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "mini_agent.evolution.eval_runner.run_eval",
            lambda factory, ctx, scenarios, skill=None: _fake_report(skill, str(scenario_dir)),
        )
        run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path), "--skill", "docx"])
        out = capsys.readouterr().out
        assert "Skill: docx" in out
        assert "tool_failure_rate" in out

    def test_summary_printed_baseline_mode(self, scenario_dir, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "mini_agent.evolution.eval_runner.run_eval",
            lambda factory, ctx, scenarios, skill=None: _fake_report(skill, str(scenario_dir)),
        )
        run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path)])
        out = capsys.readouterr().out
        assert "Scenarios:" in out
        assert "Skill:" not in out

    def test_skills_dir_defaults_to_project_skills(self, scenario_dir, tmp_path, monkeypatch):
        """--skills-dir 未传时应默认探测 <project>/skills，若不存在则 ctx.skills_dir 为 None。"""
        captured_ctx = {}

        def fake_run_eval(factory, ctx, scenarios, skill=None):
            captured_ctx["ctx"] = ctx
            return _fake_report(skill, str(scenario_dir))

        monkeypatch.setattr("mini_agent.evolution.eval_runner.run_eval", fake_run_eval)
        run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path)])
        assert captured_ctx["ctx"].skills_dir is None  # tmp_path/skills 不存在

    def test_skills_dir_detected_when_present(self, scenario_dir, tmp_path, monkeypatch):
        (tmp_path / "skills").mkdir()
        captured_ctx = {}

        def fake_run_eval(factory, ctx, scenarios, skill=None):
            captured_ctx["ctx"] = ctx
            return _fake_report(skill, str(scenario_dir))

        monkeypatch.setattr("mini_agent.evolution.eval_runner.run_eval", fake_run_eval)
        run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path)])
        assert captured_ctx["ctx"].skills_dir == tmp_path / "skills"

    def test_sandbox_on_by_default(self, scenario_dir, tmp_path, monkeypatch):
        captured_ctx = {}

        def fake_run_eval(factory, ctx, scenarios, skill=None):
            captured_ctx["ctx"] = ctx
            return _fake_report(skill, str(scenario_dir))

        monkeypatch.setattr("mini_agent.evolution.eval_runner.run_eval", fake_run_eval)
        run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path)])
        assert captured_ctx["ctx"].sandbox is True

    def test_no_sandbox_flag_disables_sandbox(self, scenario_dir, tmp_path, monkeypatch):
        captured_ctx = {}

        def fake_run_eval(factory, ctx, scenarios, skill=None):
            captured_ctx["ctx"] = ctx
            return _fake_report(skill, str(scenario_dir))

        monkeypatch.setattr("mini_agent.evolution.eval_runner.run_eval", fake_run_eval)
        run_eval_cli(["--scenario", str(scenario_dir), "--project", str(tmp_path), "--no-sandbox"])
        assert captured_ctx["ctx"].sandbox is False


# ══════════════════════════════════════════════════════════════════════════════
# 默认 Agent 工厂（构造逻辑本身，不实际跑 LLM）
# ══════════════════════════════════════════════════════════════════════════════

class TestDefaultAgentFactory:
    """
    这里只验证 Agent 的构造结果（cfg/skill_loader 字段），不调用 run_turn()，
    所以不会真的发起任何 LLM 请求——但 Agent 构造本身会走
    create_client().validate_config()，要求存在一个 api_key（哪怕是假的），
    否则在没配置真实环境变量的 CI/沙箱里直接报 LLMConfigError。
    """

    @pytest.fixture(autouse=True)
    def _fake_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key-for-construction-only")

    def test_factory_excludes_skill_when_requested(self, tmp_path):
        from mini_agent.cli.commands.eval_cmd import _make_default_agent_factory

        skills_dir = tmp_path / "skills"
        demo_dir = skills_dir / "demo"
        demo_dir.mkdir(parents=True)
        (demo_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: demo skill\n---\ncontent\n", encoding="utf-8",
        )

        factory = _make_default_agent_factory()
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=skills_dir)

        agent_with = factory(ctx, None)
        assert agent_with.skill_loader is not None
        assert "demo" in agent_with.skill_loader.available

        agent_without = factory(ctx, "demo")
        assert "demo" not in agent_without.skill_loader.available

    def test_factory_disables_streaming(self, tmp_path):
        from mini_agent.cli.commands.eval_cmd import _make_default_agent_factory

        factory = _make_default_agent_factory()
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)
        agent = factory(ctx, None)
        assert agent.cfg.stream is False

    def test_factory_applies_max_turns_override(self, tmp_path):
        from mini_agent.cli.commands.eval_cmd import _make_default_agent_factory

        factory = _make_default_agent_factory(max_turns=3)
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)
        agent = factory(ctx, None)
        assert agent.cfg.max_turns == 3

    def test_factory_tightens_retry_policy(self, tmp_path):
        """eval 跑批不应继承生产环境 15 次重试的耐心策略，否则单个失败场景会
        把整批 eval 拖慢到不可接受的程度。"""
        from mini_agent.cli.commands.eval_cmd import _make_default_agent_factory

        factory = _make_default_agent_factory()
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)
        agent = factory(ctx, None)
        assert agent.cfg.retry.max_retries <= 2
        assert agent.cfg.retry.delay <= 2.0

    def test_factory_creates_fresh_agent_each_call(self, tmp_path):
        from mini_agent.cli.commands.eval_cmd import _make_default_agent_factory

        factory = _make_default_agent_factory()
        ctx = AgentFactoryContext(project_root=tmp_path, skills_dir=None)
        a1 = factory(ctx, None)
        a2 = factory(ctx, None)
        assert a1 is not a2
        assert a1.stats is not a2.stats
