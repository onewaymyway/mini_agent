"""runtime/runtime.py — Phase 8 Sprint 8-1：AgentRuntime 单次循环骨架。

见 `next_doc/refactor_plan/09-phase8-runtime-convergence-sprint-plan.md`
Sprint 8-1。对应原方案 §41 的核心循环：

    observe → state update → gap detect → plan → simulate → decide →
    execute → record → learn

止损原则（Sprint 8-1 范围声明）：
  - 本 Sprint **只支持单次运行**（`run_once()`），不是常驻循环——持续
    运行留给 Sprint 8-2 的 `runtime/event_loop.py`。
  - `execute`/`record` 两步**不重新实现**，而是直接复用 Phase 1-7 已经
    打通的 `goal_mode/runner.py::GoalRunner`（Goal(old) → GoalAdapter →
    GoalState → 执行 → Outcome → Experience，含 Event 总线四类事件 +
    ExperienceRecorder 自动落库），`AgentRuntime.run_once()` 只是在它
    外面包一层，不改动 `GoalRunner` 内部任何逻辑。
  - `plan`/`simulate`/`decide` 三步对应 Phase 7 已经产出的
    `goals/gap.py::detect_gap()` + `simulation/engine.py` +
    `cognition/decision.py`。Phase 7 Sprint 7-2 执行记录里已经明确
    做过一次范围决策：**不**把"候选生成 → 模拟 → 决策"自动接入主循环
    （避免过度设计，见 `08-phase7-decision-simulation-sprint-plan.md`
    "后续判断依据"一节）。Sprint 8-1 沿用同一个决策——`plan`/
    `simulate`/`decide` 三步默认跳过（`enable_decision_stage=False`，
    保守 opt-in 默认值，与本项目一贯的配置默认值原则一致），只有
    `gap detect` 一步是真实执行的（因为它不需要 LLM 注入、本身就是
    纯函数，代价很低，且是"观察当前状态与目标差距"这个循环骨架里
    真正对应"骨架"部分的一环）。
  - `learn` 对应 Phase 9（Self Evolution 接入统一 Experience）。Sprint 8-1
    时留空；Sprint 9-4 起由 `runtime/learn.py::run_learn_step()` 实现，
    **默认关闭**（`goal_mode.runtime_learn_enabled=False`），关闭时行为与
    Sprint 8-1 完全一致。它只做“Observe 已部署改动 + 汇总重复问题”，不自动
    提案、不自动部署，详见该模块 docstring。
"""

from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from mini_agent.core import (
    Event,
    get_event_bus,
    get_state_manager,
    GoalAdapter,
    GoalState as CoreGoalState,
)
from mini_agent.goal_mode.runner import GoalRunner, GoalRunResult
from mini_agent.goals.gap import detect_gap
from mini_agent.runtime.learn import LearnReport, run_learn_step

if TYPE_CHECKING:
    from mini_agent.agent import Agent
    from mini_agent.config import AppConfig
    from mini_agent.goal_mode.spec import GoalSpec
    from mini_agent.goal_mode.executor import GoalStepExecutor
    from mini_agent.goal_mode.state import GoalState as LegacyGoalState, GoalStateStore

_logger = logging.getLogger("mini_agent.core.trace")


@dataclass
class AgentRuntimeResult:
    """`AgentRuntime.run_once()` 的返回值。

    字段名与 `goal_mode.runner.GoalRunResult` 保持一致（`status` /
    `rounds_used` / `compacts_done` / `final_report` / `goal_spec` /
    `replan_proposal`），使得现有调用方（`cli/commands/goal_mode_cmd.py`）
    改用 `AgentRuntime.run_once()` 之后不需要改动任何读取这些字段的代码
    ——这是 Sprint 8-1 验收标准"旧的 CLI 相关测试仍然通过"的关键前提。
    `gap`/`correlation_id`/`goal_run_result` 是新增的、供未来 Sprint
    （8-2/8-3 及 Phase 9）使用的额外信息，不影响现有调用方。
    """

    status: str
    rounds_used: int
    compacts_done: int
    final_report: str
    goal_spec: "GoalSpec"
    replan_proposal: Optional[dict] = None
    # `plan` 阶段（gap detect）产出，来自 `goals/gap.py::detect_gap()`；
    # 未能计算时（比如 goal_spec 转换失败）为空列表，不阻塞主流程。
    gap: list = field(default_factory=list)
    correlation_id: Optional[str] = None
    # 完整的底层 GoalRunResult，供需要更多细节（如 goal_spec 协商历史）
    # 的调用方使用；`AgentRuntimeResult` 本身的同名字段只是从这里拷贝
    # 出来的一份快照，避免调用方必须知道"还要再深入一层"才能拿到结果。
    goal_run_result: Optional[GoalRunResult] = None
    # Sprint 9-4：`learn` 步骤的报告；未启用（默认）时为 None。
    learn_report: Optional["LearnReport"] = None


class AgentRuntime:
    """Autonomous Runtime 的单次循环骨架（Sprint 8-1：`run_once()` only）。

    不持有任何跨调用的可变状态——每次 `run_once()` 都是一次独立的循环，
    `Agent`/`AppConfig` 只是转发给 `GoalRunner` 用的依赖，本类自己不
    缓存。持续运行（Sprint 8-2 `runtime/event_loop.py`）会在这个基础上
    加一层 `while running: run_once(...)`，届时才需要考虑跨轮次状态。
    """

    def __init__(
        self,
        agent: "Agent",
        cfg: "AppConfig",
        enable_learn_stage: Optional[bool] = None,
        learn_auto_rollback: Optional[bool] = None,
    ) -> None:
        self._agent = agent
        self._cfg = cfg
        # Sprint 9-4：显式参数优先，其次读 `cfg.goal_mode.runtime_learn_*`，
        # 缺省均为 False（保守 opt-in，与 Phase 3/7/8 的一贯做法一致）。
        gm = getattr(cfg, "goal_mode", None)
        self._learn_enabled = (
            enable_learn_stage if enable_learn_stage is not None
            else bool(getattr(gm, "runtime_learn_enabled", False))
        )
        self._learn_auto_rollback = (
            learn_auto_rollback if learn_auto_rollback is not None
            else bool(getattr(gm, "runtime_learn_auto_rollback", False))
        )
        # 供调用方在 `run_once()` 抛出 KeyboardInterrupt 时取到底层
        # `GoalRunner` 实例以便调用 `runner.pause()`（见
        # `cli/commands/goal_mode_cmd.py::_run_goal()`）。只在
        # `run_once()` 内部赋值，`AgentRuntime` 本身仍不持有跨调用状态
        # ——下一次 `run_once()` 会覆盖它，不是"累积历史"的意思。
        self.last_runner: Optional[GoalRunner] = None

    def run_once(
        self,
        goal_spec: "GoalSpec",
        executor: Optional["GoalStepExecutor"] = None,
        state_store: Optional["GoalStateStore"] = None,
        resume_state: Optional["LegacyGoalState"] = None,
    ) -> AgentRuntimeResult:
        """走一遍 `observe → state update → gap detect → plan → simulate →
        decide → execute → record → learn` 骨架，单次运行（不循环）。

        `executor`/`state_store`/`resume_state` 透传给 `GoalRunner`
        构造函数，与 `goal_mode_cmd.py::_handle_resume()` 现有的恢复场景
        保持兼容（Sprint 8-1 范围只要求"用户主动发起 Goal"这一种触发
        路径接入，但透传这几个参数不增加额外风险，为后续扩展保留接口）。
        """
        correlation_id = _uuid.uuid4().hex
        bus = get_event_bus()
        bus.publish(
            Event(
                kind="RuntimeCycleStarted",
                payload={"goal_text": goal_spec.goal_text},
                actor="runtime.AgentRuntime",
                correlation_id=correlation_id,
            )
        )

        # ── observe ──────────────────────────────────────────────────
        # Phase 8 尚未接入真实的 Perception/Observation 管线（那是
        # `perception/` 包既有能力的收编范围，参见 Sprint 8-3"剩余
        # Scheduler 逐个评估"）。本 Sprint 的"observe"只做一件诚实的事：
        # 读一次当前 StateManager 里已经托管的状态快照，供下面 gap
        # detect 使用，不假装有更多能力。
        state_manager = get_state_manager()
        observed_snapshot = state_manager.snapshot()
        _logger.debug(
            "%s",
            Event(
                kind="runtime.observe",
                payload={"observed_kinds": sorted(observed_snapshot.keys())},
            ).to_dict(),
        )

        # ── state update ─────────────────────────────────────────────
        # `GoalState` 的真正托管（`update_state("goal", ...)` + 事件
        # 驱动更新）由 `GoalRunner.run()` 内部完成（Phase 4 Sprint 4-1
        # 已打通的链路），这里不重复托管一次，避免同一个 kind 被两处
        # 分别 seed 造成竞态。本步骤只做一次只读转换（`GoalAdapter.
        # to_new`），供下一步 gap detect 使用。
        core_goal_state: Optional[CoreGoalState] = None
        gap_items: list = []
        try:
            core_goal_state = GoalAdapter.to_new(goal_spec)
        except Exception:
            from mini_agent.errors import log_exception

            log_exception(
                Exception("AgentRuntime.run_once: GoalAdapter.to_new 转换失败"),
                where="mini_agent.runtime.runtime.AgentRuntime.run_once.state_update",
            )

        # ── gap detect ───────────────────────────────────────────────
        # 见本文件顶部说明：这是 `plan/simulate/decide` 三步里唯一默认
        # 执行的一步（纯函数、不需要 LLM 注入、代价低）。`current_state`/
        # `ideal_state` 目前旧版 `GoalSpec` 还没有对应字段，`detect_gap`
        # 对此的处理方式是记一条"未采集"的 problem，而不是报错——这是
        # 诚实反映现状，不是 bug。
        if core_goal_state is not None:
            try:
                core_goal_state = detect_gap(core_goal_state, state_manager=state_manager)
                gap_items = list(core_goal_state.gap)
            except Exception:
                from mini_agent.errors import log_exception

                log_exception(
                    Exception("AgentRuntime.run_once: detect_gap 失败"),
                    where="mini_agent.runtime.runtime.AgentRuntime.run_once.gap_detect",
                )

        # ── plan / simulate / decide ─────────────────────────────────
        # 显式跳过：见本文件顶部"止损原则"说明，沿用 Phase 7 Sprint 7-2
        # 的范围决策（`simulation/engine.py`/`cognition/decision.py` 均
        # 需要调用方注入 LLM callable，本 Sprint 不内置默认实现，也不
        # 强行接入主循环）。TODO: Sprint 8-2/8-3 或专门的评估任务里，
        # 视是否需要"多候选 Action 竞争"场景再决定是否接入。

        # ── execute（复用 Phase 1-7 打通的 GoalRunner 全链路）──────────
        runner = GoalRunner(
            agent=self._agent,
            cfg=self._cfg,
            goal_spec=goal_spec,
            executor=executor,
            state_store=state_store,
            resume_state=resume_state,
        )
        self.last_runner = runner
        goal_run_result = runner.run()

        # ── record ───────────────────────────────────────────────────
        # 已经在 `GoalRunner.run()`/`_finish()` 内部完成：Event 总线上的
        # GoalCreated/ActionStarted/ActionCompleted(/Failed)/GoalUpdated/
        # ExperienceCreated 事件，以及 `ExperienceRecorder` 订阅后的自动
        # 落库，都是"execute"这一步内部自带的产出，这里不重复实现。

        # ── learn ────────────────────────────────────────────────────
        # Sprint 9-4：默认关闭。开启后由 `runtime/learn.py::run_learn_step()`
        # 做 Observe + 问题汇总；它永不抛异常，这里再包一层只是防御性兜底
        # ——learn 是旁路，任何情况下都不能改变 Goal 的执行结果。
        learn_report: Optional[LearnReport] = None
        if self._learn_enabled:
            try:
                from mini_agent.storage.paths import AgentPaths

                learn_report = run_learn_step(
                    AgentPaths(project_root=self._cfg.project_root),
                    auto_rollback=self._learn_auto_rollback,
                )
            except Exception:
                from mini_agent.errors import log_exception

                log_exception(
                    Exception("AgentRuntime.run_once: learn 步骤失败"),
                    where="mini_agent.runtime.runtime.AgentRuntime.run_once.learn",
                )

        completed_payload = {"status": goal_run_result.status, "gap_item_count": len(gap_items)}
        if learn_report is not None:  # 仅启用时才多一个键，关闭时 payload 与此前一致
            completed_payload["learn"] = learn_report.summary()
        bus.publish(
            Event(
                kind="RuntimeCycleCompleted",
                payload=completed_payload,
                actor="runtime.AgentRuntime",
                correlation_id=correlation_id,
            )
        )

        return AgentRuntimeResult(
            status=goal_run_result.status,
            rounds_used=goal_run_result.rounds_used,
            compacts_done=goal_run_result.compacts_done,
            final_report=goal_run_result.final_report,
            goal_spec=goal_run_result.goal_spec,
            replan_proposal=goal_run_result.replan_proposal,
            gap=gap_items,
            correlation_id=correlation_id,
            goal_run_result=goal_run_result,
            learn_report=learn_report,
        )
