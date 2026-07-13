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
    5. CONTINUE 分支里还会检测"是否卡住"（连续 N 轮反馈高度相似）：
         判定卡住后不直接终止——先花一次"卡住恢复额度"（max_stuck_recoveries）
         压缩历史 + 注入"换个思路重新尝试"的提示，回到 1 继续跑；只有恢复额度
         或 compact 预算耗尽后再次卡住，才真正终止（status=stuck）。这是因为
         "反复给出雷同反馈"往往是历史里堆积了太多噪音干扰了 agent 的判断，
         而不是目标真的做不到，压缩+提示给它一次换角度重来的机会往往能破局。
    6. 每个轮次边界都落盘 GoalState（若 persist_state=True）

说明：token 阈值 / 轮次计数 / 工具调用计数等常规 compact 触发，agent.py 的
`_agentic_loop` 内部已经通过 CompositeTrigger 每次 LLM 调用前自动检查并处理
（见 agent.py `_maybe_run_compact` 调用点），GoalRunner 不重复实现这部分。
GoalRunner 只需要兜底处理三种"常规触发器没接住"的情况：
  a) 撞到 cfg.max_turns 硬顶（_agentic_loop 内部循环耗尽，不是 compact 能解决的，
     需要先 compact 腾出空间再继续）
  b) GoalJudge 主观判断怀疑历史信息干扰了主 Agent 的判断力（NEED_COMPACT）
  c) 连续多轮反馈高度雷同、疑似卡住（见上方第 5 点）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

import mini_agent.ui.renderer as R

from .spec import GoalSpec
from .executor import GoalStepExecutor, CoarseStepExecutor, GoalStepResult
from .state import GoalState, GoalStateStore
from mini_agent.role_agents.stuck_detector import StuckDetector, StuckSignal

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

        self._last_stuck_signal: StuckSignal = StuckSignal.NONE
        self._stuck_detector = StuckDetector(
            similarity_threshold=self._gm_cfg.same_feedback_similarity_threshold,
            consecutive_limit=self._gm_cfg.consecutive_same_feedback_limit,
            max_recoveries=self._gm_cfg.max_stuck_recoveries,
        )

        # 恢复态：从上一次中断处继续
        if resume_state is not None:
            self._round = resume_state.round
            self._last_feedback = resume_state.last_judge_feedback
            self._compacts_done = resume_state.compacts_done
            self._stuck_detector.load_counts(
                consecutive_same=resume_state.consecutive_same_feedback,
                recoveries_used=resume_state.stuck_recoveries_used,
            )
        else:
            self._round = 0
            self._last_feedback = ""
            self._compacts_done = 0

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
                if self._try_stuck_recovery():
                    continue  # 已压缩历史+重置卡住计数，本轮判定不终止，继续跑
                return self._finish(
                    status="stuck",
                    report=(
                        f"连续 {self._gm_cfg.consecutive_same_feedback_limit} 轮收到高度相似的反馈，"
                        f"且已用尽 {self._gm_cfg.max_stuck_recoveries} 次压缩历史重试的机会，"
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
        from mini_agent.role_agents.goal_judge import run_goal_judge, build_goal_judge_prompt
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

        if self._gm_cfg.judge_show_prompt:
            # [调试开关] 打印发给 GoalJudge 的完整输入 prompt，方便排查判定依据
            # （比如怀疑 GoalJudge 判定不准，先看看它到底收到了什么上下文）
            prompt_preview = build_goal_judge_prompt(
                goal_spec=self._spec,
                agent_output=agent_output,
                round_no=self._round + 1,
                prior_feedback=self._last_feedback,
            )
            R.console.print()
            R.console.print("[bold]— GoalJudge 输入 Prompt —[/bold]")
            R.console.print(prompt_preview)
            R.console.print()

        raw = run_goal_judge(
            profile=profile,
            base_cfg=self._cfg,
            goal_spec=self._spec,
            agent_output=agent_output,
            round_no=self._round + 1,
            prior_feedback=self._last_feedback,
        )

        status = extract_goal_status(raw) or "CONTINUE"  # 提取失败时保守按 CONTINUE 处理

        # [Phase 5] GoalJudge 现在约定输出结构化 JSON（见 role_agents/verdict.py）。
        # 展示层/注入历史时优先用解析出的 `feedback` 字段（干净的人类可读文本），
        # 而不是原始 JSON 字符串；JSON 解析失败时（如尚未升级的自定义 profile、
        # 或历史遗留纯文本格式）回退到原始文本，行为与升级前完全一致。
        from mini_agent.role_agents.verdict import parse_judge_verdict
        _verdict = parse_judge_verdict(
            raw, valid_statuses=["DONE", "CONTINUE", "NEED_COMPACT"], fallback_status=status,
        )
        display_text = _verdict.feedback if (_verdict.parse_ok and _verdict.feedback) else raw

        # 注入判定反馈到主 Agent 历史（带 _type=role_agent，与现有 role agent 反馈一致）
        from mini_agent.role_agents.feedback import RoleFeedback, build_inject_message
        try:
            from mini_agent.history.entry import HType
            role_agent_type = HType.ROLE_AGENT
        except (ImportError, AttributeError):
            role_agent_type = "role_agent"
        feedback_obj = RoleFeedback(
            role_name="goal_judge",
            role_type="goal_judge",
            raw_output=display_text,
            goal_status=status,
            inject_as="user",
        )
        inject_msg = build_inject_message(feedback_obj)
        inject_typed = dict(inject_msg, _type=role_agent_type)
        self._agent._hist.append_raw_dict(inject_typed)

        # [显示改进] 把 GoalJudge 的完整核查内容打印出来，而不只是一行状态关键字，
        # 方便用户看到具体核查了哪些标准、依据是什么、CONTINUE 时的具体反馈是什么。
        from mini_agent.role_agents.feedback import format_feedback
        R.console.print()
        R.console.print(format_feedback(feedback_obj))
        R.console.print()

        return status, display_text

    # ── 内部：卡住检测（连续反馈高度雷同）──────────────────────────────────

    def _check_stuck(self, feedback: str) -> bool:
        """观察本轮反馈，返回是否已判定为"卡住"（供 run() 决定是否尝试恢复）。

        内部状态（相似度比较基准、连续雷同计数、恢复额度）全部委托给
        `StuckDetector`，与 TurnJudge 共享同一套实现（见
        role_agents/stuck_detector.py）。"""
        self._last_stuck_signal = self._stuck_detector.observe(feedback)
        return self._last_stuck_signal is not StuckSignal.NONE

    def _try_stuck_recovery(self) -> bool:
        """
        判定"卡住"后的第一反应不应该是直接认输——很可能只是主 Agent 的历史里
        堆积了太多无关信息、干扰了它看清问题本质，压缩一次历史、显式提醒它
        换个角度重新梳理，往往就能推进。这里做的事：

          1. 检查还有没有"卡住恢复"额度（max_stuck_recoveries）以及
             compact 总次数预算（max_total_compacts），任一用尽就不再尝试，
             交回调用方走正常的"卡住终止"流程。
          2. 执行一次 compact（复用 _do_compact，会自动重新钉住目标上下文）。
          3. 显式注入一条提示，点明"连续多轮反馈雷同、疑似卡在同一个问题上"，
             要求 agent 换一个思路/方法重新尝试，而不是重复同样的动作。
          4. 重置卡住计数（但不重置 prior_feedback 历史用于后续相似度比较的
             基准——下一轮反馈依然会和"压缩前最后一次反馈"比较相似度，这样
             如果压缩后还是给出高度相似的反馈，能被正确地识别为"真的卡住了"）。

        返回 True 表示已经处理（应该 continue 继续跑，不计入 max_rounds 预算），
        False 表示恢复额度或 compact 预算已耗尽，调用方应该走正常终止流程。

        "是否还有恢复额度"由 `StuckDetector.observe()`（在 `_check_stuck` 里
        已经调用过）决定，这里读取上次 observe() 返回的 signal；
        compact 总次数预算是 GoalRunner 独有的额外约束（TurnJudge 场景没有
        这个预算概念），单独检查。
        """
        if self._last_stuck_signal is StuckSignal.GIVE_UP:
            return False

        max_compacts = self._gm_cfg.max_total_compacts
        if self._compacts_done >= max_compacts:
            return False

        R.print_warning(
            f"[GoalRunner] 连续 {self._gm_cfg.consecutive_same_feedback_limit} 轮反馈高度相似，疑似卡住，"
            f"尝试压缩历史后给 agent 一次重新整理思路的机会"
            f"（第 {self._stuck_detector.recoveries_used}/{self._gm_cfg.max_stuck_recoveries} 次恢复额度）。"
        )
        self._do_compact()

        from ._compat import make_goal_context
        hint = (
            "[GoalRunner 提示] 你最近连续几轮的输出/反馈高度相似，似乎卡在同一个"
            "问题上反复尝试同样的方法却没有新进展。历史已经压缩过，请不要重复"
            "上一轮的做法——先重新梳理一下目前的障碍到底是什么，考虑换一个角度、"
            "换一种工具或方法，或者先做一些诊断性的检查（比如确认前提假设是否"
            "成立）来找到卡住的真正原因，再继续推进目标。"
        )
        self._agent._hist.append_raw_dict(make_goal_context(hint))

        self._save_state(status="running")
        return True

    # ── 内部：compact ────────────────────────────────────────────────────

    def _do_compact(self) -> None:
        R.print_info("[GoalRunner] 正在压缩历史…")
        summary = ""
        try:
            summary = self._agent.compact_with_skills()
        except Exception as e:
            R.print_error(f"[GoalRunner] compact 失败：{e}")
        else:
            if summary:
                R.console.print()
                R.console.print("[bold]— Compact 摘要 —[/bold]")
                R.console.print(summary)
                R.console.print()
                R.print_success(f"[GoalRunner] compact 完成（第 {self._compacts_done + 1} 次）。")
            else:
                R.print_warning("[GoalRunner] compact 完成，但没有生成摘要文本（历史可能为空）。")
        self._compacts_done += 1
        self._pin_goal_context()
        self._save_state(status="running")

    def _pin_goal_context(self) -> None:
        """把目标+验收标准作为一条"钉住"消息重新附加到历史末尾，
        防止 compact 之后被摘要冲淡或丢失。"""
        from ._compat import make_goal_context
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
            consecutive_same_feedback=self._stuck_detector.consecutive_same,
            stuck_recoveries_used=self._stuck_detector.recoveries_used,
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

    def pause(self) -> None:
        """用户按 Ctrl-C 中断（供 CLI 中断处理调用）。

        与 `cancel()` 的关键区别：中断的真实意图通常是"先停一下，之后还想继续"，
        不是"放弃这个目标"。所以这里保持 status="running"（和轮次边界正常保存
        的状态一致），这样 `/goal resume` 才能找到它。如果调用了 `cancel()`
        把状态改成 "cancelled"，`find_resumable_session()` / `/goal resume`
        就会认为这个 goal 已经主动放弃，反而无法恢复——这是不对的。
        """
        self._save_state(status="running")

    def cancel(self) -> None:
        """用户通过 `/goal cancel` 主动放弃（不是 Ctrl-C 中断）。"""
        self._save_state(status="cancelled", final_report="用户主动取消。")
