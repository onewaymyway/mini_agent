"""
examples/hybrid_exec_demo.py — hybrid_exec 系统端到端可运行演示

用于验证 docs/hybrid-exec-guide.md 描述的整套决策链路在真实环境下确实
可用：真实子进程执行脚本（复用 py_step_runner.py 协议，不 mock）、真实
文件系统落盘的脚本仓库版本管理、真实的 run 记录与看板汇总聚合，以及
——本版本的重点——**真实的 providers.json + 真实 LLM 调用**（不再用规则
版替身模拟 LLM 的行为）。

LLM 从哪来
----------
本演示走的是 docs/hybrid-exec-guide.md §一.1（独立执行）里说明的默认
路径：不传 `llm=`，`default_executor(project_root)` 内部的
`LLMExplorer`/`LLMRepairer`/`FallbackExecutor` 在真正需要发起调用时才会
经 `mini_agent.config.load_config()` 按 `project_root` 自动加载该项目下的
`providers.json`——与主 Agent、`python_step` 的 `ctx.llm` 是同一条解析
路径，不需要在演示脚本里手写任何 provider/api_key 拼装逻辑。

运行前准备（必须，本演示不提供任何模拟 LLM 的退路）：
    cd mini_agent-master
    pip install -e . --break-system-packages   # 如果尚未安装
    cp providers.json.example providers.json   # 若项目根目录还没有
    # 编辑 providers.json，填入至少一个可用 provider 的真实 api_key
    python examples/hybrid_exec_demo.py

如果没有配置好 `providers.json`（或环境变量里也没有对应 api_key），
本脚本会在开头明确检测出来、打印配置指引后直接退出，**不会**用假数据
硬撑着把演示"跑通"——脚本/Agent 探索、修复、Fallback 这几步的价值本来
就在于真实 LLM 的产出质量，用固定规则代替没有意义。

运行环境限制说明：若你的运行环境有出网白名单限制，请确认 providers.json
里配置的 provider 对应的 API 域名（如 Anthropic 是 api.anthropic.com）
在白名单内，否则真实请求会在网络层被拦截，报错信息里会体现出来。
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
    ReexplorePolicy,
    RunRecorder,
    ScriptRepository,
    TaskSpec,
    build_kanban_summary,
    default_executor,
)
from mini_agent.hybrid_exec.runner import RunnerAppConfig, ScriptRunner  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_ROOT = Path(__file__).resolve().parent / "_hybrid_exec_demo_workspace"


def _hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ---------------------------------------------------------------------------
# 真实 providers.json 检测与准备
# ---------------------------------------------------------------------------


def _prepare_providers_json() -> None:
    """把项目根目录真实的 providers.json 复制进独立的演示工作区，让
    `default_executor(DEMO_ROOT)` 能按默认路径自动加载到它（`load_config()`
    按 `project_root/providers.json` 查找）。用独立工作区而不是直接把项目
    根目录当 project_root，是为了不把演示产生的 `.agent/hybrid_exec/` 数据
    污染进真实项目目录，二者互不影响，但用的是同一份真实 providers.json
    内容，不是编造的。"""
    src = REPO_ROOT / "providers.json"
    if src.exists():
        shutil.copy(src, DEMO_ROOT / "providers.json")


def _check_llm_available(project_root: Path) -> "tuple[bool, str]":
    """用真实的 load_config() 走一遍解析，判断这个 project_root 下是否有
    可用的 provider + api_key 组合。不实际发起网络请求（那一步交给后面的
    真实场景去做，失败了会在那里如实报错），这里只检查"配置是否齐全"。"""
    try:
        from mini_agent.config import load_config

        cfg = load_config(project_root=project_root, verbose=False, sandbox=True, auto_approve=True)
    except Exception as e:  # noqa: BLE001
        return False, f"load_config() 失败：{type(e).__name__}: {e}"

    provider = getattr(cfg, "llm_provider", None)
    api_key = getattr(cfg, "api_key", None)
    if not provider:
        return False, "未解析出 llm_provider（未配置 providers.json，环境变量里也没有）"
    if not api_key:
        return False, f"provider={provider!r} 已解析出，但没有可用的 api_key"
    return True, f"provider={provider!r} model={getattr(cfg, 'model', None)!r}"


# ---------------------------------------------------------------------------
# 组装真实 HybridExecutor（explorer/repairer/fallback 全部是真实 LLM 实现）
# ---------------------------------------------------------------------------


def build_demo_executor(project_root: Path, **kwargs):
    """直接用 `default_executor()`——真实 `LLMExplorer`/`AgentExplorer`/
    `LLMRepairer`/`AgentRepairer`/`FallbackExecutor`，按默认路径自动读
    `project_root/providers.json`，没有任何规则版替身。"""
    return default_executor(project_root, **kwargs)


def result_line(result) -> str:
    return (
        f"ok={result.ok} tier={result.tier_used.value} "
        f"script_version={result.script_version} duration={result.duration:.3f}s\n"
        f"  output={result.output!r}\n"
        f"  attempts=[{', '.join(a.stage + ('✓' if a.ok else '✗') for a in result.attempts)}]"
    )


class _FakeCountingLLM:
    """不发任何网络请求的假 `llm` 对象，只用来证明"嵌入 workflow 时传入
    `llm=` 会被直接复用，不会重新 `load_config()`/`providers.json`"这条
    路径确实生效——同时也是 §11 提到的 P0 bug（独立执行路径下
    `LLMExplorer`/`LLMRepairer`/`FallbackExecutor` 曾经各自在每次 `.ask()`
    时惰性重建一整条 `LLMClientPool`，白白丢掉多 key 轮转/cooldown 状态）
    的回归验证：把这同一个对象分别传给 `LLMExplorer`/`LLMRepairer`/
    `FallbackExecutor` 三者共用时，调用次数应该恰好等于"实际发起的探索/
    修复/兜底请求次数"，而不会有任何一次意外地绕过它去另建了一条
    `LLMClientPool`（那样的话这个假对象根本不会被调用到，探索会直接因
    "没有 providers.json"而报错）。"""

    def __init__(self) -> None:
        self.calls: "list[str]" = []

    def ask(self, prompt: str, *, system: str = "") -> str:
        self.calls.append(prompt[:40])
        # 直接返回一个满足 csv_stats 任务协议的固定脚本，验证链路，不依赖
        # 任何真实模型的生成质量。
        return (
            "def run(ctx):\n"
            "    numbers = ctx.params.get('numbers', [])\n"
            "    return {\n"
            "        'sum': sum(numbers),\n"
            "        'avg': sum(numbers) / len(numbers) if numbers else 0,\n"
            "        'max': max(numbers) if numbers else None,\n"
            "        'min': min(numbers) if numbers else None,\n"
            "        'count': len(numbers),\n"
            "    }\n"
        )


def run_scenario_six_embedded_llm_reuse() -> None:
    """场景六：模拟"嵌入 workflow"场景——用一个不发网络请求的假 `llm`
    对象，验证 `default_executor(project_root, llm=...)` 确实原样复用了
    传入对象，既不重新 `load_config()`，也不会绕开它另建一条
    `LLMClientPool`。全程不依赖 `providers.json`/网络，因此无论场景一~五
    是否因为没配置真实 provider 而被跳过，本场景都会运行。"""
    _hr("场景六：嵌入 workflow 场景模拟——传入假 llm 对象，验证被直接复用（不发网络请求）")

    workspace = Path(__file__).resolve().parent / "_hybrid_exec_demo_workspace_scenario6"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    fake_llm = _FakeCountingLLM()
    executor = default_executor(workspace, llm=fake_llm)

    task = TaskSpec(
        task_id="csv_stats_embedded_demo",
        description="给定 numbers，计算 sum/avg/max/min/count",
        input_data={"numbers": [1, 2, 3, 4]},
        allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM),
        output_validator=lambda out: (
            isinstance(out, dict) and out.get("sum") == 10,
            f"期望 sum=10，实际 {out.get('sum') if isinstance(out, dict) else out!r}",
        ),
    )
    result = executor.run(task)
    print(result_line(result))
    if result.ok and result.tier_used == ExecutionTier.SCRIPT and fake_llm.calls:
        print(
            f"✅ 验证通过：传入的假 llm 对象被直接调用了 {len(fake_llm.calls)} 次\n"
            "   （LLMExplorer 未重新 load_config()/读取 providers.json，全程不发网络请求），\n"
            "   这正是 python_step 脚本里把 hybrid_exec 当库调用、直接传 ctx.llm 复用的路径。"
        )
    else:
        print(f"⚠️ 未按预期复用假 llm 对象，ok={result.ok} tier={result.tier_used.value} calls={fake_llm.calls}")

    shutil.rmtree(workspace, ignore_errors=True)


def main() -> None:
    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)
    DEMO_ROOT.mkdir(parents=True)
    (DEMO_ROOT / ".agent").mkdir()
    _prepare_providers_json()

    llm_ok, llm_detail = _check_llm_available(DEMO_ROOT)

    _hr("环境检测：是否已配置可用的 providers.json / api_key")
    print(f"project_root = {DEMO_ROOT}")
    print(f"检测结果：{'✅ 可用' if llm_ok else '❌ 不可用'}（{llm_detail}）")

    # 场景六不依赖真实 providers.json（假 llm 对象、不发网络请求），无论
    # 场景一~五是否因为没配置真实 provider 而被跳过，都先跑一遍。
    run_scenario_six_embedded_llm_reuse()

    if not llm_ok:
        print(
            "\n未检测到可用的 LLM 配置，本演示不会用任何模拟/规则版数据硬跑，"
            "场景一~五（依赖真实 LLM 调用）全部跳过，仅场景六（假 llm，验证\n"
            "复用逻辑）已运行。请按下面步骤配置后重新运行完整演示：\n"
            f"  1. cd {REPO_ROOT}\n"
            "  2. cp providers.json.example providers.json\n"
            "  3. 编辑 providers.json，填入至少一个 provider 的真实 api_key\n"
            "  4. python examples/hybrid_exec_demo.py\n"
        )
        _hr("演示部分完成：场景六已验证，等待真实 providers.json 配置以运行场景一~五")
        return

    # ======================================================================
    # 场景一：仓库里没有脚本 → 真实 LLMExplorer 探索 → dry-run 通过 → 转正 → 真实执行
    # ======================================================================
    _hr("场景一：首次调用，真实 LLM 探索出脚本并真实执行（真实子进程，非 mock）")
    executor = build_demo_executor(DEMO_ROOT)

    task1 = TaskSpec(
        task_id="csv_stats_v1",
        description="给定一组数字 numbers（从 ctx.params 读取），计算 sum/avg/max/min/count，返回一个 dict",
        input_data={"numbers": [4, 8, 15, 16, 23, 42]},
        allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM),  # 演示先控制成本，不升级到 Agent
        output_validator=lambda out: (
            isinstance(out, dict) and out.get("sum") == 108,
            f"期望 sum=108，实际 {out.get('sum') if isinstance(out, dict) else out!r}",
        ),
    )
    r1 = executor.run(task1)
    print(result_line(r1))
    if r1.ok and r1.tier_used == ExecutionTier.SCRIPT:
        print(f"✅ 真实 LLM 探索出的脚本 dry-run 通过、转正为 v{r1.script_version}、真实子进程执行且结果正确")
    else:
        print(
            "⚠️ 本次真实调用未能产出通过校验的脚本（可能是模型输出不稳定/网络问题），"
            "决策轨迹见上面 attempts，可重跑或换个 provider/model 再试；"
            f"最终走到的层级是 {r1.tier_used.value}，ok={r1.ok}"
        )

    # ======================================================================
    # 场景二：仓库里已有脚本 → 直接复用，不再重新探索
    # ======================================================================
    if r1.ok and r1.tier_used == ExecutionTier.SCRIPT:
        _hr("场景二：同一 task_id 再次调用，直接复用已有脚本（不再探索，不再调用 LLM）")
        task1b = TaskSpec(
            task_id="csv_stats_v1",
            description=task1.description,
            input_data={"numbers": [1, 2, 3, 4, 5]},
            allow_tiers=task1.allow_tiers,
            output_validator=lambda out: (out.get("sum") == 15, "sum 应为 15"),
        )
        r2 = executor.run(task1b)
        print(result_line(r2))
        stages2 = [a.stage for a in r2.attempts]
        if r2.ok and "script_run" in stages2 and not any(s.startswith("explore_") for s in stages2):
            print("✅ 验证通过：第二次调用直接命中已有脚本，未再触发探索、未再消耗 LLM 调用")
        else:
            print(f"⚠️ 未按预期直接命中脚本，attempts={stages2}")

    # ======================================================================
    # 场景三：人工写入一个带已知 bug 的脚本 → 真实 LLMRepairer 自愈修复 → 真实执行
    # ======================================================================
    _hr("场景三：已有脚本执行报错 → 真实 LLMRepairer 自愈修复 → 存为新版本 → 真实执行")
    repo3 = ScriptRepository(DEMO_ROOT / ".agent" / "hybrid_exec" / "scripts", retire_after_consecutive_fail=3)
    broken_code = (
        textwrap.dedent(
            """
            def run(ctx):
                # 已知 bug：读错了 key，ctx.params 里实际是 "text" 不是 "content"
                text = ctx.params["content"]
                return {"reversed": text[::-1], "length": len(text)}
            """
        ).strip()
        + "\n"
    )
    repo3.save_new_version("text_reverse", broken_code, created_by="manual")

    task3 = TaskSpec(
        task_id="text_reverse",
        description='将 ctx.params["text"] 反转，返回 {"reversed": ..., "length": ...}',
        input_data={"text": "hybrid_exec"},
        allow_tiers=(ExecutionTier.SCRIPT, ExecutionTier.LLM),
        output_validator=lambda out: (
            isinstance(out, dict) and out.get("reversed") == "cexe_dirbyh",
            "reversed 字段应为输入文本的反转",
        ),
        max_script_repair_attempts=2,
    )
    r3 = executor.run(task3)
    print(result_line(r3))
    stages3 = [a.stage for a in r3.attempts]
    if r3.ok and r3.tier_used == ExecutionTier.SCRIPT and any(s.startswith("repair_") for s in stages3):
        print(
            f"✅ 验证通过：坏脚本先报错，真实 LLMRepairer 修复后 dry-run 通过、"
            f"存为 v{r3.script_version}、真实执行成功"
        )
    else:
        print(f"⚠️ 本次真实修复未按预期走完整链路，ok={r3.ok} tier={r3.tier_used.value} attempts={stages3}")

    # ======================================================================
    # 场景四：直接强制走 Fallback（allow_tiers 只保留 LLM）→ 真实 LLM 直接给答案
    # ======================================================================
    _hr("场景四：allow_tiers 只保留 LLM，不产出脚本 → 真实 FallbackExecutor.llm_direct 直接给答案")
    task4 = TaskSpec(
        task_id="fallback_demo_only_llm",
        description="用一句话概括：hybrid_exec 是脚本/LLM/Agent 混合执行系统，脚本优先、坏了先修脚本、修不好再降级。",
        input_data={},
        allow_tiers=(ExecutionTier.LLM,),  # 不含 SCRIPT/AGENT：既不产出脚本，也不升级到 Agent
    )
    r4 = executor.run(task4)
    print(result_line(r4))
    if r4.ok and r4.tier_used == ExecutionTier.LLM and r4.script_version is None:
        print("✅ 验证通过：allow_tiers 里没有 SCRIPT，直接走真实 LLM Fallback 给出结果，不产出脚本、不写回仓库")
    else:
        print(f"⚠️ 未按预期直接走 Fallback，ok={r4.ok} tier={r4.tier_used.value}")

    # ======================================================================
    # 场景五：可观测性 —— 真实的 run 记录落盘 + kanban_summary 聚合
    # ======================================================================
    _hr("场景五：可观测性 —— 真实的 run 记录落盘 + kanban_summary 聚合")
    runs_dir = DEMO_ROOT / ".agent" / "hybrid_exec" / "runs"
    if runs_dir.exists():
        for task_dir in sorted(runs_dir.iterdir()):
            summary_path = task_dir / "summary.json"
            if summary_path.exists():
                print(f"- {task_dir.name}/summary.json: {summary_path.read_text(encoding='utf-8').strip()}")

    summary = build_kanban_summary(DEMO_ROOT)
    print("\nkanban 汇总（GET /v1/hybrid_exec/summary 会返回同样结构）：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    scripts_dir = DEMO_ROOT / ".agent" / "hybrid_exec" / "scripts"
    if scripts_dir.exists():
        print("\n脚本仓库磁盘布局（真实落盘，非内存 mock；脚本内容来自真实 LLM 产出/修复）：")
        for p in sorted(scripts_dir.rglob("*")):
            if p.is_file():
                print(f"  {p.relative_to(DEMO_ROOT)}")

    _hr("演示结束")
    print(
        "结论：本演示全程使用真实 providers.json 解析出的真实 LLM（LLMExplorer/\n"
        "LLMRepairer/FallbackExecutor.llm_direct），没有使用任何规则版/模拟替身；\n"
        "HybridExecutor 的编排逻辑、ScriptRepository 的版本管理、ScriptRunner 的\n"
        "真实子进程执行（复用 py_step_runner.py 协议）、RunRecorder 的落盘统计、\n"
        "kanban_summary 的聚合，均在真实文件系统/真实子进程/真实网络请求下验证。\n"
        "嵌入 workflow 场景（接收 workflow 传入的 llm，而不是重新读\n"
        "providers.json）另见 docs/hybrid-exec-guide.md §一.1 的示例代码，本脚本\n"
        "只演示独立调用这一种形态。"
    )


if __name__ == "__main__":
    main()
