"""
cli/commands/eval_cmd.py — `mini-agent eval` 子命令（Stage 3.2）

对应 self_evolution_implementation_plan.md Stage 3.2：

    CLI 新增子命令 eval --scenario <dir> --with-skill/--without-skill
    实现：跑 test_cases/ 下场景，对比开关某个 skill 前后的 tool 失败率/turns/token
    消耗，输出 JSON 报告

用法：
    mini-agent eval --scenario test_cases/ --skill docx
    mini-agent eval --scenario test_cases/ --skill docx --output /tmp/report.json
    mini-agent eval --scenario test_cases/                      # 不传 --skill，只验证场景能否跑通

与 EvolutionWorkspace（Stage 2.3）组合使用（设计文档 4.5 节"副本化运行天然产出
eval 数据"）：EvolutionWorkspace 负责创建隔离的 git worktree，本命令负责在该
worktree 内跑场景对比——两者通过路径参数自然组合，不需要相互 import：

    ws = EvolutionWorkspace(repo, branch="evolve/add-docx-skill")
    ws.create()
    # mini-agent eval --scenario test_cases/ --skill docx \
    #     --project <ws.path> --output <ws.eval_result_path()>

不是 build_parser()（cli/parser.py）里 argparse 的普通参数，而是独立的子命令路径——
`mini-agent` 的位置参数 `prompt` 与子命令模式冲突（argparse 不支持"位置参数 +
互斥子命令"既要又要），所以在 cli/app.py 的 main() 入口最前面，检测
`sys.argv[1] == "eval"` 时整体短路到本模块，不进入主 build_parser() 流程，
这是与现有单命令体系共存成本最低的接入方式。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional


def build_eval_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mini-agent eval",
        description="Run test_cases/ scenarios and compare tool-failure-rate/turns/token "
                     "cost with a skill enabled vs excluded.",
    )
    p.add_argument("--scenario", required=True, metavar="DIR",
                    help="Scenario directory containing *.txt files "
                         "(each file = one scenario; blank line = next turn). "
                         "Typically test_cases/.")
    p.add_argument("--skill", default=None, metavar="NAME",
                    help="Skill name to compare with-skill vs without-skill. "
                         "If omitted, runs once per scenario without comparison "
                         "(useful as a smoke pass over the whole scenario set).")
    p.add_argument("--pattern", default="*.txt", metavar="GLOB",
                    help="Glob pattern for scenario files inside --scenario (default: *.txt)")
    p.add_argument("--max-scenario-turns", type=int, default=10, metavar="N",
                    help="Skip scenario files with more than N turns (blank-line-separated "
                         "blocks), with a warning (default: 10). Some hand-written test_cases/ "
                         "files use blank lines purely for prose formatting, not real "
                         "multi-turn dialogue — this guards against accidentally running "
                         "dozens of real LLM calls for one scenario. Pass 0 to disable.")
    p.add_argument("--project", default=None, metavar="DIR",
                    help="Project root the Agent runs against (default: current directory)")
    p.add_argument("--skills-dir", default=None, metavar="DIR",
                    help="Skills directory to load (default: <project>/skills if present)")
    p.add_argument("--output", default=None, metavar="FILE",
                    help="Where to write the JSON report "
                         "(default: <project>/.agent/eval_result.json)")
    p.add_argument("--no-sandbox", action="store_true",
                    help="Disable sandbox mode (NOT recommended — eval runs untrusted-ish "
                         "scenario prompts; sandbox is on by default for safety)")
    p.add_argument("--max-turns", type=int, default=None,
                    help="Override max agentic turns per scenario step (default: AppConfig default)")
    p.add_argument("--quiet", action="store_true", help="Suppress per-scenario progress output")
    return p


def run_eval_cli(argv: list[str]) -> int:
    """
    `mini-agent eval ...` 的完整入口。argv 不含开头的 'eval' 自身
    （cli/app.py 调用时已经切片掉），返回进程退出码。
    """
    parser = build_eval_parser()
    args = parser.parse_args(argv)

    scenario_dir = Path(args.scenario).expanduser()
    project_root = Path(args.project).expanduser() if args.project else Path.cwd()

    from mini_agent.evolution.eval_runner import (
        EvalRunnerError, load_scenarios, run_eval, AgentFactoryContext,
    )

    max_scenario_turns = args.max_scenario_turns if args.max_scenario_turns > 0 else None
    try:
        scenarios = load_scenarios(scenario_dir, pattern=args.pattern, max_turns=max_scenario_turns)
    except EvalRunnerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if not scenarios:
        print(f"No scenario files matched {args.pattern!r} under {scenario_dir}", file=sys.stderr)
        return 1

    skills_dir = Path(args.skills_dir).expanduser() if args.skills_dir else (project_root / "skills")

    ctx = AgentFactoryContext(
        project_root=project_root,
        skills_dir=skills_dir if skills_dir.is_dir() else None,
        sandbox=not args.no_sandbox,
        auto_approve=True,
    )

    if not args.quiet:
        mode_desc = f"comparing skill={args.skill!r}" if args.skill else "no skill comparison (baseline pass)"
        print(f"[eval] {len(scenarios)} scenario(s) from {scenario_dir} — {mode_desc}")

    factory = _make_default_agent_factory(max_turns=args.max_turns)

    report = run_eval(factory, ctx, scenarios, skill=args.skill)

    output_path = (
        Path(args.output).expanduser() if args.output
        else project_root / ".agent" / "eval_result.json"
    )
    report.write(output_path)

    _print_summary(report, output_path)
    return 0


def _print_summary(report, output_path: Path) -> None:
    summary = report.summary()
    print(f"\n[eval] report written to {output_path}")
    if report.skill:
        w, wo = summary["with_skill"], summary["without_skill"]
        print(f"\nSkill: {report.skill}")
        print(f"{'metric':<22}{'with':>12}{'without':>12}{'delta':>12}")
        for key, label in (
            ("total_turns", "turns"),
            ("total_input_tokens", "input_tokens"),
            ("total_output_tokens", "output_tokens"),
            ("total_tool_calls", "tool_calls"),
            ("total_tool_failures", "tool_failures"),
        ):
            delta = w[key] - wo[key]
            print(f"{label:<22}{w[key]:>12}{wo[key]:>12}{delta:>+12}")
        print(f"{'tool_failure_rate':<22}{w['tool_failure_rate']:>12.2%}"
              f"{wo['tool_failure_rate']:>12.2%}"
              f"{(w['tool_failure_rate'] - wo['tool_failure_rate']):>+12.2%}")
    else:
        s = summary["with_skill"]  # baseline 模式下 with == without
        print(f"\nScenarios: {s['scenarios_ok']}/{s['scenarios_total']} ok | "
              f"turns={s['total_turns']} | "
              f"tokens in/out={s['total_input_tokens']}/{s['total_output_tokens']} | "
              f"tool_calls={s['total_tool_calls']} | "
              f"tool_failure_rate={s['tool_failure_rate']:.2%}")


# ── 默认 Agent 工厂（真实 LLM 调用） ────────────────────────────────────────────


def _make_default_agent_factory(max_turns: Optional[int] = None):
    """
    返回一个 (ctx, exclude_skill) -> Agent 的工厂函数，构造一个真实可用的 Agent
    （真实 LLM client，从环境变量 / providers.json 取 API key——与 `mini-agent`
    主入口走同一条 load_config() 路径，不重新发明配置加载逻辑）。

    每次调用都构造一个全新的 Agent（独立 history、独立 stats），保证场景之间
    互不污染——这是 eval 对比的基本要求：同一个场景在 with/without 两种模式下
    跑的是两个完全独立的 session。
    """
    def factory(ctx, exclude_skill: Optional[str]):
        from mini_agent.config import load_config
        from mini_agent.permissions import PermissionGuard
        from mini_agent.skills import SkillLoader
        from mini_agent.agent import Agent

        cfg = load_config(
            project_root=ctx.project_root,
            sandbox=ctx.sandbox,
            auto_approve=ctx.auto_approve,
            auto_save_session=False,   # eval 跑的是临时评测会话，不污染 sessions/ 目录
        )
        cfg.stream = False             # eval 只关心最终统计数据，不需要终端流式渲染
        # eval 跑批场景下，生产环境默认的"15 次重试 + 固定 5s 退避"会让单个真实
        # 失败的场景拖慢整批运行（15 × 5s ≈ 75s 起步，叠加多个场景会非常慢）。
        # eval 的目标是尽快暴露问题、看清对比数据，不是像生产会话一样耐心应对
        # 网络抖动，所以这里收紧到 1 次重试、1 秒退避——网络抖动仍能恢复一次，
        # 真实失败也能快速失败进入下一个场景/模式。
        cfg.retry.max_retries = 1
        cfg.retry.delay = 1.0
        if max_turns is not None:
            cfg.max_turns = max_turns

        skill_dirs = [ctx.skills_dir] if ctx.skills_dir else []
        skill_loader = SkillLoader(skill_dirs) if skill_dirs else None
        if skill_loader is not None and exclude_skill:
            skill_loader.exclude(exclude_skill)

        guard = PermissionGuard(
            auto_approve=ctx.auto_approve,
            sandbox=ctx.sandbox,
            project_root=ctx.project_root,
        )
        return Agent(cfg=cfg, skill_loader=skill_loader, guard=guard)

    return factory


__all__ = ["build_eval_parser", "run_eval_cli"]
