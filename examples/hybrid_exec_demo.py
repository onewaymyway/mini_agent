"""
examples/hybrid_exec_demo.py — hybrid_exec 系统端到端可运行演示

用于验证 docs/hybrid-exec-guide.md 描述的整套决策链路在真实环境下确实
可用：真实子进程执行脚本（复用 py_step_runner.py 协议，不 mock）、真实
文件系统落盘的脚本仓库版本管理、真实的 run 记录与看板汇总聚合。

关于 LLM/Agent 部分的说明
--------------------------
`HybridExecutor` 本身不关心 Explorer/Repairer/Fallback 的具体实现，只
依赖 `explorer.py::Explorer` / `repairer.py::Repairer` / `FallbackExecutor`
这几个接口（见 spec.py 与各模块的 ABC 定义）。生产环境下应该用
`default_executor(project_root, mini_agent_config=cfg)` 拿到接了真实
`LLMExplorer`/`AgentExplorer`/`LLMRepairer`/`AgentRepairer` 的实例。

本演示运行在没有配置 LLM API Key 的沙箱环境里，因此用三个"规则版"
替身（`RuleBasedExplorer`/`RuleBasedRepairer`/`RuleBasedFallback`）代替
真实的 LLM 调用——它们实现的接口与 `LLMExplorer`/`LLMRepairer`/
`FallbackExecutor` 完全一致，只是内部不发网络请求、用固定规则直接产出
脚本/答案。这样可以在不消耗真实 API 配额的前提下，把 `HybridExecutor`
自身的编排逻辑、`ScriptRepository` 的版本管理、`ScriptRunner` 的真实子
进程执行、`RunRecorder` 的落盘、`kanban_summary` 的聚合，全部作为真实
代码路径跑一遍。把这三个替身换成 `LLMExplorer(app_cfg)` 等真实类，
其余代码不需要改动一行——这正是本演示要验证的"可插拔"设计。

运行方式：
    cd mini_agent-master
    pip install -e . --break-system-packages   # 如果尚未安装
    python examples/hybrid_exec_demo.py
"""

from __future__ import annotations

import json
import shutil
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_agent.hybrid_exec import (  # noqa: E402
    ExecutionTier,
    FallbackExecutor,
    HybridExecutor,
    ReexplorePolicy,
    RunRecorder,
    ScriptRepository,
    TaskSpec,
    build_kanban_summary,
)
from mini_agent.hybrid_exec.explorer import Explorer  # noqa: E402
from mini_agent.hybrid_exec.repairer import Repairer  # noqa: E402
from mini_agent.hybrid_exec.runner import RunnerAppConfig, ScriptRunner  # noqa: E402

DEMO_ROOT = Path(__file__).resolve().parent / "_hybrid_exec_demo_workspace"


def _hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ---------------------------------------------------------------------------
# 规则版 Explorer/Repairer/Fallback（LLM/Agent 的可插拔替身，见模块头部说明）
# ---------------------------------------------------------------------------


class RuleBasedExplorer(Explorer):
    """模拟 LLMExplorer：根据 task_id 直接产出预设的正确脚本。真实场景下
    这一步是把 task.description + input_data 样例拼进 prompt 交给 LLM。"""

    SCRIPTS = {
        "csv_stats_v1": textwrap.dedent(
            """
            def run(ctx):
                numbers = ctx.params.get("numbers", [])
                if not numbers:
                    return {"sum": 0, "avg": 0, "max": None, "min": None, "count": 0}
                return {
                    "sum": sum(numbers),
                    "avg": sum(numbers) / len(numbers),
                    "max": max(numbers),
                    "min": min(numbers),
                    "count": len(numbers),
                }
            """
        ).strip()
        + "\n",
    }

    def explore(self, task: TaskSpec) -> str:
        code = self.SCRIPTS.get(task.task_id)
        if code is None:
            raise NotImplementedError(f"RuleBasedExplorer 没有为 {task.task_id!r} 预置脚本")
        return code


class RuleBasedRepairer(Repairer):
    """模拟 LLMRepairer：看到 broken_code 里的已知 bug 标记就产出修复版。
    真实场景下这一步是把 broken_code + traceback 拼进 prompt 交给 LLM。"""

    def repair(self, task: TaskSpec, broken_code: str, outcome) -> str:
        if "BUG_MARKER_MISSING_KEY" in broken_code:
            return textwrap.dedent(
                """
                def run(ctx):
                    text = ctx.params.get("text", "")
                    return {"reversed": text[::-1], "length": len(text)}
                """
            ).strip() + "\n"
        raise NotImplementedError("RuleBasedRepairer 不认识这个错误模式，模拟修复失败")


class AlwaysFailRepairer(Repairer):
    """模拟"LLM 修复始终没修对"的场景：原样返回坏代码，用于演示
    ScriptRepository 的连续失败自动退役机制。"""

    def repair(self, task: TaskSpec, broken_code: str, outcome) -> str:
        return broken_code  # 故意不修，dry-run 还会失败


class RuleBasedFallback(FallbackExecutor):
    """模拟 FallbackExecutor.llm_direct/agent_direct：脚本这条路彻底走
    不通时，直接给一个兜底答案（不产出脚本、不写回仓库）。"""

    def __init__(self):  # 不需要 app_cfg，覆盖父类构造
        pass

    def llm_direct(self, task: TaskSpec) -> str:
        return json.dumps({"fallback": True, "note": f"LLM 直接兜底完成任务 {task.task_id}"})

    def agent_direct(self, task: TaskSpec) -> str:
        return json.dumps({"fallback": True, "note": f"Agent 直接兜底完成任务 {task.task_id}"})


# ---------------------------------------------------------------------------
# 组装一个 HybridExecutor（除 Explorer/Repairer/Fallback 外全部是真实组件）
# ---------------------------------------------------------------------------


def build_demo_executor(project_root: Path, *, repairer: Repairer) -> HybridExecutor:
    app_cfg = RunnerAppConfig(project_root=str(project_root))
    repo = ScriptRepository(
        project_root / ".agent" / "hybrid_exec" / "scripts",
        retire_after_consecutive_fail=3,
    )
    script_runner = ScriptRunner(app_cfg)  # 真实：会真的拉起子进程执行脚本
    run_recorder = RunRecorder(project_root / ".agent" / "hybrid_exec" / "runs")  # 真实：真的落盘
    explorer = RuleBasedExplorer()
    fallback = RuleBasedFallback()
    return HybridExecutor(
        repo=repo,
        script_runner=script_runner,
        llm_explorer=explorer,
        agent_explorer=explorer,
        llm_repairer=repairer,
        agent_repairer=repairer,
        fallback=fallback,
        run_recorder=run_recorder,
        reexplore_policy=ReexplorePolicy(enabled=False),
    )


def result_line(result) -> str:
    return (
        f"ok={result.ok} tier={result.tier_used.value} "
        f"script_version={result.script_version} duration={result.duration:.3f}s\n"
        f"  output={result.output!r}\n"
        f"  attempts=[{', '.join(a.stage + ('✓' if a.ok else '✗') for a in result.attempts)}]"
    )


def main() -> None:
    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)
    DEMO_ROOT.mkdir(parents=True)
    (DEMO_ROOT / ".agent").mkdir()

    # ======================================================================
    # 场景一：仓库里没有脚本 → 探索 → dry-run 通过 → 转正 → 真实执行
    # ======================================================================
    _hr("场景一：首次调用，从 0 探索出脚本并真实执行（真实子进程，非 mock）")
    executor = build_demo_executor(DEMO_ROOT, repairer=RuleBasedRepairer())

    task1 = TaskSpec(
        task_id="csv_stats_v1",
        description="给定一组数字，计算 sum/avg/max/min/count，返回 JSON",
        input_data={"numbers": [4, 8, 15, 16, 23, 42]},
        output_validator=lambda out: (
            isinstance(out, dict) and out.get("sum") == 108,
            f"期望 sum=108，实际 {out.get('sum') if isinstance(out, dict) else out!r}",
        ),
    )
    r1 = executor.run(task1)
    print(result_line(r1))
    assert r1.ok and r1.tier_used == ExecutionTier.SCRIPT and r1.script_version == 1
    print("✅ 验证通过：探索出的脚本 dry-run 通过、转正为 v1、真实子进程执行且结果正确")

    # ======================================================================
    # 场景二：仓库里已有脚本 → 直接复用，不再重新探索
    # ======================================================================
    _hr("场景二：同一 task_id 再次调用，直接复用已有脚本（不再探索）")
    task1b = TaskSpec(
        task_id="csv_stats_v1",
        description=task1.description,
        input_data={"numbers": [1, 2, 3, 4, 5]},
        output_validator=lambda out: (out.get("sum") == 15, "sum 应为 15"),
    )
    r2 = executor.run(task1b)
    print(result_line(r2))
    assert r2.ok and r2.script_version == 1
    stages2 = [a.stage for a in r2.attempts]
    assert "script_run" in stages2 and not any(s.startswith("explore_") for s in stages2)
    print("✅ 验证通过：第二次调用直接命中已有 v1 脚本，未触发探索")

    # ======================================================================
    # 场景三：脚本报错 → 自愈修复 → 存为新版本 → 真实执行
    # ======================================================================
    _hr("场景三：已有脚本执行报错 → LLMRepairer 自愈修复 → 存为 v2 → 真实执行")
    # 人为往仓库里塞一个"带已知 bug"的脚本，模拟"人写的脚本有缺陷"或
    # "上一版脚本在新输入下报错"的场景。
    repo2 = ScriptRepository(DEMO_ROOT / ".agent" / "hybrid_exec" / "scripts", retire_after_consecutive_fail=3)
    broken_code = textwrap.dedent(
        """
        def run(ctx):
            # BUG_MARKER_MISSING_KEY: 读错了 key，ctx.params 里其实是 "text" 不是 "content"
            text = ctx.params["content"]
            return {"reversed": text[::-1]}
        """
    ).strip() + "\n"
    repo2.save_new_version("text_reverse", broken_code, created_by="manual")

    task3 = TaskSpec(
        task_id="text_reverse",
        description="将输入文本反转，返回 {reversed, length}",
        input_data={"text": "hybrid_exec"},
        output_validator=lambda out: (
            isinstance(out, dict) and out.get("reversed") == "cexe_dirbyh",
            "reversed 字段应为输入文本的反转",
        ),
        max_script_repair_attempts=2,
    )
    r3 = executor.run(task3)
    print(result_line(r3))
    assert r3.ok and r3.tier_used == ExecutionTier.SCRIPT and r3.script_version == 2
    stages = [a.stage for a in r3.attempts]
    assert "script_run" in stages and any(s.startswith("repair_") for s in stages)
    print("✅ 验证通过：坏脚本先报错，Repairer 修复后 dry-run 通过、存为 v2、真实执行成功")

    # ======================================================================
    # 场景四：修复始终失败 → 连续失败达到阈值 → 自动退役 → 降级 Fallback
    # ======================================================================
    _hr("场景四：脚本反复失败且修复不了 → 自动退役 → 降级到 Fallback 兜底")
    executor_bad = build_demo_executor(DEMO_ROOT, repairer=AlwaysFailRepairer())
    repo3 = ScriptRepository(DEMO_ROOT / ".agent" / "hybrid_exec" / "scripts", retire_after_consecutive_fail=3)
    always_fail_code = "def run(ctx):\n    raise RuntimeError('永远失败，模拟无法修复的脚本')\n"
    repo3.save_new_version("always_fail_task", always_fail_code, created_by="manual")

    task4 = TaskSpec(
        task_id="always_fail_task",
        description="故意设计成必然失败的任务，用于验证退役与降级",
        input_data={},
        max_script_repair_attempts=1,
    )
    results4 = []
    for i in range(3):
        res = executor_bad.run(task4)
        results4.append(res)
        active = repo3.get_active_script("always_fail_task")
        active_desc = (
            f"version={active.version} status={active.status} consecutive_fail={active.consecutive_fail}"
            if active
            else "（无 active 版本，已全部退役）"
        )
        print(f"第 {i + 1} 次调用: {result_line(res)}")
        print(f"  仓库状态: {active_desc}")

    last = results4[-1]
    assert last.tier_used == ExecutionTier.LLM  # 降级到 RuleBasedFallback.llm_direct
    assert last.script_version is None
    final_active = repo3.get_active_script("always_fail_task")
    assert final_active is None or final_active.status == "retired"
    print("✅ 验证通过：脚本连续失败达到阈值后自动退役，最终降级到 Fallback 兜底给出结果")

    # ======================================================================
    # 场景五：可观测性 —— run 记录落盘 + kanban 汇总
    # ======================================================================
    _hr("场景五：可观测性 —— 真实的 run 记录落盘 + kanban_summary 聚合")
    runs_dir = DEMO_ROOT / ".agent" / "hybrid_exec" / "runs"
    for task_dir in sorted(runs_dir.iterdir()):
        summary_path = task_dir / "summary.json"
        if summary_path.exists():
            print(f"- {task_dir.name}/summary.json: {summary_path.read_text(encoding='utf-8').strip()}")

    summary = build_kanban_summary(DEMO_ROOT)
    print("\nkanban 汇总（GET /v1/hybrid_exec/summary 会返回同样结构）：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    scripts_dir = DEMO_ROOT / ".agent" / "hybrid_exec" / "scripts"
    print("\n脚本仓库磁盘布局（真实落盘，非内存 mock）：")
    for p in sorted(scripts_dir.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(DEMO_ROOT)}")

    _hr("全部场景验证通过 ✅")
    print(
        "结论：HybridExecutor 的编排逻辑、ScriptRepository 的版本管理与退役、\n"
        "ScriptRunner 的真实子进程执行（复用 py_step_runner.py 协议）、\n"
        "RunRecorder 的落盘统计、kanban_summary 的聚合，均在真实文件系统/\n"
        "真实子进程环境下验证可用。Explorer/Repairer/Fallback 用规则版替身\n"
        "代替了真实 LLM 调用（因本沙箱无 LLM API Key），生产环境换成\n"
        "default_executor(project_root, mini_agent_config=cfg) 即可接入真实\n"
        "LLM/Agent，接口完全一致、无需改动调用方代码。"
    )


if __name__ == "__main__":
    main()
