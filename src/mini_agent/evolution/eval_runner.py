"""
evolution/eval_runner.py — Phase D：eval 反馈环（Stage 3.2）

对应 self_evolution_implementation_plan.md Stage 3.2 / 设计文档第 5 节：

    Phase D（eval 反馈环）：复用 test_cases/ 作为回归集，提供
    mini-agent eval --scenario ... --with-skill/--without-skill 对比命令；
    4.5 节的副本化运行天然产出 eval 数据。

以及设计文档 4.6 节验证流水线表格中明确写出的对比指标：
    "T1: schema/加载校验 + eval 场景对比（tool 失败率 / turns / token）"

本模块职责：
  1. 场景加载（load_scenarios）：从一个目录读取场景文件，复用现有 test_cases/
     的既成格式——纯文本 .txt 文件，多轮对话之间用一个空行分隔（核对
     test_cases/tool_test.txt 等现有文件确认的格式），不要求改造现有测试用例。
  2. 单场景执行（run_scenario）：构造一个真实 Agent（真实 LLM 调用，eval 的
     意义就是衡量真实效果），按场景里的每一轮依次调用 run_turn()，
     收集 SessionStats（tool_stats / turns / token）。
  3. with/without-skill 对比（run_eval）：同一批场景分别跑两遍——
     一遍显式排除某个 skill（SkillLoader.exclude），一遍正常允许其参与
     （包括被关键词自动激活）——产出结构化对比报告。

刻意不做的事情（与 Stage 2.3 EvolutionWorkspace 的取舍一致，避免本阶段战线过长）：
  - 不在本模块内创建 git worktree 隔离；调用方（CLI 或未来的 evolution-agent）
    若需要在隔离环境里跑 eval，直接复用 EvolutionWorkspace + 本模块组合即可——
    EvolutionWorkspace.write_eval_result() 已经预留了落盘位置。
  - 不做"多次试验取统计分布"（那是设计文档 7.10 节 Experiment 机制的要求，
    属于 Phase H 范畴），每个场景每种模式默认只跑一次。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class EvalRunnerError(Exception):
    """eval 场景加载/执行过程中的不可恢复错误（场景目录不存在、Agent 构造失败等）。"""


# ── 场景数据结构 ────────────────────────────────────────────────────────────────


@dataclass
class EvalScenario:
    """
    一个 eval 场景：可能含多轮用户输入（顺序依次喂给同一个 Agent session）。

    文件格式（与现有 test_cases/*.txt 完全一致，不要求改造既有文件）：
        第一轮 prompt 文本，可以多行

        第二轮 prompt 文本
        （第一行空行分隔出下一轮）

        第三轮……

    即：文件内容按"至少一个空行"切分为多个非空块，每块剥离首尾空白后即为一轮输入。
    单轮场景（文件里没有空行）退化为只有一个元素的 turns 列表。
    """
    name: str
    path: Path
    turns: list[str] = field(default_factory=list)


def load_scenarios(
    scenario_dir: Path,
    pattern: str = "*.txt",
    max_turns: Optional[int] = None,
) -> list[EvalScenario]:
    """
    从 scenario_dir 加载所有匹配 pattern 的场景文件，按文件名排序保证结果稳定。

    设计文档原话"复用 test_cases/ 作为回归集"——默认 pattern 只匹配 .txt，
    跳过 test_cases/ 下已经存在的 .md（人工测试手册，非机器可读格式）和
    inputs/（辅助资源目录，不是场景本身）。调用方可传别的 pattern 覆盖。

    max_turns 非 None 时，跳过轮次数超过该值的场景文件并打印警告（不报错）——
    test_cases/ 下个别文件（如多分镜漫画脚本）用空行分隔了几十个段落，
    这些文件本意是"一份详细的长 prompt"而非"几十轮真实对话"，逐轮喂给
    Agent 会产生几十次真实 LLM 调用，意外地把一次轻量 eval 跑成长耗时/高费用
    任务。调用方（CLI 默认值见 cli/commands/eval_cmd.py）应设置一个合理上限，
    把这类文件当作"需要显式确认才跑"的场景，而不是默默跳过或默默全跑。
    """
    d = Path(scenario_dir)
    if not d.is_dir():
        raise EvalRunnerError(f"scenario directory does not exist: {d}")

    scenarios: list[EvalScenario] = []
    for fp in sorted(d.glob(pattern)):
        if not fp.is_file():
            continue
        text = fp.read_text(encoding="utf-8")
        turns = _split_turns(text)
        if not turns:
            continue  # 空文件直接跳过，不构成一个有效场景
        if max_turns is not None and len(turns) > max_turns:
            import sys as _sys
            print(
                f"[eval] skipping {fp.name}: {len(turns)} turns exceeds --max-scenario-turns={max_turns} "
                f"(pass a higher value or use --pattern to target this file alone if intentional)",
                file=_sys.stderr,
            )
            continue
        scenarios.append(EvalScenario(name=fp.stem, path=fp, turns=turns))
    return scenarios


def _split_turns(text: str) -> list[str]:
    """按连续空行切分文本为多轮输入，每轮剥离首尾空白；跳过切分后变为空的块。"""
    raw_blocks = _re_split_blank_lines(text)
    return [b.strip() for b in raw_blocks if b.strip()]


def _re_split_blank_lines(text: str) -> list[str]:
    import re as _re
    return _re.split(r"\n\s*\n+", text)


# ── 单场景执行结果 ──────────────────────────────────────────────────────────────


@dataclass
class ScenarioResult:
    """单个场景在某一种模式（with/without skill）下的执行结果。"""
    scenario: str
    mode: str                      # "with_skill" | "without_skill" | "baseline"
    ok: bool                       # 是否完整跑完全部轮次（未抛异常）
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    tool_failure_rate: float = 0.0     # tool_failures / max(tool_calls, 1)
    duration_seconds: float = 0.0
    error: str = ""
    final_response: str = ""           # 最后一轮的助手回复，便于人工抽查

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "ok": self.ok,
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "tool_failure_rate": round(self.tool_failure_rate, 4),
            "duration_seconds": round(self.duration_seconds, 3),
            "error": self.error,
        }


def _stats_to_result(scenario_name: str, mode: str, stats, duration: float,
                      ok: bool = True, error: str = "", final_response: str = "") -> ScenarioResult:
    tool_calls = int(getattr(stats, "tool_calls", 0) or 0)
    tool_failures = sum(
        int(v.get("fail", 0)) for v in (getattr(stats, "tool_stats", {}) or {}).values()
    )
    rate = (tool_failures / tool_calls) if tool_calls > 0 else 0.0
    return ScenarioResult(
        scenario=scenario_name,
        mode=mode,
        ok=ok,
        turns=int(getattr(stats, "turns", 0) or 0),
        input_tokens=int(getattr(stats, "input_tokens", 0) or 0),
        output_tokens=int(getattr(stats, "output_tokens", 0) or 0),
        tool_calls=tool_calls,
        tool_failures=tool_failures,
        tool_failure_rate=rate,
        duration_seconds=duration,
        error=error,
        final_response=final_response,
    )


# ── Agent 工厂（调用方注入，便于测试用假 LLM client 替换） ──────────────────────


@dataclass
class AgentFactoryContext:
    """
    构造一次 eval 运行所需的全部上下文，传给调用方提供的 agent_factory。

    之所以不在本模块内部直接 import/构造 Agent/AppConfig/SkillLoader/PermissionGuard，
    是为了让测试可以注入假 LLM client（参考 tests/test_session.py 的既有模式），
    避免 eval_runner 单测真的发起网络请求。CLI 路径下提供一个真实的默认工厂
    （见 cli/commands/eval_cmd.py 的 _default_agent_factory）。
    """
    project_root: Path
    skills_dir: Optional[Path]
    sandbox: bool = True
    auto_approve: bool = True


# agent_factory 签名：(ctx, exclude_skill: Optional[str]) -> Agent
AgentFactory = "Callable[[AgentFactoryContext, Optional[str]], object]"


def run_scenario(
    agent_factory,
    ctx: AgentFactoryContext,
    scenario: EvalScenario,
    mode: str,
    exclude_skill: Optional[str] = None,
) -> ScenarioResult:
    """
    跑一个场景的全部轮次，返回该次运行的统计结果。

    exclude_skill 非 None 时，agent_factory 应当在构造 SkillLoader 后调用
    skill_loader.exclude(exclude_skill)（CLI 默认工厂已实现这一步），
    保证该 skill 在本次运行中完全不参与——这是 --without-skill 模式的核心。

    单个场景内任意一轮抛异常，立即终止该场景并标记 ok=False，但仍返回
    截至失败前已经累积的统计（部分结果比完全没有结果更有诊断价值）。
    """
    start = time.time()
    agent = agent_factory(ctx, exclude_skill)
    final_response = ""
    try:
        for turn_text in scenario.turns:
            final_response = agent.run_turn(turn_text)
        duration = time.time() - start
        return _stats_to_result(
            scenario.name, mode, agent.stats, duration,
            ok=True, final_response=final_response,
        )
    except Exception as e:
        duration = time.time() - start
        return _stats_to_result(
            scenario.name, mode, agent.stats, duration,
            ok=False, error=f"{type(e).__name__}: {e}", final_response=final_response,
        )


# ── 对比报告 ────────────────────────────────────────────────────────────────────


@dataclass
class ComparisonRow:
    """单个场景的 with vs without 对比（差值 = with - without，负数表示 with 更优）。"""
    scenario: str
    with_skill: ScenarioResult
    without_skill: ScenarioResult

    def to_dict(self) -> dict:
        w, wo = self.with_skill, self.without_skill
        return {
            "scenario": self.scenario,
            "with_skill": w.to_dict(),
            "without_skill": wo.to_dict(),
            "delta": {
                "turns": w.turns - wo.turns,
                "input_tokens": w.input_tokens - wo.input_tokens,
                "output_tokens": w.output_tokens - wo.output_tokens,
                "tool_calls": w.tool_calls - wo.tool_calls,
                "tool_failures": w.tool_failures - wo.tool_failures,
                "tool_failure_rate": round(w.tool_failure_rate - wo.tool_failure_rate, 4),
            },
        }


@dataclass
class EvalReport:
    """一次完整 eval 运行（含全部场景）的报告，结构对应设计文档 4.5 节 eval_result.json。"""
    skill: Optional[str]
    scenario_dir: str
    generated_at: str
    rows: list[ComparisonRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "scenario_dir": self.scenario_dir,
            "generated_at": self.generated_at,
            "scenarios": [r.to_dict() for r in self.rows],
            "summary": self.summary(),
        }

    def summary(self) -> dict:
        """跨全部场景的汇总：总 token / 总 tool 失败次数 / 失败率，with vs without 各一份。"""
        def _agg(results: list[ScenarioResult]) -> dict:
            total_calls = sum(r.tool_calls for r in results)
            total_fail = sum(r.tool_failures for r in results)
            return {
                "scenarios_ok": sum(1 for r in results if r.ok),
                "scenarios_total": len(results),
                "total_turns": sum(r.turns for r in results),
                "total_input_tokens": sum(r.input_tokens for r in results),
                "total_output_tokens": sum(r.output_tokens for r in results),
                "total_tool_calls": total_calls,
                "total_tool_failures": total_fail,
                "tool_failure_rate": round((total_fail / total_calls) if total_calls else 0.0, 4),
            }
        return {
            "with_skill": _agg([row.with_skill for row in self.rows]),
            "without_skill": _agg([row.without_skill for row in self.rows]),
        }

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def run_eval(
    agent_factory,
    ctx: AgentFactoryContext,
    scenarios: list[EvalScenario],
    skill: Optional[str] = None,
) -> EvalReport:
    """
    对每个场景分别跑 with-skill / without-skill 两次，汇总为一份对比报告。

    skill=None 时退化为"跑一遍 baseline"（with_skill 与 without_skill 使用同一组
    结果，delta 全为 0）——用于"暂不关心某个 skill、只想看场景本身能否跑通"的场景，
    例如 CLI 不传 --skill 时的默认行为。
    """
    rows: list[ComparisonRow] = []
    for sc in scenarios:
        mode = "baseline" if skill is None else "with_skill"
        with_result = run_scenario(agent_factory, ctx, sc, mode=mode, exclude_skill=None)
        if skill is None:
            without_result = with_result
        else:
            without_result = run_scenario(
                agent_factory, ctx, sc, mode="without_skill", exclude_skill=skill,
            )
        rows.append(ComparisonRow(scenario=sc.name, with_skill=with_result, without_skill=without_result))

    return EvalReport(
        skill=skill,
        scenario_dir=str(scenarios[0].path.parent) if scenarios else "",
        generated_at=_now_iso(),
        rows=rows,
    )


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


__all__ = [
    "EvalRunnerError",
    "EvalScenario",
    "load_scenarios",
    "ScenarioResult",
    "AgentFactoryContext",
    "run_scenario",
    "ComparisonRow",
    "EvalReport",
    "run_eval",
]
