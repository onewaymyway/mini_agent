"""
hybrid_exec/executor.py — HybridExecutor：顶层编排器，唯一对外入口

对应 next_doc/hybrid_exec_design_plan.md §4（执行流程）。

决策逻辑（成本优先）：
  1. 有 active 脚本 → 直接跑；失败 → 修复阶段（LLM 修复优先，Agent 修复兜底）。
  2. 没有 active 脚本（或强制重探索）→ 探索阶段（LLM 探索优先，Agent 探索兜底），
     产出脚本先用本次真实输入做一次 dry-run，通过才转正入库。
  3. 脚本这条路彻底走不通（探索失败 / 修复次数用尽）→ Fallback：
     LLM 直接给答案 → 仍不满足 output_validator 或 LLM 也不可用 → Agent 直接给答案。

防御性设计：任意 Explorer/Repairer/Fallback 实现（不管是当前的 LLM/Agent
实现，还是未来插件替换的实现）抛出异常（含 NotImplementedError）时，本
编排器统一捕获、记一条失败 attempt 后继续按流程往下走，不会导致整个
run() 直接崩溃。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from .explorer import AgentExplorer, Explorer, LLMExplorer
from .fallback import FallbackExecutor
from .policy import ReexplorePolicy
from .recorder import RunRecorder
from .repairer import AgentRepairer, LLMRepairer, Repairer
from .repository import ScriptRepository
from .runner import RunnerAppConfig, ScriptRunner
from .spec import AttemptRecord, ExecutionResult, ExecutionTier, ScriptOutcome, TaskSpec


class HybridExecutor:
    def __init__(
        self,
        repo: ScriptRepository,
        script_runner: ScriptRunner,
        llm_explorer: Explorer,
        agent_explorer: Explorer,
        llm_repairer: Repairer,
        agent_repairer: Repairer,
        fallback: FallbackExecutor,
        run_recorder: Optional[RunRecorder] = None,
        reexplore_policy: Optional[ReexplorePolicy] = None,
    ) -> None:
        self.repo = repo
        self.script_runner = script_runner
        self.llm_explorer = llm_explorer
        self.agent_explorer = agent_explorer
        self.llm_repairer = llm_repairer
        self.agent_repairer = agent_repairer
        self.fallback = fallback
        self.run_recorder = run_recorder
        self.reexplore_policy = reexplore_policy

    # -- 对外入口 ----------------------------------------------------------

    def run(self, task: TaskSpec) -> ExecutionResult:
        result = self._run(task)
        if self.run_recorder is not None:
            try:
                self.run_recorder.record(task.task_id, result)
            except OSError:
                pass  # run 记录落盘失败不应该影响本次执行结果的返回
        return result

    def _run(self, task: TaskSpec) -> ExecutionResult:
        start = time.monotonic()
        attempts: "list[AttemptRecord]" = []

        active = None if task.force_reexplore else self.repo.get_active_script(task.task_id)

        # [P4] 跨 run 主动重探索：即使这次的 active 脚本还没坏（没到 retire
        # 阈值），如果它的累计成功率已经不达标，机会主义地先探索一版新的；
        # 探索失败也不影响——仍然继续走下面的正常流程用现在这个脚本。
        if active is not None and self.reexplore_policy is not None:
            should, reason = self.reexplore_policy.should_reexplore(active)
            attempts.append(AttemptRecord("proactive_reexplore_check", ExecutionTier.SCRIPT, should, reason))
            if should:
                explored = self._explore(task, attempts)
                if explored is not None:
                    return self._finish(True, explored[1], ExecutionTier.SCRIPT, explored[0], attempts, start)
                # 主动探索没成功：不影响现有脚本，继续往下用它正常执行。

        if active is not None:
            script_path = self.repo.get_script_path(task.task_id, active.version)
            outcome = self._run_script(task, script_path, attempts, stage="script_run")
            if outcome.ok:
                ok, reason = task.run_validator(outcome.output)
                attempts.append(AttemptRecord("script_run_validate", ExecutionTier.SCRIPT, ok, reason))
                if ok:
                    self.repo.record_success(task.task_id, active.version)
                    return self._finish(True, outcome.output, ExecutionTier.SCRIPT, active.version, attempts, start)
                # 校验不通过按"失败"对待，走修复阶段（脚本能跑但结果不对，同样需要修）
                self.repo.record_failure(task.task_id, active.version, f"输出未通过校验：{reason}")
                outcome = ScriptOutcome(ok=False, error=f"输出未通过 output_validator：{reason}")
            else:
                self.repo.record_failure(task.task_id, active.version, outcome.error or "未知错误")

            repaired = self._repair_loop(task, active.version, script_path, outcome, attempts)
            if repaired is not None:
                return self._finish(True, repaired[1], ExecutionTier.SCRIPT, repaired[0], attempts, start)
            # 修复彻底失败：走到这里说明连续失败次数大概率已达阈值触发 retire，
            # 无论是否 retire，都不再用这个脚本，直接进入 Fallback。

        else:
            explored = self._explore(task, attempts)
            if explored is not None:
                version, output = explored
                return self._finish(True, output, ExecutionTier.SCRIPT, version, attempts, start)

        # -- 脚本这条路走不通，Fallback --------------------------------
        result = self._fallback(task, attempts)
        return self._finish(result[0], result[1], result[2], None, attempts, start)

    # -- 内部：脚本执行 -----------------------------------------------------

    def _run_script(
        self, task: TaskSpec, script_path: Path, attempts: "list[AttemptRecord]", *, stage: str
    ) -> ScriptOutcome:
        t0 = time.monotonic()
        outcome = self.script_runner.run(script_path, task)
        dur = time.monotonic() - t0
        attempts.append(
            AttemptRecord(stage, ExecutionTier.SCRIPT, outcome.ok, outcome.error or "", dur)
        )
        return outcome

    # -- 内部：探索阶段 -----------------------------------------------------

    def _explore(self, task: TaskSpec, attempts: "list[AttemptRecord]") -> Optional["tuple[int, object]"]:
        if ExecutionTier.SCRIPT not in task.allow_tiers:
            return None

        if ExecutionTier.LLM in task.allow_tiers:
            code = self._safe_call(
                lambda: self.llm_explorer.explore(task), "explore_llm", ExecutionTier.LLM, attempts
            )
            if code is not None:
                verified = self._dry_run_and_store(task, code, "llm_explorer", attempts, stage="explore_llm_dryrun")
                if verified is not None:
                    return verified

        if ExecutionTier.AGENT in task.allow_tiers:
            code = self._safe_call(
                lambda: self.agent_explorer.explore(task), "explore_agent", ExecutionTier.AGENT, attempts
            )
            if code is not None:
                verified = self._dry_run_and_store(
                    task, code, "agent_explorer", attempts, stage="explore_agent_dryrun"
                )
                if verified is not None:
                    return verified

        return None

    def _dry_run_and_store(
        self,
        task: TaskSpec,
        code: str,
        created_by: str,
        attempts: "list[AttemptRecord]",
        *,
        stage: str,
    ) -> Optional["tuple[int, object]"]:
        """把候选脚本先写到一个临时文件里做 dry-run（不直接入库，避免刚探索
        出来就跑不通的脚本污染仓库版本历史），通过了才正式存入
        ScriptRepository 并转正为 active 版本。"""
        import tempfile

        with tempfile.TemporaryDirectory(prefix="mini_agent_hybrid_exec_dryrun_") as tmp_dir:
            candidate_path = Path(tmp_dir) / "candidate.py"
            candidate_path.write_text(code, encoding="utf-8")
            t0 = time.monotonic()
            outcome = self.script_runner.run(candidate_path, task)
            dur = time.monotonic() - t0
            if not outcome.ok:
                attempts.append(AttemptRecord(stage, ExecutionTier.SCRIPT, False, outcome.error or "", dur))
                return None
            ok, reason = task.run_validator(outcome.output)
            attempts.append(AttemptRecord(stage, ExecutionTier.SCRIPT, ok, reason, dur))
            if not ok:
                return None

        rec = self.repo.save_new_version(task.task_id, code, created_by)
        self.repo.record_success(task.task_id, rec.version)
        return rec.version, outcome.output

    # -- 内部：修复阶段 -----------------------------------------------------

    def _repair_loop(
        self,
        task: TaskSpec,
        version: int,
        script_path: Path,
        outcome: ScriptOutcome,
        attempts: "list[AttemptRecord]",
    ) -> Optional["tuple[int, object]"]:
        broken_code = self.repo.load_code(task.task_id, version)
        max_attempts = max(1, task.max_script_repair_attempts)

        for i in range(max_attempts):
            is_last = i == max_attempts - 1
            # 只有当预算允许"先 LLM 再 Agent"时才在最后一次升级到 Agent；
            # max_attempts=1 时预算只够试一次，优先用更便宜的 LLM 修复，
            # 不直接跳到 Agent（Agent 兜底是给"LLM 修了还是不行"的情况用的）。
            use_agent = is_last and max_attempts > 1 and ExecutionTier.AGENT in task.allow_tiers
            if use_agent:
                repairer, tier_label = self.agent_repairer, "agent"
            else:
                repairer, tier_label = self.llm_repairer, "llm"

            if ExecutionTier.LLM not in task.allow_tiers and not use_agent:
                break  # 连 LLM 修复都不允许，没有更弱的修复手段可用

            new_code = self._safe_call(
                lambda: repairer.repair(task, broken_code, outcome),
                f"repair_{tier_label}#{i + 1}",
                ExecutionTier.AGENT if use_agent else ExecutionTier.LLM,
                attempts,
            )
            if new_code is None:
                continue

            verified = self._dry_run_and_store(
                task,
                new_code,
                f"{tier_label}_repairer",
                attempts,
                stage=f"repair_{tier_label}#{i + 1}_dryrun",
            )
            if verified is not None:
                return verified

            broken_code = new_code  # 下一轮在这次修复结果的基础上继续修

        # 修复彻底失败，记一次失败，触发 retire 判定（是否真正 retire 由
        # ScriptRepository 内部的 consecutive_fail 阈值决定）。
        self.repo.record_failure(task.task_id, version, "多轮修复后仍无法通过 dry-run")
        return None

    # -- 内部：Fallback -----------------------------------------------------

    def _fallback(self, task: TaskSpec, attempts: "list[AttemptRecord]") -> "tuple[bool, object, ExecutionTier]":
        if ExecutionTier.LLM in task.allow_tiers:
            output = self._safe_call(
                lambda: self.fallback.llm_direct(task), "fallback_llm", ExecutionTier.LLM, attempts
            )
            if output is not None:
                ok, reason = task.run_validator(output)
                attempts.append(AttemptRecord("fallback_llm_validate", ExecutionTier.LLM, ok, reason))
                if ok:
                    return True, output, ExecutionTier.LLM

        if ExecutionTier.AGENT in task.allow_tiers:
            output = self._safe_call(
                lambda: self.fallback.agent_direct(task), "fallback_agent", ExecutionTier.AGENT, attempts
            )
            if output is not None:
                ok, reason = task.run_validator(output)
                attempts.append(AttemptRecord("fallback_agent_validate", ExecutionTier.AGENT, ok, reason))
                # Agent 已经是最高能力层级，没有再降级的空间，如实返回结果，
                # ok 字段如实反映校验结论。
                return ok, output, ExecutionTier.AGENT

        # 所有允许的层级都尝试过仍拿不到结果（比如 P1 阶段 AGENT 未实现，
        # 且 LLM fallback 也未通过校验）。
        return False, None, ExecutionTier.LLM

    # -- 通用小工具 ---------------------------------------------------------

    @staticmethod
    def _safe_call(fn, stage: str, tier: ExecutionTier, attempts: "list[AttemptRecord]"):
        """统一处理 NotImplementedError（P1 阶段 Agent 相关能力未实现）和其它
        异常：都记一条失败 attempt，返回 None 交给调用方决定是否继续降级，
        不让单个手段的异常直接打断整个决策流程。"""
        t0 = time.monotonic()
        try:
            result = fn()
        except NotImplementedError as e:
            attempts.append(AttemptRecord(stage, tier, False, str(e), time.monotonic() - t0))
            return None
        except Exception as e:  # noqa: BLE001 — 探索/修复/兜底调用失败不应打断整体流程
            attempts.append(AttemptRecord(stage, tier, False, f"{type(e).__name__}: {e}", time.monotonic() - t0))
            return None
        attempts.append(AttemptRecord(stage, tier, True, "", time.monotonic() - t0))
        return result

    @staticmethod
    def _finish(
        ok: bool,
        output,
        tier: ExecutionTier,
        script_version: Optional[int],
        attempts: "list[AttemptRecord]",
        start: float,
    ) -> ExecutionResult:
        return ExecutionResult(
            ok=ok,
            output=output,
            tier_used=tier,
            script_version=script_version,
            attempts=attempts,
            duration=time.monotonic() - start,
        )


def default_executor(
    project_root,
    *,
    mini_agent_config=None,
    llm: object = None,
    retire_after_consecutive_fail: int = 3,
    reexplore_policy: Optional[ReexplorePolicy] = None,
) -> HybridExecutor:
    """便捷工厂：给定项目根目录（以及可选的已加载好的 mini_agent Config 对象），
    组装出一个默认配置的 HybridExecutor，供独立调用场景直接使用（无需手动拼
    ScriptRepository/ScriptRunner/Explorer/Repairer 等各个组件）。

    两种使用形态（对应 next_doc/hybrid_exec_design_plan.md 的新增要求）：
      1. **独立执行**：不传 `llm`。`LLMExplorer`/`LLMRepairer`/`FallbackExecutor`
         内部会在真正需要发起 LLM 调用时才调用 `build_llm_helper(app_cfg)`，
         经由 `mini_agent.config.load_config()` 自动按 `project_root` 加载该
         项目的 `providers.json`（与主 Agent、`python_step` 的 `ctx.llm` 走
         同一条解析路径），无需调用方手动传 model/provider/api_key。
      2. **嵌入 workflow（如 python_step 脚本内部）**：传入 `llm=ctx.llm`
         （或任意已构造好的 `LLMHelper` 实例）。只要该对象实现
         `ask(prompt, *, system=...) -> str`（`LLMHelper`/`PyStepLLM` 均满足，
         鸭子类型，不要求具体类型），`LLMExplorer`/`LLMRepairer`/
         `FallbackExecutor` 会直接复用它，不再重新走 `load_config()`——沿用
         workflow 当前已经解析好的 provider/模型/重试策略，避免重复解析
         配置、也不会绕过 workflow 对这次运行做的任何 provider 覆盖。

      `AgentExplorer`/`AgentRepairer`/`FallbackExecutor.agent_direct` 需要的
      是一个可多轮执行、能调用工具的完整 Agent（不是单次问答），因此固定
      通过 `build_minimal_agent()` 按 `app_cfg` 现起一个临时 Agent，不受
      `llm` 参数影响；如需让 Agent 层也对齐 workflow 的模型选择，请通过
      `mini_agent_config`（或直接构造 `RunnerAppConfig`）传入相应的
      model/llm_provider 等字段。

    reexplore_policy 默认不传（即不启用主动重探索，P4 §8 里说明的"跨 run
    自动重探索触发"是 opt-in 的，避免默认行为在没有实际使用数据支撑时就
    悄悄改变已有脚本的稳定使用）。"""
    project_root = Path(project_root)
    if mini_agent_config is not None:
        app_cfg = RunnerAppConfig.from_mini_agent_config(mini_agent_config)
    else:
        app_cfg = RunnerAppConfig(project_root=str(project_root))

    repo = ScriptRepository(
        project_root / ".agent" / "hybrid_exec" / "scripts",
        retire_after_consecutive_fail=retire_after_consecutive_fail,
    )
    script_runner = ScriptRunner(app_cfg)
    run_recorder = RunRecorder(project_root / ".agent" / "hybrid_exec" / "runs")
    return HybridExecutor(
        repo=repo,
        script_runner=script_runner,
        llm_explorer=LLMExplorer(app_cfg, llm=llm),
        agent_explorer=AgentExplorer(app_cfg),
        llm_repairer=LLMRepairer(app_cfg, llm=llm),
        agent_repairer=AgentRepairer(app_cfg),
        fallback=FallbackExecutor(app_cfg, llm=llm),
        run_recorder=run_recorder,
        reexplore_policy=reexplore_policy,
    )
