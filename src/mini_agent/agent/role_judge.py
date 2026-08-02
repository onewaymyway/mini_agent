from __future__ import annotations

import copy
import re as _re
import threading
from typing import Optional

from mini_agent.config import AppConfig, SessionStats, build_system_prompt
from mini_agent.llm import (
    LLMClient, LLMConfig, LLMResponse, ToolSchema,
    create_client, LLMError,
)
from mini_agent.llm.retry import RetryPolicy, default_retry_policy, no_retry_policy, parse_backoff
from mini_agent.llm.client_pool import LLMClientPool
from mini_agent.permissions import PermissionGuard
from mini_agent.skills import SkillLoader
from mini_agent.tools import ToolRegistry, get_default_registry
from mini_agent.session import SessionManager, Session
import mini_agent.ui.renderer as R
from mini_agent.perception.token_counter import estimate_messages_tokens
from mini_agent.perception.project_scanner import ProjectScanner
from mini_agent.perception.file_watcher import FileWatcher
from mini_agent.perception.tool_cache import ToolResultCache
from mini_agent.perception.memory_base import MemoryBackend
from mini_agent.perception.memory_store import MemoryStore, MemoryEntry
from mini_agent.perception.memory_factory import create_memory_backend
from mini_agent.context_builder import ContextBuilder
from mini_agent.tool_executor import ToolExecutor
from mini_agent.history_manager import HistoryManager
from mini_agent.reminders import ReminderManager

from mini_agent.agent._helpers import (
    _term_write_lock_ctx, _NullCtx, _locked_print_info, _locked_print_warning,
    _is_tool_error, _clamp_confidence, _parse_lesson_candidates, _parse_timeline_summary,
)


class RoleJudgeMixin:
    """角色 Agent 联动与轮次质量判定（turn judge）。"""

    def _trigger_role_agents_tool_use(self, tool_calls, result_strs: list) -> None:
        """
        工具调用完成后，触发监听该工具的角色 Agent（通常是 CoachAgent）。
        触发是轻量的：如果没有注册任何 tool 角色，立即返回，开销为零。
        """
        try:
            from mini_agent.role_agents import get_dispatcher
        except ImportError:
            return

        dispatcher = get_dispatcher()
        if dispatcher is None or not dispatcher.has_tool_roles:
            return

        triggers = dispatcher.get_tool_triggers()
        if not triggers:
            return

        # 提取最近几条历史作为上下文（避免传太多）
        context_msgs = self._history[-6:] if len(self._history) >= 6 else self._history
        import json as _json
        context = "\n".join(
            f"[{m['role']}]: {str(m['content'])[:200]}"
            for m in context_msgs
            if isinstance(m.get('content'), str)
        )

        for tc, result_str in zip(tool_calls, result_strs):
            if tc.name not in triggers:
                continue
            # 解析 tool input（可能是 dict 或 str）
            tool_input = tc.input if isinstance(tc.input, dict) else {"input": str(tc.input)}
            dispatcher.trigger_tool_use(
                tool_name=tc.name,
                tool_input=tool_input,
                tool_output=result_str[:2000],  # 截断过长输出
                context=context,
                inject_into=self._history,
                parent_session_id=self._session.id if self._session else None,
                parent_session_dir=self._current_session_dir(),
            )

    def _run_role_agents_output(self, original_request: str, initial_output: str) -> str:
        """
        主 Agent 完成 turn 输出后，触发所有 output 类角色 Agent。
        支持 evaluator 的多轮修订循环：
          1. 触发 evaluator → 评分 → 注入反馈
          2. 若未通过且 max_iterations > 1 → 追加 "请根据反馈修订" → 重新 _agentic_loop
          3. 重复直到通过或耗尽次数
        """
        try:
            from mini_agent.role_agents import get_dispatcher
        except ImportError:
            return initial_output

        dispatcher = get_dispatcher()
        if dispatcher is None or not dispatcher.has_output_roles:
            return initial_output

        current_output = initial_output

        # 对每个 output 角色，做最多 max_iterations 轮
        for profile in dispatcher._output_roles:
            max_iter = profile.max_iterations if profile.role_type == "evaluator" else 1

            for iteration in range(1, max_iter + 1):
                import mini_agent.ui.renderer as R
                R.print_info(
                    f"[RoleAgent:{profile.name}] "
                    f"{'评估' if profile.role_type == 'evaluator' else '分析'} "
                    f"第 {iteration}/{max_iter} 轮..."
                )

                # 运行单次角色评估
                from mini_agent.role_agents.feedback import (
                    RoleFeedback, extract_score, build_inject_message
                )
                if profile.role_type == "evaluator":
                    from mini_agent.role_agents.evaluator import run_evaluator
                    raw = run_evaluator(
                        profile=profile,
                        base_cfg=self.cfg,
                        original_request=original_request,
                        agent_output=current_output,
                        iteration=iteration,
                        parent_session_id=self._session.id if self._session else None,
                parent_session_dir=self._current_session_dir(),
                    )
                else:
                    from mini_agent.role_agents.dispatcher import RoleAgentDispatcher
                    raw = dispatcher._run_custom_role(
                        profile, current_output, original_request,
                        parent_session_id=self._session.id if self._session else None,
                parent_session_dir=self._current_session_dir(),
                    )

                score = extract_score(raw) if profile.role_type == "evaluator" else None
                passed = (
                    score is not None and score >= profile.pass_threshold
                ) if score is not None else True  # 非 evaluator 视为通过

                feedback = RoleFeedback(
                    role_name=profile.name,
                    role_type=profile.role_type,
                    raw_output=raw,
                    score=score,
                    passed=passed,
                    inject_as=profile.inject_as,
                )

                # 注入反馈到历史（带 _type=role_agent）
                inject_msg = build_inject_message(feedback)
                from mini_agent.history.entry import HType
                inject_typed = dict(inject_msg, _type=HType.ROLE_AGENT)
                self._hist.append_raw_dict(inject_typed)

                if score is not None:
                    score_pct = int(score * 100)
                    status = "✅ 通过" if passed else "⚠️ 需修订"
                    R.print_info(f"[RoleAgent:{profile.name}] 评分 {score_pct}/100 {status}")

                # 通过或最后一轮，不再循环
                if passed or iteration >= max_iter:
                    break

                # 未通过且还有轮次：让主 Agent 根据反馈修订输出
                R.print_info(f"[RoleAgent:{profile.name}] 反馈已注入，主 Agent 修订中...")
                revision_prompt = (
                    "请根据上方评估反馈，对你的回答进行修订和改进。"
                    "重点解决指出的具体问题，保持其他优点不变。"
                )
                self._hist.append_user(revision_prompt)
                self.stats.turns += 1
                current_output = self._agentic_loop()

        return current_output

    # ── [SYS-UNDO] 手动重试 / 回退 ───────────────────────────────────────────

    def _maybe_run_turn_judge(self, assistant_output: str) -> None:
        """
        [SYS-TURN-JUDGE] 轮次守门员：一轮对话结束、真正把控制权交还真人用户之前，
        核查这到底是「真的需要用户输入」还是「主 Agent 遇到了技术性问题（模型
        输出格式有问题、撞到 max_turns 硬顶需要 compact 等）」，后者由系统自动
        代替用户反馈，让主 Agent 继续处理，而不是打断真人。

        安全阀：
          - 子 Agent（is_subagent）从不触发，避免嵌套判定
          - 未开启 cfg.turn_judge.enabled 时直接跳过（零开销）
          - 连续自动接管次数达到 max_auto_rounds 后强制交还真人，防止死循环
          - 连续 consecutive_same_output_limit 轮输出高度相似 → 判定"卡住"，
            主动 compact + 换角度提示重试，最多 max_stuck_recoveries 次
            （不占用 max_auto_rounds 预算），额度耗尽后再次卡住才强制交还真人
          - 判定/执行过程中的任何异常都保守回退到"等待真人输入"
        """
        tj_cfg = getattr(self.cfg, "turn_judge", None)
        if tj_cfg is None or not tj_cfg.enabled or self._is_subagent:
            return

        if self._turn_judge_auto_count >= tj_cfg.max_auto_rounds:
            R.print_info(
                f"[TurnJudge] 已连续自动接管 {self._turn_judge_auto_count} 次，"
                f"达到上限（{tj_cfg.max_auto_rounds}），强制交还真人用户输入。"
            )
            self._turn_judge_auto_count = 0
            self._turn_judge_stuck_detector.reset()
            return

        # ── 卡住检测（与 goal_mode 的 _check_stuck/_try_stuck_recovery 共享
        # role_agents/stuck_detector.py::StuckDetector 实现）──────────────
        # [SYS-TURN-JUDGE] 不等 TurnJudge 自己判定 NEED_COMPACT，先看主 Agent
        # 连续几轮的输出是否高度相似——高度相似通常意味着反复卡在同一个
        # 报错/格式问题上打转，没有实质进展。检测到就主动 compact + 提示
        # 换角度重试，且不占用 max_auto_rounds 预算（与 goal_mode 里"卡住
        # 恢复"不消耗 max_rounds 预算的语义一致）。
        limit = getattr(tj_cfg, "consecutive_same_output_limit", 0)
        if limit > 0:
            detector = self._turn_judge_stuck_detector
            detector.consecutive_limit = limit
            detector.similarity_threshold = tj_cfg.same_output_similarity_threshold
            detector.max_recoveries = tj_cfg.max_stuck_recoveries
            max_stuck = tj_cfg.max_stuck_recoveries

            from mini_agent.role_agents.stuck_detector import StuckSignal
            signal = detector.observe(assistant_output)

            if signal is StuckSignal.GIVE_UP:
                R.print_warning(
                    f"[TurnJudge] 连续 {limit} 轮输出高度相似，且已用尽 "
                    f"{max_stuck} 次压缩重试的恢复额度，疑似卡在同一个问题上，"
                    "强制交还真人用户输入。"
                )
                # [系统关联性断点改进方案 F2 追加] 此前 TurnJudge 场景的
                # stuck 信号没有任何持久化记录（GoalRunner 场景已经通过
                # _record_dead_end() 落盘），这里补上——只做最小成本的
                # 追加写入，供 sys:failure_pattern_aggregation 周期性聚合，
                # 不影响本轮已经在执行的"交还真人"流程，异常不阻断主流程。
                try:
                    from mini_agent.evolution.failure_pattern_store import record_turn_judge_stuck_event
                    from mini_agent.history.entry import HType
                    task_hint = ""
                    for msg in reversed(self._history):
                        if msg.get("_type") == HType.USER_INPUT or (
                            msg.get("_type") is None and msg.get("role") == "user"
                        ):
                            content = msg.get("content", "")
                            task_hint = content if isinstance(content, str) else ""
                            break
                    from mini_agent.storage.paths import AgentPaths as _TJAgentPaths
                    record_turn_judge_stuck_event(
                        _TJAgentPaths(self.cfg.project_root),
                        task_hint=task_hint,
                        reason=f"连续 {limit} 轮输出高度相似，恢复额度耗尽",
                    )
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.agent.role_judge.RoleJudgeMixin._maybe_run_turn_judge.record_turn_judge_stuck_event')
                self._turn_judge_auto_count = 0
                detector.reset()
                return

            if signal is StuckSignal.RECOVER:
                R.print_warning(
                    f"[TurnJudge] 连续 {limit} 轮输出高度相似，"
                    f"疑似卡住，先压缩历史再给一次换角度重试的机会"
                    f"（第 {detector.recoveries_used}/{max_stuck} 次恢复，"
                    "不计入自动接管次数）。"
                )
                try:
                    summary = self.compact_with_skills()
                    if summary:
                        R.print_success("[TurnJudge] compact 完成。")
                    else:
                        R.print_warning("[TurnJudge] compact 完成，但没有生成摘要文本。")
                except Exception as e:
                    from mini_agent.errors import log_exception
                    log_exception(e, where='mini_agent.agent.role_judge.RoleJudgeMixin._maybe_run_turn_judge')
                    R.print_error(f"[TurnJudge] compact 失败：{e}，回退到等待真人输入。")
                    self._turn_judge_auto_count = 0
                    detector.reset()
                    return

                self._turn_end_user_input = (
                    "[TurnJudge 自动接管] 你最近连续几轮的输出高度相似，似乎卡在同一个"
                    "问题上反复尝试同样的方法却没有新进展。历史已经压缩过，请不要重复"
                    "上一轮的做法——先重新梳理一下目前的障碍到底是什么，考虑换一个角度、"
                    "换一种工具或方法，或者先做一些诊断性的检查，再继续推进任务。"
                )
                R.print_info(
                    f"[TurnJudge] 已自动代替用户输入继续推进（卡住恢复 "
                    f"第 {detector.recoveries_used}/{max_stuck} 次）。"
                )
                return  # 本轮不再调用 TurnJudge LLM 判定，直接用换角度提示接管

        from mini_agent.role_agents.turn_judge import run_turn_judge, build_turn_judge_prompt
        from mini_agent.role_agents.feedback import RoleFeedback, format_feedback, extract_turn_status, build_inject_message

        auto_round_no = self._turn_judge_auto_count + 1

        # 组装最近历史窗口（角色 + 内容摘要），供 judge 参考上下文
        window = max(0, int(getattr(tj_cfg, "history_window", 6)))
        recent_msgs = self._hist._history[-window:] if window else []
        recent_lines = []
        for m in recent_msgs:
            role = m.get("role", "")
            content = m.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if len(content) > 500:
                content = content[:500] + "…(截断)"
            recent_lines.append(f"[{role}] {content}")
        recent_history = "\n".join(recent_lines)

        # [判官接线统一 阶段六 b] profile 不再由这里现场拼一个临时
        # AgentProfile 对象，而是优先从 dispatcher 的 turn_end_review 注册表
        # 查询——这样 "turn_judge" 才有一个真实存在的"注册来源"，可以被
        # role_agent.block 屏蔽，也可以被磁盘上的 .agent/agents/turn_judge.md
        # 自定义覆盖（model/system_prompt 等）。
        #
        # dispatcher 为 None（未经过 app.py 的 init_role_agent_system，例如
        # 独立/测试场景直接触发 _maybe_run_turn_judge）时，fallback 到升级前
        # 的现场拼装方式，保持完全向后兼容。
        #
        # dispatcher 存在但查不到任何 turn_end_review profile（"turn_judge"
        # 被 role_agent.block 屏蔽掉了）：这里**不**像 GoalRunner 那样报错
        # 拒绝启动——TurnJudge 本来就有"任何异常都保守回退到等待真人输入"
        # 的既定原则（对应设计文档 §8 开放问题 3 里"分歧较小"的那一条），
        # 直接当作 TurnJudge 未启用处理：不调用判官、不消耗
        # `_turn_judge_auto_count`，直接把控制权交还真人。
        from mini_agent.role_agents import get_dispatcher
        _dispatcher = get_dispatcher()
        if _dispatcher is not None:
            turn_end_review_roles = _dispatcher.get_turn_end_review_roles()
            if not turn_end_review_roles:
                R.print_info(
                    "[TurnJudge] cfg.turn_judge.enabled=True，但 \"turn_judge\" 已被 "
                    "role_agent.block 屏蔽，本轮当作 TurnJudge 未启用处理，"
                    "直接交还真人用户输入。"
                )
                return
            profile = turn_end_review_roles[0]
        else:
            from mini_agent.orchestrator.agent_profiles import AgentProfile
            profile = AgentProfile(
                name="turn_judge",
                role_type="turn_judge",
                trigger_on="turn_end_review",
                model=tj_cfg.judge_model,
                provider=tj_cfg.judge_provider,
            )

        if tj_cfg.judge_show_prompt:
            prompt_preview = build_turn_judge_prompt(
                assistant_output=assistant_output,
                recent_history=recent_history,
                auto_round_no=auto_round_no,
                max_auto_rounds=tj_cfg.max_auto_rounds,
                hit_max_turns=self._last_turn_hit_max_turns,
            )
            R.console.print()
            R.console.print("[bold]— TurnJudge 输入 Prompt —[/bold]")
            R.console.print(prompt_preview)
            R.console.print()

        R.print_info(f"[TurnJudge] 正在核查本轮是否需要真人介入…（第 {auto_round_no}/{tj_cfg.max_auto_rounds} 次自动核查）")

        raw = run_turn_judge(
            profile=profile,
            base_cfg=self.cfg,
            assistant_output=assistant_output,
            recent_history=recent_history,
            auto_round_no=auto_round_no,
            max_auto_rounds=tj_cfg.max_auto_rounds,
            hit_max_turns=self._last_turn_hit_max_turns,
            parent_session_id=self._session.id if self._session else None,
                parent_session_dir=self._current_session_dir(),
        )

        status = extract_turn_status(raw) or "NEED_USER"  # 解析失败时保守按 NEED_USER 处理

        # [Phase 5] TurnJudge 现在约定输出结构化 JSON（见 role_agents/verdict.py）。
        # 展示层/注入历史/自动接管提示优先用解析出的 `feedback` 字段，而不是
        # 原始 JSON 字符串或靠正则从 Markdown 里"抠"出的"**反馈**"段落；
        # JSON 解析失败时（历史遗留纯文本格式等）回退到原始文本，行为与升级前一致。
        from mini_agent.role_agents.verdict import parse_judge_verdict
        _verdict = parse_judge_verdict(
            raw, valid_statuses=["NEED_USER", "AUTO_CONTINUE", "NEED_COMPACT"], fallback_status=status,
        )
        display_text = _verdict.feedback if (_verdict.parse_ok and _verdict.feedback) else raw

        feedback_obj = RoleFeedback(
            role_name="turn_judge",
            role_type="turn_judge",
            raw_output=display_text,
            inject_as="user",
            turn_status=status,
        )

        R.console.print()
        R.console.print(format_feedback(feedback_obj))
        R.console.print()

        if status == "NEED_USER":
            self._turn_judge_auto_count = 0
            self._turn_judge_stuck_detector.reset()
            return

        if status == "NEED_COMPACT":
            R.print_info("[TurnJudge] 建议先压缩历史再继续，正在自动压缩…")
            try:
                summary = self.compact_with_skills()
                if summary:
                    R.print_success("[TurnJudge] compact 完成。")
                else:
                    R.print_warning("[TurnJudge] compact 完成，但没有生成摘要文本。")
            except Exception as e:
                from mini_agent.errors import log_exception
                log_exception(e, where='mini_agent.agent.role_judge.RoleJudgeMixin._maybe_run_turn_judge')
                R.print_error(f"[TurnJudge] compact 失败：{e}，回退到等待真人输入。")
                self._turn_judge_auto_count = 0
                self._turn_judge_stuck_detector.reset()
                return
            auto_msg = "[TurnJudge 自动接管] 历史已压缩，请根据目标继续推进任务。"
        else:  # AUTO_CONTINUE
            # 优先用解析出的结构化 `feedback` 字段作为注入文本，找不到（JSON 解析
            # 失败等历史遗留情况）就用完整判定文本兜底。
            auto_msg = raw
            if display_text and display_text.strip() and display_text is not raw:
                auto_msg = (
                    "[TurnJudge 自动接管] 检测到技术性问题（而非任务真正完成），"
                    "以下是系统代替用户给出的下一步指令：\n\n" + display_text.strip()
                )

        # 把判定反馈也记入历史（与 goal_judge 一致的注入方式），保留可审计的判定痕迹
        try:
            from mini_agent.history.entry import HType
            role_agent_type = HType.ROLE_AGENT
        except (ImportError, AttributeError):
            role_agent_type = "role_agent"
        inject_msg = build_inject_message(feedback_obj)
        inject_typed = dict(inject_msg, _type=role_agent_type)
        self._hist.append_raw_dict(inject_typed)

        self._turn_judge_auto_count += 1
        self._turn_end_user_input = auto_msg
        R.print_info(
            f"[TurnJudge] 判定为 {status}，自动代替用户输入继续推进（第 {auto_round_no} 次）。"
        )

