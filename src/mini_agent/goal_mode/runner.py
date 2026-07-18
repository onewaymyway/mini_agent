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

import re
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

import mini_agent.ui.renderer as R

from .spec import GoalSpec
from .executor import GoalStepExecutor, CoarseStepExecutor, GoalStepResult
from .state import GoalState, GoalStateStore
from mini_agent.role_agents.stuck_detector import StuckDetector, StuckSignal, ProgressTracker, GoalPhase

if TYPE_CHECKING:
    from mini_agent.agent import Agent
    from mini_agent.config import AppConfig


# [goal_mode_stuck_compact_plan.md §5] 主 Agent 提出重规划提议时约定用一个
# ```replan_proposal 代码块包裹（见 prompts/fragments/goal_mode.md
# REPLAN_PROPOSAL_REQUEST_BLOCK），block 内是一段 JSON，容忍围栏内有多余空白。
_REPLAN_PROPOSAL_BLOCK_RE = re.compile(r"```replan_proposal\s*(\{.*?\})\s*```", re.DOTALL)


def render_replan_proposal(proposal: dict) -> str:
    """把 replan_proposal 字典渲染成人类可读文本，供终止报告 / CLI `/goal
    revise` 展示，以及"auto"档位自动应用时作为 GoalSpecBuilder.revise() 的
    user_feedback 起点使用。是模块级函数（不依赖 GoalRunner 实例），方便
    goal_mode_cmd.py 在独立于本次 run() 的场景（比如恢复后的 `/goal
    revise`）里复用同一份渲染逻辑。"""
    if not proposal:
        return ""
    lines = ["Agent 在多次卡住恢复后提出了以下重规划建议："]
    split = proposal.get("suggested_split")
    if split:
        lines.append("建议拆分为子目标：")
        for s in (split if isinstance(split, list) else [split]):
            lines.append(f"  - {s}")
    changes = proposal.get("suggested_criteria_changes")
    if changes:
        lines.append("建议调整验收标准：")
        for c in (changes if isinstance(changes, list) else [changes]):
            lines.append(f"  - {c}")
    reason = proposal.get("reason")
    if reason:
        lines.append(f"理由：{reason}")
    if len(lines) == 1:
        lines.append("（提议内容为空，仅有一份不完整的标记）")
    return "\n".join(lines)


@dataclass
class GoalRunResult:
    """GoalRunner.run() 的最终结果。"""
    status: str            # done | max_rounds_exhausted | stuck | cancelled | failed
    rounds_used: int
    compacts_done: int
    final_report: str
    goal_spec: GoalSpec
    # [goal_mode_stuck_compact_plan.md §5] 若 cfg.goal_mode.replan_proposal_mode
    # != "off" 且本次运行中 agent 提出过重规划提议，这里携带最近一次的提议
    # 字典（{"suggested_split": [...], "suggested_criteria_changes": [...],
    # "reason": str}）；"auto" 档位已经自动应用过的提议不会再出现在这里
    # （应用后会被清空）。其余情况恒为 None。
    replan_proposal: Optional[dict] = None


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

        # [判官接线统一 阶段六] goal_mode.enabled=True 但 "goal_judge" 被
        # role_agent.block 屏蔽（或磁盘自定义 profile 的 trigger_on 被改坏）
        # 时，dispatcher.get_goal_review_roles() 会返回空列表——这种组合本身
        # 就是自相矛盾的配置（开了 Goal 模式却把唯一的验收判官拉黑）。与其
        # 运行时静默降级成某个可能出乎意料的行为（永远 CONTINUE 跑满
        # max_rounds，或悄悄当作 DONE 绕过验收），这里选择启动时就明确报错，
        # 让用户自己决定到底想要哪种（对应设计文档 §8 开放问题 3 的方案 c）。
        #
        # 注意区分两种"拿不到 goal_review 判官"的情况：
        #   - dispatcher 存在（app.py 正常走过 init_role_agent_system），但
        #     goal_review_roles 为空 → 真的是配置自相矛盾（block 掉了），报错。
        #   - dispatcher 是 None（全局单例从未被初始化，例如脱离 app.py 独立
        #     构造 GoalRunner 的场景，如测试用例）→ 不视为"配置错误"，
        #     保持与升级前完全一致的行为：不报错，_run_judge 里会 fallback
        #     到现场拼装 profile（见下方 _run_judge 的对应处理）。
        from mini_agent.role_agents import get_dispatcher
        _dispatcher = get_dispatcher()
        if _dispatcher is not None and not _dispatcher.get_goal_review_roles():
            raise ValueError(
                "cfg.goal_mode.enabled=True，但 \"goal_judge\" 已被 role_agent.block 屏蔽，"
                "导致没有任何可用的 goal_review 判官。Goal 模式没有验收判官会导致无法核查"
                "是否完成，请检查配置：从 role_agent.block 中移除 \"goal_judge\"，"
                "或关闭 goal_mode.enabled。"
            )

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

        # [改造项三] 验收标准逐条状态追踪的初始快照：全部未通过、尚无依据。
        self._criteria_status: list[dict] = [
            {"index": i + 1, "text": c, "passed": False, "evidence": "", "last_updated_round": 0}
            for i, c in enumerate(goal_spec.acceptance_criteria)
        ]
        # [改造项二] 最近几轮 progress/progress_reason 记录，供卡住恢复时拼装
        # "已尝试路径清单"。上限固定取 consecutive_same_feedback_limit（正好
        # 覆盖一次"卡住判定周期"涉及的轮次），避免无界增长。
        self._recent_progress_reasons: list[dict] = []
        self._progress_reasons_cap = max(3, self._gm_cfg.consecutive_same_feedback_limit)

        # [goal_mode_stuck_compact_plan.md §1.2] Dead-end 持久清单：只增不减
        # （去重后），不随 _recent_progress_reasons 的滚动窗口被冲掉。
        self._dead_ends: list[dict] = []

        # [goal_mode_stuck_compact_plan.md §3.1] 进展分数追踪状态。
        self._last_passed_count: int = 0
        self._progress_scores: list[float] = []
        self._progress_scores_cap = max(10, self._gm_cfg.consecutive_same_feedback_limit * 3)

        # [goal_mode_stuck_compact_plan.md §3.2] 伪进展趋势识别：ProgressTracker
        # 自身不需要独立落盘字段，其全部状态就是"最近 window 个进展分数"，
        # resume 时直接从 self._progress_scores（已经持久化）末尾 window 个
        # 重建（见下方 resume_state 分支），不需要额外持久化。
        self._progress_tracker = ProgressTracker(
            window=getattr(self._gm_cfg, "pseudo_progress_window", 5),
            stagnation_score_threshold=getattr(self._gm_cfg, "pseudo_progress_stagnation_threshold", 0.15),
            max_score_cap=getattr(self._gm_cfg, "pseudo_progress_max_score_cap", 0.5),
        )
        # 记录本轮触发 stuck 的来源（"similarity" 规则化相似度判定 / LLM 语义判定，
        # 还是 "pseudo_progress" §3.2 伪进展趋势），仅用于终止报告文案更准确，
        # 不影响判定逻辑本身。
        self._last_stuck_reason: str = "similarity"

        # [goal_mode_stuck_compact_plan.md §4] 探索/收敛阶段追踪。默认关闭
        # （proactive_compact_enabled=False）时这套状态仍会被计算但不产生任何
        # 额外动作，方便用户先观察 self._phase 的变化再决定是否开启。
        self._phase: GoalPhase = GoalPhase.EXPLORING
        self._consecutive_positive_rounds: int = 0
        self._last_proactive_compact_round: int = 0

        # [goal_mode_stuck_compact_plan.md §5] Goal 重规划提议状态。
        # _awaiting_replan_proposal：本轮是否刚刚在提示里请求过提议（只在
        # "最后一次恢复机会"这一轮为 True，下一步 executor 产出后立即消费
        # 并复位，不会跨轮持续等待）。
        # _replan_proposal：最近一次解析到的非空提议（"confirm" 档位下终止
        # 时展示给用户；"auto" 档位自动应用后会被清空）。
        # _replan_auto_applied：每次 run() 最多只允许自动应用一次重规划，
        # 防止变成"每次卡住都自动放宽标准"的隐蔽漏洞。
        self._awaiting_replan_proposal: bool = False
        self._replan_proposal: Optional[dict] = None
        self._replan_auto_applied: bool = False

        # [恢复时的轮次/compact 预算追加] 详见下方 resume_state 分支：
        # 只有从 max_rounds_exhausted 恢复时才会被赋值，其余情况保持 None，
        # run() 里 fallback 到 self._gm_cfg 的原始上限。
        self._max_rounds_override: Optional[int] = None
        self._max_compacts_override: Optional[int] = None

        # 恢复态：从上一次中断处继续
        if resume_state is not None:
            self._round = resume_state.round
            self._last_feedback = resume_state.last_judge_feedback
            self._compacts_done = resume_state.compacts_done
            # [BUGFIX] 只有从 status=="running" 正常中断（Ctrl-C/进程被杀）恢复时，
            # 才应该原样带回卡住计数——那种情况下恢复额度确实还没耗尽。但如果
            # 这个 goal 是因为 status=="stuck" 才落盘的（意味着当时
            # recoveries_used 已经 >= max_stuck_recoveries，是"已用尽额度后
            # 主动终止"），此时走 /goal resume --force 恢复，如果原样带回这份
            # "已耗尽"的计数，第一次 _check_stuck 命中相似反馈就会立刻再次
            # GIVE_UP、直接终止——用户表现为"resume 之后完全无法继续"。
            # 这里对 stuck 恢复的场景重置一次计数，给它一份全新的恢复额度，
            # 相当于"人工介入后重新开始观察是否还卡住"，而不是延续一个已经
            # 判定过"救不回来"的状态。
            if resume_state.status == "stuck":
                self._stuck_detector.load_counts(consecutive_same=0, recoveries_used=0)
                R.print_info(
                    "[GoalRunner] 检测到从 stuck 状态恢复，已重置卡住检测计数"
                    "（重新给予一份完整的卡住恢复额度）。"
                )
            else:
                self._stuck_detector.load_counts(
                    consecutive_same=resume_state.consecutive_same_feedback,
                    recoveries_used=resume_state.stuck_recoveries_used,
                )
            if resume_state.criteria_status:
                self._criteria_status = list(resume_state.criteria_status)
            if resume_state.recent_progress_reasons:
                self._recent_progress_reasons = list(resume_state.recent_progress_reasons)
            if resume_state.dead_ends:
                self._dead_ends = list(resume_state.dead_ends)
            self._last_passed_count = int(resume_state.last_passed_count or 0)
            if resume_state.progress_scores:
                self._progress_scores = list(resume_state.progress_scores)
                self._progress_tracker.replay(self._progress_scores)
                # [goal_mode_stuck_compact_plan.md §4] 用历史分数序列重建
                # 探索/收敛阶段，不需要额外的落盘字段（原理同 ProgressTracker）。
                for s in self._progress_scores:
                    self._update_goal_phase({"progress_score": s})
                # 避免恢复后立刻被判定为"已经到了该主动 compact 的轮次"，
                # 把基准对齐到恢复时的轮次。
                self._last_proactive_compact_round = self._round
            if getattr(resume_state, "replan_proposal", None):
                self._replan_proposal = dict(resume_state.replan_proposal)

            # [BUGFIX] 恢复一个 status=="max_rounds_exhausted" 的 goal（必然是
            # /goal resume --force，因为非 running 状态默认不允许恢复）时，
            # self._round / self._compacts_done 会原样带回"已经耗尽"的计数，
            # 而 run() 里的循环上限只读 self._gm_cfg.max_rounds / max_total_compacts
            # 这两个"全局配置值"——resume 之后第一次判断
            # `self._round < max_rounds` 就已经是 False，一轮都不会跑，立刻
            # 又落回同一个 max_rounds_exhausted，用户表现为"resume 之后立刻
            # 又停了"，完全没有意义。这里给它追加一整份全新预算（在原本已用
            # 掉的基础上，再给一次 max_rounds/max_total_compacts 的量），
            # 效果上等价于 stuck 恢复时"重置卡住计数、给一份全新恢复额度"的
            # 处理思路——人工介入 --force 恢复本身就代表"我知道之前额度用完
            # 了，我要它接着跑"，不追加预算的话这个操作没有意义。
            if resume_state.status == "max_rounds_exhausted":
                self._max_rounds_override = resume_state.round + self._gm_cfg.max_rounds
                self._max_compacts_override = resume_state.compacts_done + self._gm_cfg.max_total_compacts
                R.print_info(
                    "[GoalRunner] 检测到从 max_rounds_exhausted 状态恢复，"
                    f"已追加一份全新预算：轮次上限 {self._gm_cfg.max_rounds} → "
                    f"{self._max_rounds_override}，compact 上限 "
                    f"{self._gm_cfg.max_total_compacts} → {self._max_compacts_override}"
                    "（否则恢复后会因预算已耗尽立即再次终止）。"
                )
        else:
            self._round = 0
            self._last_feedback = ""
            self._compacts_done = 0

    # ── 主循环 ────────────────────────────────────────────────────────────

    def run(self) -> GoalRunResult:
        self._pin_goal_context()
        self._save_state(status="running")

        # 正常从 0 开始时用全局配置；从 max_rounds_exhausted 恢复时用
        # __init__ 里追加过的更高上限（见上方 resume_state 分支的说明）。
        max_rounds = self._max_rounds_override or self._gm_cfg.max_rounds
        max_compacts = self._max_compacts_override or self._gm_cfg.max_total_compacts

        while self._round < max_rounds:
            prompt = self._build_prompt()

            R.print_info(
                f"[GoalRunner] 第 {self._round + 1}/{max_rounds} 轮执行中…"
            )
            step = self._executor.execute(self._agent, prompt)

            # [goal_mode_stuck_compact_plan.md §5] 如果上一轮的提示里刚刚
            # 请求过重规划提议（只会发生在"最后一次恢复机会"那一轮），
            # 立即从本轮输出里消费一次，不管有没有解析到，都复位等待标记，
            # 保证"只在耗尽额度前提议一次"，不会跨轮持续询问。
            if self._awaiting_replan_proposal:
                self._awaiting_replan_proposal = False
                proposal = self._extract_replan_proposal(step.output)
                if proposal:
                    self._replan_proposal = proposal
                    R.print_info("[GoalRunner] 收到 Agent 提出的重规划提议。")
                    replan_mode = getattr(self._gm_cfg, "replan_proposal_mode", "off")
                    if replan_mode == "auto" and not self._replan_auto_applied:
                        if self._try_auto_apply_replan(proposal):
                            continue  # 已应用新目标并重新钉住上下文，直接进入下一轮

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

            verification_result = self._run_verification_command()
            judge_status, judge_feedback, progress_info = self._run_judge(
                step.output, verification_result=verification_result,
            )

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
                # [goal_mode_stuck_compact_plan.md §1.3] 判官主动建议 compact 时
                # 也应该受益于已积累的 dead-end 信息，不需要非得先经历一次
                # StuckDetector 判定才能用上。
                dead_ends_block = self._render_dead_ends_block()
                if dead_ends_block:
                    from ._compat import make_goal_context
                    self._agent._hist.append_raw_dict(make_goal_context(dead_ends_block))
                continue  # 不计入轮次

            # CONTINUE（或判定异常时的保守兜底）
            self._last_feedback = judge_feedback
            self._round += 1
            self._save_state(status="running")

            # [goal_mode_stuck_compact_plan.md §4] 更新探索/收敛阶段（计算本身
            # 成本很低，始终执行；是否据此触发主动 compact 由
            # proactive_compact_enabled 控制，默认关闭时只是单纯维护状态，不
            # 产生任何额外动作）。必须在 _check_stuck 之前调用，好让本轮主动
            # compact 判断用到的是"刚更新过的阶段"。
            self._update_goal_phase(progress_info)
            if self._maybe_proactive_compact():
                continue  # 主动 compact 只重新钉住上下文，不计入卡住恢复额度，直接进入下一轮

            if self._check_stuck(judge_feedback, progress_info):
                if self._try_stuck_recovery():
                    continue  # 已压缩历史+重置卡住计数，本轮判定不终止，继续跑
                if self._last_stuck_reason == "pseudo_progress":
                    stuck_reason_text = (
                        f"最近 {getattr(self._gm_cfg, 'pseudo_progress_window', 5)} 轮进展分数"
                        "长期平缓、没有实质累积增长（伪进展）"
                    )
                else:
                    stuck_reason_text = (
                        f"连续 {self._gm_cfg.consecutive_same_feedback_limit} 轮收到高度相似的反馈"
                    )
                return self._finish(
                    status="stuck",
                    report=(
                        f"{stuck_reason_text}，"
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
        # [goal_mode_stuck_compact_plan.md §2.2 步骤1] 自验证优先：如果设置了
        # verification_command，显式要求主 Agent 在本轮结束前主动执行一次，
        # 并把结果总结进回复——prompt 层面的强约束，成本低，与 GoalRunner
        # 自己的程序化执行（§2.2 步骤2）互补，不冲突。
        verify_hint = ""
        if self._spec.verification_command:
            verify_hint = (
                "\n\n【自验证要求】在结束本轮回复之前，请主动执行一次验证命令：\n"
                f"`{self._spec.verification_command}`\n"
                "并把执行结果（是否通过、关键输出）总结进你的回复，而不是仅凭自己的判断。"
            )
        if self._round == 0 and not self._last_feedback:
            return (
                f"{self._spec.render_context_block()}\n\n"
                "请开始尝试完成这个目标。"
                f"{verify_hint}"
            )
        return (
            "请根据上一轮的核查反馈继续推进目标：\n\n"
            f"{self._last_feedback}\n\n"
            f"{self._spec.render_context_block()}"
            f"{verify_hint}"
        )

    # ── 内部：自验证（程序化执行 verification_command）───────────────────

    def _run_verification_command(self) -> Optional[dict]:
        """[goal_mode_stuck_compact_plan.md §2.2 步骤2] 在送进 GoalJudge 之前，
        GoalRunner 自己（不经过任何 LLM）程序化地执行一次 GoalSpec.verification_command，
        把 {command, returncode, stdout_tail, stderr_tail} 作为客观证据返回，
        供 _run_judge 拼进 judge prompt。

        不满足以下任一条件时返回 None（不影响现有流程，判官依然只能看到
        verification_command 的文本描述）：
          - cfg.goal_mode.auto_verify_enabled 为 False
          - GoalSpec.verification_command 为空
          - 执行过程本身抛出异常（超时、命令不存在等）——此时把异常信息本身
            也作为证据返回（returncode=None），而不是完全静默吞掉，因为
            "验证命令执行失败"本身对判官也是有价值的信息。
        """
        if not getattr(self._gm_cfg, "auto_verify_enabled", True):
            return None
        command = (self._spec.verification_command or "").strip()
        if not command:
            return None

        import subprocess

        timeout = getattr(self._gm_cfg, "auto_verify_timeout", 120)
        tail_lines = getattr(self._gm_cfg, "auto_verify_output_tail_lines", 40)

        def _tail(text: str) -> str:
            lines = text.splitlines()
            if len(lines) > tail_lines:
                lines = lines[-tail_lines:]
            return "\n".join(lines)

        R.print_info(f"[GoalRunner] 自动执行验证命令：{command}")
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(self._cfg.project_root) if getattr(self._cfg, "project_root", None) else None,
                capture_output=True,
                timeout=timeout,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return {
                "command": command,
                "returncode": result.returncode,
                "stdout_tail": _tail(result.stdout or ""),
                "stderr_tail": _tail(result.stderr or ""),
            }
        except subprocess.TimeoutExpired:
            R.print_warning(f"[GoalRunner] 验证命令执行超时（{timeout}s）。")
            return {
                "command": command,
                "returncode": None,
                "stdout_tail": "",
                "stderr_tail": f"[验证命令执行超时，已等待 {timeout} 秒]",
            }
        except Exception as e:
            R.print_warning(f"[GoalRunner] 验证命令执行失败（不影响本轮流程）：{e}")
            return {
                "command": command,
                "returncode": None,
                "stdout_tail": "",
                "stderr_tail": f"[验证命令执行异常：{e}]",
            }

    # ── 内部：GoalJudge 调用 ─────────────────────────────────────────────

    def _run_judge(self, agent_output: str, verification_result: Optional[dict] = None) -> tuple[str, str, dict]:
        from mini_agent.role_agents.goal_judge import run_goal_judge, build_goal_judge_prompt
        from mini_agent.role_agents.feedback import extract_goal_status

        # [改造项一/三] 是否要求 GoalJudge 额外输出 progress/progress_reason/
        # checklist；两者共用同一次扩展 JSON 输出，分别受各自开关控制。
        progress_mode = getattr(self._gm_cfg, "progress_judge_mode", "llm")
        criteria_tracking_enabled = getattr(self._gm_cfg, "criteria_tracking_enabled", True)
        extended_output_enabled = (progress_mode == "llm") or criteria_tracking_enabled

        prior_checklist_lines = ""
        if criteria_tracking_enabled and any(c.get("last_updated_round", 0) > 0 for c in self._criteria_status):
            prior_checklist_lines = "\n".join(
                f"{c['index']}. {c['text']} —— "
                f"{'已通过' if c.get('passed') else '尚未通过'}"
                f"（第 {c.get('last_updated_round', 0)} 轮评审依据：{c.get('evidence') or '无'}）"
                for c in self._criteria_status
            )

        # [判官接线统一 阶段六] profile 不再由 runner.py 现场拼一个临时
        # AgentProfile 对象，而是优先从 dispatcher 的 goal_review 注册表
        # 查询——这样 "goal_judge" 才有一个真实存在的"注册来源"，可以被
        # role_agent.block 屏蔽，也可以被磁盘上的 .agent/agents/goal_judge.md
        # 自定义覆盖（model/system_prompt 等）。
        #
        # dispatcher 为 None（未经过 app.py 的 init_role_agent_system，例如
        # 独立/测试场景直接构造 GoalRunner）时，fallback 到升级前的现场拼装
        # 方式，保持完全向后兼容；__init__ 里已经确保了"dispatcher 存在但
        # 拿不到 goal_judge"这种真正的配置错误不会走到这里。
        from mini_agent.role_agents import get_dispatcher
        _dispatcher = get_dispatcher()
        if _dispatcher is not None:
            goal_review_roles = _dispatcher.get_goal_review_roles()
            profile = goal_review_roles[0]
        else:
            from mini_agent.orchestrator.agent_profiles import AgentProfile
            profile = AgentProfile(
                name="goal_judge",
                role_type="goal_judge",
                trigger_on="goal_review",
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
                prior_checklist_lines=prior_checklist_lines,
                verification_result=verification_result,
            )
            R.console.print()
            R.console.print("[bold]— GoalJudge 输入 Prompt —[/bold]")
            R.console.print(prompt_preview)
            R.console.print()

        process_integrity_enabled = getattr(self._gm_cfg, "process_integrity_check_enabled", True)
        raw = run_goal_judge(
            profile=profile,
            base_cfg=self._cfg,
            goal_spec=self._spec,
            agent_output=agent_output,
            round_no=self._round + 1,
            prior_feedback=self._last_feedback,
            extended_output_enabled=extended_output_enabled,
            prior_checklist_lines=prior_checklist_lines,
            verification_result=verification_result,
            process_integrity_enabled=process_integrity_enabled,
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

        # [显示改进] display_text 理论上不应为空（goal_judge.run_goal_judge 已经
        # 对"空输出"做了兜底），但保留最后一道防线：万一自定义 profile / 未来
        # 改动导致 raw 也是空白，至少展示一句明确提示，而不是让用户看到完全
        # 空白的判定内容。
        if not display_text or not display_text.strip():
            display_text = "[GoalJudge 未返回任何可展示的判定内容，已保守判定为需继续]"

        if getattr(self._gm_cfg, "judge_show_raw_output", False):
            R.console.print()
            R.console.print("[bold]— GoalJudge 原始返回 —[/bold]")
            R.console.print(raw)

        # [改造项一/三] 解析扩展字段。verdict.extra 是 parse_judge_verdict 已经
        # 透传出来的 status/feedback 之外的原始字段，解析失败（_verdict.parse_ok
        # =False）或模型没有按扩展 schema 输出时，progress/checklist 都拿不到，
        # progress_info 里 "progress" 为 None——调用方（_check_stuck）据此自动
        # 回退到旧的文本相似度规则，不会因为字段缺失而报错或误判。
        progress_info: dict = {"progress": None, "progress_reason": "", "process_flags": []}
        process_info: dict = {"process_flags": []}
        if _verdict.parse_ok:
            raw_progress = _verdict.extra.get("progress")
            if isinstance(raw_progress, str) and raw_progress.strip().upper() in (
                "SUBSTANTIVE_ADVANCE", "SAME_APPROACH_NO_GAIN", "REGRESSED",
            ):
                progress_info["progress"] = raw_progress.strip().upper()
            raw_reason = _verdict.extra.get("progress_reason")
            if isinstance(raw_reason, str):
                progress_info["progress_reason"] = raw_reason

            if criteria_tracking_enabled:
                raw_checklist = _verdict.extra.get("checklist")
                if isinstance(raw_checklist, list):
                    by_index = {c.get("index"): c for c in self._criteria_status}
                    for item in raw_checklist:
                        if not isinstance(item, dict):
                            continue
                        idx = item.get("index")
                        target = by_index.get(idx)
                        if target is None:
                            continue
                        passed_val = item.get("passed")
                        if isinstance(passed_val, bool):
                            target["passed"] = passed_val
                        evidence_val = item.get("evidence")
                        if isinstance(evidence_val, str) and evidence_val:
                            target["evidence"] = evidence_val
                        target["last_updated_round"] = self._round + 1

            # [goal_mode_stuck_compact_plan.md §2.1] 过程判断：解析 process_flags，
            # 非空即代表判官发现了"表面结果满足但达成方式有问题"的投机行为证据。
            if process_integrity_enabled:
                raw_process_flags = _verdict.extra.get("process_flags")
                if isinstance(raw_process_flags, list):
                    process_info["process_flags"] = [
                        item for item in raw_process_flags if isinstance(item, dict)
                    ]

        progress_info.update(process_info)

        # [goal_mode_stuck_compact_plan.md §2.1] process_flags 非空时，即使
        # checklist 全部 passed=true、判官给出 DONE，也不能直接放行——强制
        # 降级为 CONTINUE，并在展示给主 Agent 的反馈里明确指出"结果表面达标
        # 但存在过程问题，需要恢复真实的验证方式后重做"，而不是允许它蒙混
        # 成 DONE。process_integrity_check_enabled=False 时完全不做这个检查，
        # 行为与升级前一致（只取决于 checklist）。
        if process_integrity_enabled and status == "DONE" and progress_info["process_flags"]:
            flags_lines = "\n".join(
                f"- [{f.get('concern', '未分类')}] {f.get('detail', '')}"
                for f in progress_info["process_flags"]
            )
            R.print_warning(
                "[GoalRunner] GoalJudge 判定 DONE，但发现过程正当性问题，强制降级为 CONTINUE："
                f"\n{flags_lines}"
            )
            status = "CONTINUE"
            display_text = (
                f"{display_text}\n\n"
                "【系统提示】虽然验收标准表面看起来已通过，但核查发现以下过程正当性问题，"
                "结果不能视为真正达成，请恢复真实的验证方式后重新完成对应标准：\n"
                f"{flags_lines}"
            )

        # [goal_mode_stuck_compact_plan.md §3.1] 计算连续进展分数：结合
        # checklist 客观通过数增量 + judge 主观 progress 判断。只在开关开启
        # 且 progress 字段可用（criteria_tracking 相关解析成功）时计算，
        # 计算失败/关闭时 progress_info 里没有 progress_score 键，不影响
        # 现有 CONTINUE/DONE/NEED_COMPACT 判定与卡住检测逻辑。
        if getattr(self._gm_cfg, "progress_score_enabled", True):
            score = self._compute_progress_score(progress_info.get("progress"))
            if score is not None:
                progress_info["progress_score"] = score
                self._progress_scores.append(score)
                if len(self._progress_scores) > self._progress_scores_cap:
                    self._progress_scores = self._progress_scores[-self._progress_scores_cap:]

        if progress_info["progress"] is not None:
            self._recent_progress_reasons.append({
                "round": self._round + 1,
                "progress": progress_info["progress"],
                "reason": progress_info["progress_reason"],
            })
            if len(self._recent_progress_reasons) > self._progress_reasons_cap:
                self._recent_progress_reasons = self._recent_progress_reasons[-self._progress_reasons_cap:]

            # [goal_mode_stuck_compact_plan.md §1.2] 无进展/退步且带具体理由时，
            # 追加进持久化的 dead_ends 清单（去重，不随窗口滚动被冲掉）。
            if (
                getattr(self._gm_cfg, "dead_ends_persist_enabled", True)
                and progress_info["progress"] in ("SAME_APPROACH_NO_GAIN", "REGRESSED")
                and progress_info["progress_reason"]
            ):
                self._record_dead_end(
                    round_no=self._round + 1,
                    progress=progress_info["progress"],
                    reason=progress_info["progress_reason"],
                )

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

        return status, display_text, progress_info

    # ── 内部：进展分数（§3.1）────────────────────────────────────────────

    def _compute_progress_score(self, progress: Optional[str]) -> Optional[float]:
        """[goal_mode_stuck_compact_plan.md §3.1] 结合 checklist 客观通过数
        增量 + judge 主观 progress 判断，产出一个连续的进展分数（而不是只有
        三态分类）。

        `progress` 为 None（判官未按扩展 schema 输出/关闭 progress_judge_mode）
        时依然可以只依赖客观 checklist 增量计算——两个信号源互相独立，缺一个
        不影响另一个。两者都缺失（criteria_tracking_enabled=False 且没有
        progress 字段）时返回 None，调用方不应记录这一轮的分数。

        客观信号（checklist 增量）作为主观判断的校验/加权，而不是替代：
          - 有新增通过条目（delta > 0）：分数至少是 0.3 * delta（哪怕主观
            判断认为没有进展，客观有硬指标增量也不该判 0）
          - 有条目从通过退化为未通过（delta < 0）：分数至多是 -0.5（明确的
            倒退信号，即使主观判断没有察觉）
          - 无变化（delta == 0）：分数完全取决于主观判断
        """
        subjective_map = {
            "SUBSTANTIVE_ADVANCE": 1.0,
            "SAME_APPROACH_NO_GAIN": 0.0,
            "REGRESSED": -1.0,
        }
        subjective = subjective_map.get(progress) if progress else None

        criteria_tracking_enabled = getattr(self._gm_cfg, "criteria_tracking_enabled", True)
        delta = None
        if criteria_tracking_enabled and self._criteria_status:
            passed_now = sum(1 for c in self._criteria_status if c.get("passed"))
            delta = passed_now - self._last_passed_count
            self._last_passed_count = passed_now

        if subjective is None and delta is None:
            return None
        if subjective is None:
            subjective = 0.0

        if delta is not None:
            if delta > 0:
                return max(subjective, 0.3 * delta)
            if delta < 0:
                return min(subjective, -0.5)
        return subjective

    # ── 内部：Dead-end 持久清单 ──────────────────────────────────────────

    def _record_dead_end(self, round_no: int, progress: str, reason: str) -> None:
        """[goal_mode_stuck_compact_plan.md §1.2] 追加一条 dead-end 记录，
        与已有记录做粗粒度相似度去重（复用 spec.py 现成的 _is_near_duplicate），
        避免同一条已验证无效的路径被反复记录、把提示block撑得又臭又长。
        """
        from .spec import _is_near_duplicate

        if any(_is_near_duplicate(reason, d.get("reason", "")) for d in self._dead_ends):
            return
        self._dead_ends.append({"round": round_no, "progress": progress, "reason": reason})

    def _render_dead_ends_block(self) -> str:
        """把持久化 dead_ends 清单渲染成"已验证无效路径"提示文本，
        用于任何 compact 场景（stuck 恢复 / NEED_COMPACT）注入。"""
        if not self._dead_ends:
            return ""
        attempted_paths_lines = "\n".join(
            f"{i + 1}.（第 {d['round']} 轮）{d['reason']}"
            for i, d in enumerate(self._dead_ends)
        )
        from mini_agent.prompts import pm
        return pm.fragment(
            "goal_mode", "STUCK_RECOVERY_ATTEMPTED_PATHS_BLOCK",
            attempted_paths_lines=attempted_paths_lines,
        )

    # ── 内部：Goal 重规划提议（§5，默认关闭）────────────────────────────────

    def _extract_replan_proposal(self, text: str) -> Optional[dict]:
        """从主 Agent 本轮输出里解析 ```replan_proposal 代码块，解析失败或
        内容为空壳（没有任何建议字段）时返回 None——不把"格式对但没有实质
        建议"的空块当成一份真提议来展示/自动应用。"""
        if not text:
            return None
        m = _REPLAN_PROPOSAL_BLOCK_RE.search(text)
        if not m:
            return None
        try:
            import json_repair
            data = json_repair.loads(m.group(1))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if not (
            data.get("reason")
            or data.get("suggested_split")
            or data.get("suggested_criteria_changes")
        ):
            return None
        return data

    def _try_auto_apply_replan(self, proposal: dict) -> bool:
        """[goal_mode_stuck_compact_plan.md §5] `replan_proposal_mode="auto"`
        时，收到非空重规划提议后自动调用 `GoalSpecBuilder.revise()` 生成新版本
        `GoalSpec` 并自动确认（`confirmed=True`），替换 `self._spec` 后继续
        执行（不终止 `run()`）。

        每次 `run()` 最多只允许自动应用一次（`self._replan_auto_applied`），
        且只会在 `_try_stuck_recovery()` 判定"最后一次恢复机会"时才有机会
        被调用——不会让 agent 每一轮都尝试"申请改标准"。

        目标变了之后，之前累积的"卡住/进展"相关旁路状态都是针对旧标准的，
        继续复用没有意义甚至会误导判断，全部重置，相当于用新目标重新开始
        观察窗口（但不重置 `self._round` / `self._compacts_done` 等运行期
        累计计数，这些仍然是同一次 `run()` 执行过程的一部分）。

        失败（LLM 调用异常等）时静默保留原 spec 不变，只打印警告，不影响
        正常的卡住终止/继续流程。
        """
        try:
            from .spec import GoalSpecBuilder
            feedback_text = render_replan_proposal(proposal)
            builder = GoalSpecBuilder(self._cfg)
            new_spec = builder.revise(self._spec, feedback_text)
        except Exception as e:
            R.print_warning(f"[GoalRunner] 自动应用重规划提议失败（保留原目标继续）：{e}")
            return False

        if not new_spec.acceptance_criteria:
            R.print_warning("[GoalRunner] 重规划后的新版本没有任何验收标准，放弃自动应用，保留原目标继续。")
            return False

        new_spec.confirmed = True
        R.print_success(f"[GoalRunner] 已自动采纳重规划提议，目标更新为第 {new_spec.version} 版：")
        R.console.print(new_spec.render_summary_for_user())

        self._spec = new_spec
        self._replan_auto_applied = True
        self._replan_proposal = None  # 已经应用，不再是"待用户确认"的提议

        self._stuck_detector.reset()
        self._criteria_status = [
            {"index": i + 1, "text": c, "passed": False, "evidence": "", "last_updated_round": 0}
            for i, c in enumerate(new_spec.acceptance_criteria)
        ]
        self._recent_progress_reasons = []
        self._dead_ends = []
        self._last_passed_count = 0
        self._progress_scores = []
        self._progress_tracker.reset()
        self._phase = GoalPhase.EXPLORING
        self._consecutive_positive_rounds = 0
        # 上一轮反馈是针对旧验收标准给出的，目标已经变了，继续拼进下一轮
        # prompt 会造成误导（"根据上一轮反馈继续推进"却引用着已经不存在的
        # 标准），改成一句通用提示，让 agent 基于新目标重新开始。
        self._last_feedback = "目标定义已根据你提出的重规划建议更新，请基于新的目标和验收标准重新开始尝试。"

        self._pin_goal_context()
        self._save_state(status="running")
        return True

    # ── 内部：探索/收敛阶段 + 主动 compact（§4，默认关闭）──────────────────

    def _update_goal_phase(self, progress_info: Optional[dict]) -> None:
        """[goal_mode_stuck_compact_plan.md §4] 依据进展分数序列（§3.1）更新
        当前所处阶段：连续 `phase_convergence_window` 轮出现正向进展分数
        （> 0）才切换到 `CONVERGING`；任意一轮分数不为正（含缺失/None，
        视为保守起见的"未确认进展"）都会立刻打回 `EXPLORING`，并重置连续
        计数——收敛阶段的判定要"严进宽出"，避免过早放松警惕。

        没有可用的 `progress_score`（比如 `progress_score_enabled=False`）
        时，永远停留在 `EXPLORING`：没有分数化的进展信号，就没有依据判断
        "已经进入稳定收敛"，保守处理。
        """
        score = (progress_info or {}).get("progress_score")
        if score is not None and score > 0:
            self._consecutive_positive_rounds += 1
        else:
            self._consecutive_positive_rounds = 0
            self._phase = GoalPhase.EXPLORING
            return

        window = getattr(self._gm_cfg, "phase_convergence_window", 3)
        if self._consecutive_positive_rounds >= window:
            self._phase = GoalPhase.CONVERGING

    def _maybe_proactive_compact(self) -> bool:
        """[goal_mode_stuck_compact_plan.md §4] `proactive_compact_enabled=True`
        且当前处于 `EXPLORING` 阶段时，按固定轮次节奏（`exploring_compact_interval`）
        主动做一次"轻量"动作——只重新钉住目标上下文（`_pin_goal_context`），
        不做真正的历史压缩，成本很低，给 agent 一次跳出当前上下文重新审视的
        机会，不需要等 `StuckDetector` 正式判定为卡住。

        `CONVERGING` 阶段暂停这层主动触发（只保留被动的三个安全阀：撞硬顶 /
        判官建议 / 真正卡住），避免把正在起效的上下文过早打断。

        返回 True 表示本轮已经执行了主动 compact（调用方应该直接进入下一轮，
        不再走 `_check_stuck`）；这是纯增量的旁路判断，不影响现有的
        `hit_max_turns` / `NEED_COMPACT` / stuck 三条已有路径。默认
        `proactive_compact_enabled=False` 时恒返回 `False`，行为与升级前
        完全一致。
        """
        if not getattr(self._gm_cfg, "proactive_compact_enabled", False):
            return False
        if self._phase is not GoalPhase.EXPLORING:
            return False

        interval = max(1, getattr(self._gm_cfg, "exploring_compact_interval", 2))
        if self._round - self._last_proactive_compact_round < interval:
            return False

        self._last_proactive_compact_round = self._round
        R.print_info(
            f"[GoalRunner] 探索阶段主动触发一次轻量 compact（每 {interval} 轮一次，"
            "重新钉住目标上下文，帮助换个角度重新审视）。"
        )
        self._pin_goal_context()
        return True

    # ── 内部：卡住检测（连续反馈高度雷同 / LLM 判进展）──────────────────────

    def _check_stuck(self, feedback: str, progress_info: Optional[dict] = None) -> bool:
        """观察本轮反馈，返回是否已判定为"卡住"（供 run() 决定是否尝试恢复）。

        [改造项一] 当 `cfg.goal_mode.progress_judge_mode == "llm"` 且本轮
        GoalJudge 成功输出了 `progress` 字段时，改用 GoalJudge 的语义判断
        （是否有实质进展）驱动卡住计数，而不是对反馈文本做规则化的相似度
        比较——这样能识别"表述不同但本质相同"（原规则的假阴性）和"表述相似
        但确有进展"（原规则的假阳性）这两类规则算法处理不好的情况。

        `progress` 字段缺失（解析失败 / 模型未按扩展 schema 输出 / 功能关闭）
        时自动回退到原有的 `StuckDetector.observe(text)` 文本相似度规则，
        不会因为升级而降低鲁棒性。

        内部计数状态（连续雷同计数、恢复额度）全部委托给 `StuckDetector`，
        与 TurnJudge 共享同一套实现（见 role_agents/stuck_detector.py）。

        [goal_mode_stuck_compact_plan.md §3.2] 在 `StuckDetector` 判定为
        `NONE`（没有识别到"完全没有变化/退步"）的基础上，额外用
        `ProgressTracker` 检查"最近几轮进展分数是否呈现有意义的上升趋势"——
        识别"每轮都有一点点进展，但累积起来毫无实质意义"的伪进展模式。
        任一个判定为需要干预都应该触发恢复流程；`ProgressTracker` 触发时
        通过 `StuckDetector.trigger_recovery()` 复用同一份恢复额度，不会
        因为触发路径不同而获得额外的恢复次数。仅在
        `pseudo_progress_detection_enabled=True` 且本轮有可用的
        `progress_score`（依赖 §3.1）时才会检查；关闭或没有分数可用时，
        行为与升级前完全一致。
        """
        progress_mode = getattr(self._gm_cfg, "progress_judge_mode", "llm")
        progress = (progress_info or {}).get("progress")

        if progress_mode == "llm" and progress is not None:
            is_same = progress in ("SAME_APPROACH_NO_GAIN", "REGRESSED")
            self._last_stuck_signal = self._stuck_detector.observe_signal(is_same=is_same)
        else:
            self._last_stuck_signal = self._stuck_detector.observe(feedback)
        self._last_stuck_reason = "similarity"

        if (
            self._last_stuck_signal is StuckSignal.NONE
            and getattr(self._gm_cfg, "pseudo_progress_detection_enabled", True)
        ):
            score = (progress_info or {}).get("progress_score")
            if score is not None:
                pseudo_detected = self._progress_tracker.observe(score)
                if pseudo_detected:
                    R.print_warning(
                        "[GoalRunner] 最近几轮进展分数长期平缓但不为零，疑似伪进展，"
                        "触发和卡住同等级别的恢复流程。"
                    )
                    self._last_stuck_signal = self._stuck_detector.trigger_recovery()
                    self._last_stuck_reason = "pseudo_progress"

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

        if self._last_stuck_reason == "pseudo_progress":
            recovery_log_reason = (
                f"最近 {getattr(self._gm_cfg, 'pseudo_progress_window', 5)} 轮进展分数长期平缓、"
                "疑似伪进展"
            )
        else:
            recovery_log_reason = f"连续 {self._gm_cfg.consecutive_same_feedback_limit} 轮反馈高度相似，疑似卡住"
        R.print_warning(
            f"[GoalRunner] {recovery_log_reason}，"
            f"尝试压缩历史后给 agent 一次重新整理思路的机会"
            f"（第 {self._stuck_detector.recoveries_used}/{self._gm_cfg.max_stuck_recoveries} 次恢复额度）。"
        )

        # [goal_mode_stuck_compact_plan.md §1.1] 分级 compact：前
        # light_compact_max_recoveries 次恢复只注入提示、不压缩历史（第一次
        # 卡住可能只是暂时性的），超过这个次数才真正触发 compact。
        # StuckDetector.observe()/observe_signal() 判定 RECOVER 时已经把
        # recoveries_used 计数加过一次，这里读到的就是"本次是第几次恢复"。
        light_max = getattr(self._gm_cfg, "light_compact_max_recoveries", 1)
        recovery_index = self._stuck_detector.recoveries_used
        is_light = recovery_index <= light_max
        if is_light:
            R.print_info(
                f"[GoalRunner] 本次为轻量恢复（第 {recovery_index}/{light_max} 次轻量额度内），"
                "先只注入提示、不压缩历史，给一次低成本的重新尝试机会。"
            )
            self._pin_goal_context()
        else:
            self._do_compact()

        from ._compat import make_goal_context
        hint = (
            "[GoalRunner 提示] 你最近连续几轮的输出/反馈高度相似，似乎卡在同一个"
            "问题上反复尝试同样的方法却没有新进展。"
            + ("历史已经压缩过，" if not is_light else "")
            + "请不要重复"
            "上一轮的做法——先重新梳理一下目前的障碍到底是什么，考虑换一个角度、"
            "换一种工具或方法，或者先做一些诊断性的检查（比如确认前提假设是否"
            "成立）来找到卡住的真正原因，再继续推进目标。"
        )

        # [改造项二] 如果积累了带具体理由的 progress_reason 历史，把"已验证
        # 无效的方向"拼进提示，取代/补充上面这段通用话术——让"换角度"有具体
        # 依据，而不是一句空话导致 agent 换个说法继续同一个思路。
        # [goal_mode_stuck_compact_plan.md §1.2] 优先使用不随窗口滚动的持久化
        # dead_ends 清单；关闭该开关时退化为原有的窗口内 _recent_progress_reasons
        # （升级前行为，保持向后兼容）。
        attempted_paths_enabled = getattr(self._gm_cfg, "stuck_recovery_attempted_paths_enabled", True)
        if attempted_paths_enabled and getattr(self._gm_cfg, "dead_ends_persist_enabled", True):
            dead_ends_block = self._render_dead_ends_block()
            if dead_ends_block:
                hint = hint + "\n\n" + dead_ends_block
        elif attempted_paths_enabled:
            no_gain_reasons = [
                r for r in self._recent_progress_reasons
                if r.get("progress") in ("SAME_APPROACH_NO_GAIN", "REGRESSED") and r.get("reason")
            ]
            if no_gain_reasons:
                attempted_paths_lines = "\n".join(
                    f"{i + 1}.（第 {r['round']} 轮）{r['reason']}"
                    for i, r in enumerate(no_gain_reasons)
                )
                from mini_agent.prompts import pm
                hint = hint + "\n\n" + pm.fragment(
                    "goal_mode", "STUCK_RECOVERY_ATTEMPTED_PATHS_BLOCK",
                    attempted_paths_lines=attempted_paths_lines,
                )

        # [goal_mode_stuck_compact_plan.md §5] 即将耗尽恢复额度的最后一次
        # 机会：额外请求一份"重规划提议"，只在这一轮问一次（不是每次卡住恢复
        # 都问），由 run() 在拿到本轮输出后立即消费一次
        # （self._awaiting_replan_proposal）。cfg.goal_mode.replan_proposal_mode
        # == "off"（默认）时完全跳过，不拼接任何额外提示、不解析任何输出，
        # 行为与升级前完全一致。
        replan_mode = getattr(self._gm_cfg, "replan_proposal_mode", "off")
        if replan_mode != "off" and recovery_index >= self._gm_cfg.max_stuck_recoveries:
            from mini_agent.prompts import pm
            hint = hint + "\n\n" + pm.fragment("goal_mode", "REPLAN_PROPOSAL_REQUEST_BLOCK")
            self._awaiting_replan_proposal = True

        self._agent._hist.append_raw_dict(make_goal_context(hint))

        self._save_state(status="running")
        return True

    # ── 内部：compact ────────────────────────────────────────────────────

    def _build_goal_aware_compact_hint(self) -> str:
        """
        [compact_mechanism_improvement_plan P0-A]
        cfg.goal_mode... 复用 cfg.compress.goal_aware_weighting_enabled 开关
        （放在 compress 子配置下，因为这本质是 compact 行为的一个参数，
        goal_mode 只是其中一个调用方）。存在未通过的验收标准时，构建一段
        提示文本传给 compact_with_skills(goal_hint=...)；关闭或没有未通过
        标准时返回空字符串，行为与升级前完全一致。
        """
        if not getattr(getattr(self._cfg, "compress", None), "goal_aware_weighting_enabled", False):
            return ""
        unmet = [c.get("text", "") for c in self._criteria_status if not c.get("passed") and c.get("text")]
        if not unmet:
            return ""
        from mini_agent.prompts import pm
        lines = "\n".join(f"- {text}" for text in unmet)
        return pm.fragment("compress", "GOAL_AWARE_COMPACT_HINT_BLOCK", unmet_criteria_lines=lines)

    def _do_compact(self) -> None:
        R.print_info("[GoalRunner] 正在压缩历史…")
        summary = ""
        try:
            goal_hint = self._build_goal_aware_compact_hint()
            summary = self._agent.compact_with_skills(goal_hint=goal_hint)
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
            criteria_status=list(self._criteria_status),
            recent_progress_reasons=list(self._recent_progress_reasons),
            dead_ends=list(self._dead_ends),
            last_passed_count=self._last_passed_count,
            progress_scores=list(self._progress_scores),
            replan_proposal=dict(self._replan_proposal) if self._replan_proposal else {},
        )
        try:
            self._state_store.save(state)
        except Exception as e:
            R.print_warning(f"[GoalRunner] 状态落盘失败（不影响本轮执行）：{e}")

    def _finish(self, status: str, report: str) -> GoalRunResult:
        # [BUGFIX] 此前 stuck / max_rounds_exhausted 都被折叠成同一个 "failed"，
        # resume 时读不出真实的终止原因；下面新增的"stuck 恢复时重置卡住计数"
        # 逻辑依赖这里保留 "stuck" 这个具体值，因此不再统一折叠。
        persisted_status = status if status in ("done", "stuck", "max_rounds_exhausted") else "failed"
        self._save_state(status=persisted_status, final_report=report)

        if status in ("stuck", "max_rounds_exhausted"):
            self._write_failure_lesson(status=status, report=report)

        if status == "done":
            R.print_success(f"[GoalRunner] 目标已达成（共 {self._round} 轮）。")
        else:
            R.print_warning(f"[GoalRunner] 目标未达成（状态：{status}）：{report}")
            # [goal_mode_stuck_compact_plan.md §5] "confirm" 档位下，如果本次
            # 运行中收集到了非空的重规划提议，在终止时明确展示给用户（不只是
            # 写进 memory），并提示可以通过 /goal revise 采纳——提议本身不会
            # 自动生效改写 self._spec。"auto" 档位应用成功后
            # self._replan_proposal 已被清空，不会重复展示。
            if self._replan_proposal and getattr(self._gm_cfg, "replan_proposal_mode", "off") == "confirm":
                R.console.print()
                R.console.print("[bold]— Agent 提出的重规划提议（仅供参考，不会自动生效）—[/bold]")
                R.console.print(render_replan_proposal(self._replan_proposal))
                R.print_info(
                    "如果认可以上建议，可执行 `/goal revise` 基于该提议重新协商目标"
                    "（也可以在协商过程中输入自己的修改意见）。"
                )

        return GoalRunResult(
            status=status,
            rounds_used=self._round,
            compacts_done=self._compacts_done,
            final_report=report,
            goal_spec=self._spec,
            replan_proposal=self._replan_proposal,
        )

    def _write_failure_lesson(self, status: str, report: str) -> None:
        """[next_doc/goal_mode_completion_improvement_plan.md 改造项五]

        goal 因 stuck / max_rounds_exhausted 终止时，把已尝试路径 + 失败原因
        整理成一条 entry_type="lesson" 写入 memory（source="goal_mode_failure"），
        供未来同类目标的 GoalSpecBuilder / 主 Agent 参考，避免重复踩同一个坑。

        只在 cfg.goal_mode.failure_lesson_enabled=True、且主 Agent 确实持有
        可用的 memory 后端时才会写入；写入失败（含 memory 未启用）不影响 goal
        本身已经完成的终止流程，只是静默跳过。
        """
        if not getattr(self._gm_cfg, "failure_lesson_enabled", True):
            return

        memory_backend = getattr(self._agent, "_memory", None)
        if memory_backend is None:
            return

        try:
            from mini_agent.perception.memory_store import MemoryEntry

            # [goal_mode_stuck_compact_plan.md §1.2] 失败经验沉淀时优先使用
            # 持久化的 dead_ends 清单（覆盖更完整，不受窗口滚动限制）。
            source_reasons = self._dead_ends if self._dead_ends else self._recent_progress_reasons
            no_gain_reasons = [
                r for r in source_reasons
                if r.get("progress") in ("SAME_APPROACH_NO_GAIN", "REGRESSED") and r.get("reason")
            ]
            attempted_lines = "\n".join(
                f"- （第 {r['round']} 轮）{r['reason']}" for r in no_gain_reasons
            ) or "（未记录到具体的分轮次进展理由）"

            unmet_criteria = [
                c["text"] for c in self._criteria_status if not c.get("passed")
            ]
            unmet_lines = "\n".join(f"- {t}" for t in unmet_criteria) or "（无法确定具体未通过的标准）"

            summary = f"Goal 模式执行终止（{status}）：{self._spec.goal_text}"
            entry = MemoryEntry(
                session_id=self._agent.session_id or "goal_mode",
                summary=summary,
                key_outcomes=[
                    f"共尝试 {self._round} 轮、压缩 {self._compacts_done} 次后仍未达成目标",
                ],
                tags=["lesson", "goal_mode_failure", status],
                model="goal_mode_runner",
                entry_type="lesson",
                source="goal_mode_failure",
                trigger=f"目标：{self._spec.goal_text}",
                outcome=(
                    f"终止状态：{status}。以下方向已经验证无效：\n{attempted_lines}\n\n"
                    f"仍未通过的验收标准：\n{unmet_lines}"
                ),
                suggested_action=(
                    "下次面对类似目标时，建议先确认是否要规避以上已验证无效的方向，"
                    "或者先做诊断性检查确认这次的前提条件是否与上次不同。"
                ),
                confidence=0.5,
            )
            memory_backend.add(entry)
        except Exception as e:
            R.print_warning(f"[GoalRunner] 失败经验写入 memory 失败（不影响 goal 终止流程）：{e}")

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