"""
goal_mode/runner.py — GoalRunner：Goal 模式的外层驱动循环

流程（对应设计方案第四节）：

  loop（安全阀：max_rounds / max_total_compacts / consecutive_same_feedback_limit）：
    1. 组装 prompt：目标 + 验收标准（首轮）或上一轮反馈（后续轮）
    2. executor.execute(agent, prompt) —— 粗粒度：一次完整 run_turn
    3. 若 step.hit_max_turns → 显式 compact，回到 1（不消耗 max_rounds 预算，
       因为这一步还没跑出可评审的"完成态"，不算作真正的一轮）
    4. 否则调用 GoalJudge 评审：
         DONE          → 结束，返回成功
         CONTINUE      → 反馈注入历史，回到 1（消耗一轮 max_rounds 预算）
         NEED_COMPACT  → 显式 compact，回到 1（不消耗 max_rounds 预算）
    5. 每个轮次边界都落盘 GoalState（若 persist_state=True）

说明：token 阈值 / 轮次计数 / 工具调用计数等常规 compact 触发，agent.py 的
`_agentic_loop` 内部已经通过 CompositeTrigger 每次 LLM 调用前自动检查并处理
（见 agent.py `_maybe_run_compact` 调用点），GoalRunner 不重复实现这部分。
GoalRunner 只需要兜底处理两种"常规触发器没接住"的情况：
  a) 撞到 cfg.max_turns 硬顶（_agentic_loop 内部循环耗尽，不是 compact 能解决的，
     需要先 compact 腾出空间再继续）
  b) GoalJudge 主观判断怀疑历史信息干扰了主 Agent 的判断力（NEED_COMPACT）
"""

from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

import mini_agent.ui.renderer as R

from .spec import GoalSpec
from .executor import GoalStepExecutor, CoarseStepExecutor, GoalStepResult
from .state import GoalState, GoalStateStore

if TYPE_CHECKING:
    from mini_agent.agent import Agent
    from mini_agent.config import AppConfig


@dataclass
class GoalRunResult:
    """GoalRunner.run() 的最终结果。"""
    status: str            # done | max_rounds_exhausted | stuck | cancelled | failed
    rounds_used: int
    compacts_done: int
    final_report: str
    goal_spec: GoalSpec


class GoalRunner:
    """Goal 模式外层驱动器。"""

    def __init__(
        self,
        agent: "Agent",
        cfg: "AppConfig",
        goal_spec: GoalSpec,
        executor: Optional[GoalStepExecutor] = None,
        state_store: Optional[GoalStateStore] = None,
        resume_state: Optional[GoalState] = None,
    ) -> None:
        if not goal_spec.confirmed:
            raise ValueError("GoalSpec 尚未确认（confirmed=False），不能开始执行。请先完成验收标准协商流程。")

        self._agent = agent
        self._cfg = cfg
        self._spec = goal_spec
        self._executor = executor or CoarseStepExecutor()
        self._gm_cfg = cfg.goal_mode

        self._state_store = state_store
        if self._state_store is None and self._gm_cfg.persist_state:
            from mini_agent.storage.paths import AgentPaths
            paths = AgentPaths(project_root=cfg.project_root)
            sid = agent.session_id or "unknown"
            self._state_store = GoalStateStore(paths, sid)

        # 恢复态：从上一次中断处继续
        if resume_state is not None:
            self._round = resume_state.round
            self._last_feedback = resume_state.last_judge_feedback
            self._compacts_done = resume_state.compacts_done
            self._consecutive_same = resume_state.consecutive_same_feedback
        else:
            self._round = 0
            self._last_feedback = ""
            self._compacts_done = 0
            self._consecutive_same = 0

        self._prior_feedback_for_similarity: list[str] = []

    # ── 主循环 ────────────────────────────────────────────────────────────

    def run(self) -> GoalRunResult:
        self._pin_goal_context()
        self._save_state(status="running")

        max_rounds = self._gm_cfg.max_rounds
        max_compacts = self._gm_cfg.max_total_compacts

        while self._round < max_rounds:
            prompt = self._build_prompt()

            R.print_info(
                f"[GoalRunner] 第 {self._round + 1}/{max_rounds} 轮执行中…"
            )
            step = self._executor.execute(self._agent, prompt)

            # [兜底] agent.py 内部的 auto-compact（CompositeTrigger 命中，
            # 发生在本步 run_turn 执行过程中）可能已经把上一轮钉住的
            # goal_context 压缩掉了（取决于所用压缩策略）。不管本步是否
            # 显式触发了 compact，每轮结束都无条件重新钉一次，成本很低
            # （一条 user 消息），但能确保目标信息永远不会真的丢失。
            self._pin_goal_context()

            if step.hit_max_turns:
                R.print_warning(
                    "[GoalRunner] 本步撞到 max_turns 硬顶，尚未产出可评审的完成态，"
                    "先压缩历史再继续（不计入轮次预算）。"
                )
                if self._compacts_done >= max_compacts:
                    return self._finish(
                        status="max_rounds_exhausted",
                        report=(
                            f"已达到最大 compact 次数上限（{max_compacts}），"
                            "且本步仍未产出可评审结果，提前终止。"
                        ),
                    )
                self._do_compact()
                continue  # 不计入 self._round，重新跑这一步

            judge_status, judge_feedback = self._run_judge(step.output)

            if judge_status == "DONE":
                return self._finish(status="done", report=judge_feedback)

            if judge_status == "NEED_COMPACT":
                R.print_info("[GoalRunner] GoalJudge 建议先压缩历史再继续。")
                if self._compacts_done >= max_compacts:
                    return self._finish(
                        status="max_rounds_exhausted",
                        report=f"已达到最大 compact 次数上限（{max_compacts}）。",
                    )
                self._do_compact()
                continue  # 不计入轮次

            # CONTINUE（或判定异常时的保守兜底）
            self._last_feedback = judge_feedback
            self._round += 1
            self._save_state(status="running")

            if self._check_stuck(judge_feedback):
                return self._finish(
                    status="stuck",
                    report=(
                        f"连续 {self._gm_cfg.consecutive_same_feedback_limit} 轮收到高度相似的反馈，"
                        f"怀疑卡在同一个问题上，提前终止。最近一次反馈：\n{judge_feedback}"
                    ),
                )

        return self._finish(
            status="max_rounds_exhausted",
            report=(
                f"已达到最大轮次上限（{max_rounds}），目标尚未达成。"
                f"最后一轮反馈：\n{self._last_feedback}"
            ),
        )

    # ── 内部：prompt 组装 ────────────────────────────────────────────────

    def _build_prompt(self) -> str:
        if self._round == 0 and not self._last_feedback:
            return (
                f"{self._spec.render_context_block()}\n\n"
                "请开始尝试完成这个目标。"
            )
        return (
            "请根据上一轮的核查反馈继续推进目标：\n\n"
            f"{self._last_feedback}\n\n"
            f"{self._spec.render_context_block()}"
        )

    # ── 内部：GoalJudge 调用 ─────────────────────────────────────────────

    def _run_judge(self, agent_output: str) -> tuple[str, str]:
        from mini_agent.role_agents.goal_judge import run_goal_judge
        from mini_agent.role_agents.feedback import extract_goal_status
        from mini_agent.orchestrator.agent_profiles import AgentProfile

        profile = AgentProfile(
            name="goal_judge",
            role_type="goal_judge",
            model=self._gm_cfg.judge_model,
            provider=self._gm_cfg.judge_provider,
            tools=list(self._gm_cfg.judge_allowed_tools) if self._gm_cfg.judge_tools_enabled else [],
            tool_groups=list(self._gm_cfg.judge_allowed_tool_groups) if self._gm_cfg.judge_tools_enabled else [],
        )

        raw = run_goal_judge(
            profile=profile,
            base_cfg=self._cfg,
            goal_spec=self._spec,
            agent_output=agent_output,
            round_no=self._round + 1,
            prior_feedback=self._last_feedback,
        )

        status = extract_goal_status(raw) or "CONTINUE"  # 提取失败时保守按 CONTINUE 处理

        # 注入判定反馈到主 Agent 历史（带 _type=role_agent，与现有 role agent 反馈一致）
        from mini_agent.role_agents.feedback import RoleFeedback, build_inject_message
        from mini_agent.history.entry import HType
        feedback_obj = RoleFeedback(
            role_name="goal_judge",
            role_type="goal_judge",
            raw_output=raw,
            goal_status=status,
            inject_as="user",
        )
        inject_msg = build_inject_message(feedback_obj)
        inject_typed = dict(inject_msg, _type=HType.ROLE_AGENT)
        self._agent._hist.append_raw_dict(inject_typed)

        R.print_info(f"[GoalRunner] GoalJudge 判定：{status}")
        return status, raw

    # ── 内部：卡住检测（连续反馈高度雷同）──────────────────────────────────

    def _check_stuck(self, feedback: str) -> bool:
        limit = self._gm_cfg.consecutive_same_feedback_limit
        threshold = self._gm_cfg.same_feedback_similarity_threshold

        if self._prior_feedback_for_similarity:
            prev = self._prior_feedback_for_similarity[-1]
            ratio = difflib.SequenceMatcher(None, prev, feedback).ratio()
            if ratio >= threshold:
                self._consecutive_same += 1
            else:
                self._consecutive_same = 0
        self._prior_feedback_for_similarity.append(feedback)

        return self._consecutive_same >= (limit - 1)

    # ── 内部：compact ────────────────────────────────────────────────────

    def _do_compact(self) -> None:
        try:
            self._agent.compact_with_skills()
        except Exception as e:
            R.print_error(f"[GoalRunner] compact 失败：{e}")
        self._compacts_done += 1
        self._pin_goal_context()
        self._save_state(status="running")

    def _pin_goal_context(self) -> None:
        """把目标+验收标准作为一条"钉住"消息重新附加到历史末尾，
        防止 compact 之后被摘要冲淡或丢失。"""
        from mini_agent.history.entry import make_goal_context
        self._agent._hist.append_raw_dict(make_goal_context(self._spec.render_context_block()))

    # ── 内部：状态持久化 ─────────────────────────────────────────────────

    def _save_state(self, status: str, final_report: str = "") -> None:
        if self._state_store is None:
            return
        state = GoalState(
            status=status,
            session_id=self._agent.session_id or "",
            goal_spec=self._spec.to_dict(),
            round=self._round,
            last_judge_feedback=self._last_feedback,
            last_judge_status="",
            compacts_done=self._compacts_done,
            consecutive_same_feedback=self._consecutive_same,
            final_report=final_report,
        )
        try:
            self._state_store.save(state)
        except Exception as e:
            R.print_warning(f"[GoalRunner] 状态落盘失败（不影响本轮执行）：{e}")

    def _finish(self, status: str, report: str) -> GoalRunResult:
        persisted_status = {
            "done": "done",
            "max_rounds_exhausted": "failed",
            "stuck": "failed",
        }.get(status, "failed")
        self._save_state(status=persisted_status, final_report=report)

        if status == "done":
            R.print_success(f"[GoalRunner] 目标已达成（共 {self._round} 轮）。")
        else:
            R.print_warning(f"[GoalRunner] 目标未达成（状态：{status}）：{report}")

        return GoalRunResult(
            status=status,
            rounds_used=self._round,
            compacts_done=self._compacts_done,
            final_report=report,
            goal_spec=self._spec,
        )

    def cancel(self) -> None:
        """用户主动取消（供 CLI 中断处理调用）。"""
        self._save_state(status="cancelled", final_report="用户主动取消。")
