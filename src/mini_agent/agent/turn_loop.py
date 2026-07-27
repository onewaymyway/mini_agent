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
from mini_agent.perception.token_counter import estimate_messages_tokens, estimate_tokens
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


class TurnLoopMixin:
    """对话主循环：run_turn / agentic loop / 工具执行与结果拼装。"""

    def run_turn(self, user_message: str) -> str:

        """
        Run one user turn. May make multiple API calls (tool loops).
        Returns the final assistant text.
        """
        try:
            # [SYS-HOOKS] UserPromptSubmit：可注入额外上下文
            from mini_agent.hooks import get_hook_manager
            hook_mgr = get_hook_manager()
            if hook_mgr is not None:
                pre = hook_mgr.run("UserPromptSubmit", {"prompt": user_message})
                if pre.context:
                    user_message = user_message + f"\n\n[hook context]\n{pre.context}"

            # [SYS-WATCH] 检测外部文件变化（消费后台线程的检测结果，不做同步 IO）
            if self._file_watcher:
                with self._file_changes_lock:
                    changed = list(self._pending_file_changes)
                    self._pending_file_changes.clear()
                if changed:
                    notice = self._file_watcher.build_change_notice(
                        changed, self.cfg.project_root
                    )
                    # 让缓存失效
                    if self._tool_cache:
                        for p in changed:
                            self._tool_cache.invalidate_file(p)
                    user_message = user_message + notice

            if self.skill_loader and self.cfg.skill.keyword_activation_enabled:
                newly = self.skill_loader.auto_activate(user_message)
                for name in newly:
                    R.print_skill_loaded(name)
                    # [SYS-SKILL-TRACK] 记录技能激活
                    if self.cfg.skill_tracking_enabled:
                        self.stats.record_skill_activation(name)
                # [渐进式加载] 关键词自动通道：对已激活 skill 下的子资源做同样的匹配，
                # 命中且带 triggers 的 resource 会被自动加载（留空 triggers 的资源
                # 不受影响，只能被 agent 通过 skill_resource_load 主动加载）
                newly_resources = self.skill_loader.auto_activate_resources(user_message)
                for key in newly_resources:
                    R.print_info(f"📥 Resource auto-loaded: {key}")

            # [SYS-MEMORY] 预检索记忆，缓存到 turn 级别。
            # 整个 turn 内的多次 _call_llm() 复用此缓存，不重复遍历记忆条目。
            if self._ctx_builder is not None:
                self._ctx_builder.refresh_turn_context(user_message)

            # [SYS-UNDO] 在追加用户消息前保存快照，用于 retry/rollback
            self._save_turn_snapshot()

            self._hist.append_user(user_message)
            self.stats.turns += 1

            # [SYS-LESSON] 人类反馈纠正检测（Stage 1.4）：规则式短语识别，
            # 命中时立即生成 source="human_feedback" 的高质量 lesson，不等 SessionEnd。
            self._detect_and_record_correction(user_message)

            # [SYS-REMINDER] 用户意图触发：在用户消息入队后，检查是否需要注入 reminder
            self._inject_reminders_for_user_intent(user_message)

            # [SYS-SKILL-CANDIDATE-REMINDER / 问题0 修复] 独立于关键词自动激活
            # 开关：只要有"看起来匹配但尚未激活"的 skill，就注入一条明确点名的
            # reminder，提示模型先 skill_list/skill_activate 再动手，而不是让
            # 模型自己在一大段 system prompt 目录里判断要不要激活。
            self._inject_reminders_for_skill_candidates(user_message)

            # [决策/取舍知识提炼计划 5.4 节，路径 B] 启发式门控命中时自动召回
            # 相关历史决策，注入方式与上面的 reminder 完全一致（一次性、同轮去重）。
            self._maybe_recall_decisions_for_user_message(user_message)

            # [SYS-ENSEMBLE] AUTO 模式：框架自行判断本轮是否值得做 best-of-N，
            # 判断为"值得"时，用多个 SubAgent（不同上下文）跑完整这一轮任务，
            # 评判/合并出最终结果后直接作为本轮回复，跳过常规单路 _agentic_loop()。
            # 仅在 mode=auto 且 granularity 允许 subagent 且 TaskManager 已初始化时生效；
            # 任何异常都安静回退到正常单路流程，不影响主流程稳定性。
            _ensemble_used = False
            if getattr(self.cfg, "ensemble", None) is not None and self.cfg.ensemble.mode == "auto" \
                    and self.cfg.ensemble.granularity in ("subagent", "both"):
                try:
                    from mini_agent.ensemble import should_trigger_ensemble, run_subagent_ensemble
                    from mini_agent.tools.orchestration import get_task_manager

                    decision = should_trigger_ensemble(user_message, self.cfg, llm_helper=self.llm_helper)
                    if decision.trigger and get_task_manager() is not None:
                        R.print_info(
                            f"[ensemble] auto-triggered (source={decision.source}): {decision.reason}"
                        )
                        ens_result = run_subagent_ensemble(
                            self.cfg, user_message,
                            strategy=decision.judge_strategy,
                            session_id=getattr(self, "session_id", None),
                            llm_helper=self.llm_helper,
                        )
                        if ens_result.final_content:
                            result = ens_result.final_content
                            from mini_agent.llm.base import LLMResponse, LLMUsage
                            self._hist.append_assistant(LLMResponse(
                                text=result, tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
                            ))
                            R.print_assistant_prefix(agent_name=self.cfg.agent_name)
                            R.print_markdown(result)
                            _ensemble_used = True
                except Exception as _e:
                    from mini_agent.errors import log_exception
                    log_exception(_e, where='mini_agent.agent.turn_loop.TurnLoopMixin.run_turn')
                    R.print_warning(f"[ensemble] auto-trigger 失败，回退到常规流程: {_e}")

            if not _ensemble_used:
                result = self._agentic_loop()

            # [SYS-ROLE-AGENT] output 触发：主 Agent 完成输出后，触发 output 类角色
            result = self._run_role_agents_output(user_message, result)

            # [SYS-HOOKS] TurnEnd：一轮对话结束，轮到用户输入前触发。
            # payload 包含当前历史快照（浅拷贝，供 hook 读取），以及本轮 assistant 输出。
            # hook 可返回 {"user_input": "..."} 以替代真实用户输入；
            # 否则正常等待用户输入。
            self._turn_end_user_input: "Optional[str]" = None
            try:
                from mini_agent.hooks import get_hook_manager as _get_hook_manager
                _hook_mgr = _get_hook_manager()
                if _hook_mgr is not None and _hook_mgr._all_specs("TurnEnd"):
                    _history_snapshot = [
                        {"role": m.get("role", ""), "content": m.get("content", "")}
                        for m in self._hist._history
                    ]
                    _te_result = _hook_mgr.run(
                        "TurnEnd",
                        {
                            "assistant_output": result,
                            "history": _history_snapshot,
                        },
                    )
                    if _te_result.user_input is not None:
                        self._turn_end_user_input = _te_result.user_input
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent.turn_loop.TurnLoopMixin.run_turn')
                pass  # TurnEnd hook 失败不影响主流程

            # [SYS-TURN-JUDGE] TurnEnd hook 没有接管（未配置或未返回替代输入）时，
            # 若开启了 turn_judge，则让 TurnJudgeAgent 核查一次：这到底是真的
            # 需要真人输入，还是主 Agent 遇到了技术性问题，应该自动代替用户反馈
            # 让主 Agent 继续处理。
            if self._turn_end_user_input is None:
                try:
                    self._maybe_run_turn_judge(result)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.agent.turn_loop.TurnLoopMixin.run_turn')
                    pass  # TurnJudge 失败不影响主流程，保守回退到等待真人输入

            # [SYS-SUMMARY] session 结束后写入摘要（在 save 前）
            # 摘要写入由 save_session 触发，这里只标记需要摘要
            return result
        finally:
            # 清理 turn 级上下文缓存（含 system prompt 缓存）
            self._cached_system = None
            if self._ctx_builder is not None:
                self._ctx_builder.clear_turn_cache()
            # 每轮对话后自动保存 session
            if getattr(self.cfg, 'auto_save_session', True) and self._history:
                self.save_session()

    # ── Agentic loop ───────────────────────────────────────────────────────────

    def _agentic_loop(self) -> str:
        """Keep calling the LLM until it produces a final text response (no tool calls)."""
        final_text = ""
        loop_count = 0
        # [具身改进 B1] 本轮（一次 _agentic_loop 调用）内是否已经注入过元认知提示，
        # 避免 frustration 持续超阈值时每个 loop_count 都重复注入刷屏。
        _meta_hint_emitted_this_call = False
        # [SYS-FORMAT-CORRECTION] 本轮（一次 _agentic_loop 调用）内已消耗的格式纠错重试次数。
        # 与 loop_count 分开计数：纠错重试不应挤占 max_turns 预算，
        # 但仍需独立上限防止模型持续输出坏格式导致死循环。
        format_correction_retries = 0
        _hard_loop_count = 0

        _max_turns_policy = getattr(self.cfg, "max_turns_on_limit", "stop")
        _max_turns_hard_limit = getattr(self.cfg, "max_turns_hard_limit", self.cfg.max_turns)
        _turns_budget = self.cfg.max_turns

        while True:
            if loop_count >= _turns_budget:
                if _max_turns_policy in ("continue", "compact_continue") and loop_count < _max_turns_hard_limit:
                    if _max_turns_policy == "compact_continue":
                        R.print_warning(f"[max-turns] hit {_turns_budget}, policy=compact_continue, compacting then continuing.")
                        try:
                            self._cached_system = None
                            self.compact_with_skills()
                            self._cached_system = None
                        except Exception as _mini_agent_exc:
                            from mini_agent.errors import log_exception
                            log_exception(_mini_agent_exc, where='mini_agent.agent.turn_loop.TurnLoopMixin._agentic_loop')
                            R.print_warning(f"[max-turns] auto-compact failed: {_mini_agent_exc}")
                    else:
                        R.print_warning(f"[max-turns] hit {_turns_budget}, policy=continue, auto-continuing (hard limit {_max_turns_hard_limit}).")
                    self._hist.append_user("继续")
                    _turns_budget = min(_turns_budget + self.cfg.max_turns, _max_turns_hard_limit)
                else:
                    break
            loop_count += 1
            _hard_loop_count += 1

            # [SYS-HOT-RELOAD] 检查 skills / agent profiles 是否有文件变化
            if self._hot_reloader.has_watches:
                _hr_reports = self._hot_reloader.poll()
                for _hr in _hr_reports:
                    if _hr.has_changes:
                        # 使 system prompt 缓存失效（包含 skill 目录和 agent 目录）
                        self._cached_system = None
                        R.print_info(f"[hot-reload] {_hr.summary()}")

            # [SYS-TOKEN] token 预估 + 自动压缩
            # _build_system() 命中 turn 级缓存，与后续 _call_llm() 共享同一字符串，
            # 不重复构建 system prompt。
            _budget_pct = 0.0  # [具身改进 B1] 默认值，token 预估关闭时 proprioception 仍可读取（视为 0）
            if self.cfg.token_estimate_enabled or self.cfg.auto_compress_enabled:
                from mini_agent.llm.system_tool_call import convert_tool_use_to_text
                # [Stage 6 / 6.1] build_system 追踪（首次调用时有实际构建成本）
                if self._tracer:
                    with self._tracer.span("build_system", turn_id=self.stats.turns) as _bsp:
                        _sys_preview = self._build_system()
                        _msgs_preview = convert_tool_use_to_text(self._history)
                        _est = estimate_messages_tokens(_msgs_preview, _sys_preview)
                        _sys_tokens = estimate_messages_tokens([], _sys_preview)
                        _hist_tokens = _est - _sys_tokens
                        _bsp["context_breakdown"] = {
                            "system_base": _sys_tokens,
                            "history":     _hist_tokens,
                            "total":       _est,
                        }
                else:
                    _sys_preview = self._build_system()   # 首次调用时填充缓存
                    _msgs_preview = convert_tool_use_to_text(self._history)
                    _est = estimate_messages_tokens(_msgs_preview, _sys_preview)
                _ctx_window = self._resolve_context_window()
                _budget_pct = _est / max(_ctx_window, 1)
                # 注：verbose 场景下的详细 system/skill/history 拆分打印已挪到
                # _agentic_loop 结束处统一重算并打印一次，这里不再逐次打印
                # （循环内此时的 history 还没算上本次迭代的 LLM 回复，打印会是
                # 上一步的旧值）。
            # [SYS-COMPACT-TRIGGERS] 组合触发器检查：token 阈值 / 轮次计数 /
            # 工具调用计数 / 冗余检测 / 话题切换，任一命中即可能触发 compact。
            # 独立于 token_estimate_enabled 之外运行（多数子触发器不依赖 token 估算）。
            self._turns_since_last_compact = self.stats.turns - self._last_compact_turns
            from mini_agent.history.triggers import TriggerContext
            _trigger_ctx = TriggerContext(
                history=self._history,
                budget_pct=_budget_pct,
                turns=self.stats.turns,
                tool_calls=self.stats.tool_calls,
                last_compact_turns=self._last_compact_turns,
                last_compact_tool_calls=self._last_compact_tool_calls,
                turns_since_last_compact=self._turns_since_last_compact,
                llm_client=self._llm,
            )
            # [BUGFIX 重入保护] compact_with_skills() 的"正常路径"会调用
            # run_turn()，从而重新进入本函数（_agentic_loop）。若此时
            # 已经处于 compact 执行过程中（_compacting_in_progress=True），
            # 直接跳过本轮触发检查，避免压缩尚未完成、历史尚未清空时
            # 又一次被 token_threshold 等触发器命中，递归/重复触发 compact。
            _trigger_result = None
            if not getattr(self, "_compacting_in_progress", False):
                _trigger_result = self._compact_triggers.check(_trigger_ctx, self.cfg)
            if _trigger_result is not None and _trigger_result.triggered:
                _did_compact = self._maybe_run_compact(_trigger_result)
                # [AUTO-COMPACT-CONTINUE] 压缩真正执行后，自动注入一条模拟的
                # "继续"用户消息，让 agent 自动接着往下走，而不是把压缩后的
                # 历史晾在那里、等真人手动敲一句话才会继续（跟 /compact_continue
                # 手动命令的"压缩后自动续接"行为保持一致）。
                #
                # 只在 loop_count > 1（即这是同一个 run_turn 内、已经在
                # 多轮工具调用过程中触发的压缩）时才注入：如果是 loop_count==1
                # （刚追加完用户这次的真实输入、还没来得及回复过）触发的压缩，
                # 说明本来就要立即用压缩后的历史回答用户这次的真实提问，
                # 不需要、也不应该在用户消息后面再插一条"继续"把话题带偏。
                if _did_compact and loop_count > 1:
                    self._hist.append_user("继续")

            # [wiki 提取层与组织层改进计划 E1] 独立于 compact 的轻量抽取
            # 触发检查：纯规则扫描，成本极低，默认关闭
            # （cfg.compress.extraction_trigger_enabled）。与上面的 compact
            # 触发器检查相互独立，不占用同一个"本轮是否触发了什么"的判断，
            # 允许同一轮里既不触发 compact、又触发一次独立抽取。
            try:
                self._hist.maybe_trigger_extraction(llm_client=self._llm)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent.turn_loop.TurnLoopMixin._agentic_loop')
                pass

            # [具身改进 B1] 本体感知快照：每轮 LLM 调用前 sense 一次。
            # O(1)，不调用 LLM；frustration 超阈值时注入一次元认知提示，
            # 建议模型停下来向用户汇报困境而不是盲目重试——但不强制中断循环，
            # 决定权仍在模型/用户手里（前馈控制 + 保留人类控制权）。
            if self._proprioception is not None:
                _pp_state = self._proprioception.sense(
                    cognitive_load_ratio=_budget_pct,
                    recent_tool_names=self._last_tool_names,
                    assistant_text=final_text,
                    turns_used=loop_count,
                    max_turns=self.cfg.max_turns,
                )
                if self.cfg.proprioception.verbose:
                    R.print_info(f"[proprioception] {_pp_state.to_dict()}")
                if self.cfg.proprioception.trace_enabled and self._tracer:
                    self._tracer.record_internal_state(
                        turn_id=self.stats.turns, state=_pp_state.to_dict()
                    )
                if (
                    not _meta_hint_emitted_this_call
                    and _pp_state.frustration >= self.cfg.proprioception.frustration_threshold
                    and self._proprioception.consecutive_failures
                        >= self.cfg.proprioception.consecutive_failure_threshold
                ):
                    _meta_hint_emitted_this_call = True
                    self._hist.append_user(
                        "[proprioception] 系统提示（非用户输入）：最近连续 "
                        f"{self._proprioception.consecutive_failures} 次工具调用失败，"
                        "挫败感信号偏高。建议先停下来总结目前卡在哪里、是否需要换一种方法，"
                        "或者直接向用户说明遇到的困难并请求指引，而不是继续重复同样的尝试。"
                    )

                # [B1 → Stage 9 信号桥接] 把本轮快照落盘，供 ResourceArbiter（跑在
                # daemon 后台 tick 里，不持有活跃 Agent 引用）读取，避免一个正在
                # 反复受挫的 Agent 同时还在后台跑高置信度要求的自主探索。只在
                # frustration 有意义变化时才写，避免每轮都触发磁盘 IO。
                if _pp_state.frustration != self._last_written_frustration:
                    _prev_frustration = self._last_written_frustration
                    self._last_written_frustration = _pp_state.frustration
                    try:
                        self._write_proprioception_snapshot(_pp_state)
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.agent')
                        pass
                    # [事件总线接入] 只在"越过阈值边沿"时发布 instant 事件，
                    # 不是每次快照变化都发——快照文件本身已经覆盖了"任意时刻
                    # 查询当前状态"的需求，事件总线只负责"状态刚刚变差了，
                    # 应该有人尽快看一眼"这类边沿通知，避免事件日志被
                    # 高频采样信号刷屏。
                    try:
                        _threshold = self.cfg.proprioception.frustration_threshold
                        if _pp_state.frustration >= _threshold and _prev_frustration < _threshold:
                            from mini_agent.perception import system_events as _se
                            from mini_agent.storage.paths import AgentPaths as _AP
                            _se.publish(
                                _AP(self.cfg.project_root),
                                source=f"session:{self._session.id if self._session else 'unknown'}",
                                event_type="proprioception.frustration_spike",
                                tier="instant",
                                payload={
                                    "frustration": round(_pp_state.frustration, 3),
                                    "consecutive_failures": self._proprioception.consecutive_failures
                                        if self._proprioception is not None else 0,
                                },
                            )
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.agent.turn_loop.TurnLoopMixin._agentic_loop')
                        pass  # 事件发布是旁路增强，绝不能影响主循环

                # [方案三] uncertainty 信号接入事件总线：限流发布（连续 N 轮
                # 都超阈值才发），与上面 frustration 的"边沿事件"节奏不同，
                # 不依赖 _pp_state.frustration 是否变化，独立判断。
                self._maybe_publish_uncertainty_signal(_pp_state)

            # [具身改进 C1] AgentSelfModel 快变量更新：把刚 sense() 到的内部状态
            # 同步给 self_model，ContextBuilder.build() 下次调用时会自动读取。
            if self._self_model is not None and self._proprioception is not None:
                try:
                    self._self_model.update_internal_state(_pp_state)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.agent')
                    pass

            # [Stage 6 / 6.1] call_llm 追踪
            # [AUTO-COMPACT] 捕获上下文窗口超限错误，自动压缩历史后在同一 loop 内重试。
            # LLMContextWindowError 已被 RetryPolicy.non_retryable_exceptions 排除出重试
            # 循环（所以到这里时重试预算已用尽、且没有等待时间），直接触发 compact。
            _auto_compact_done = False
            while True:
                try:
                    if self._tracer:
                        _turn_id = self.stats.turns
                        with self._tracer.span("call_llm", turn_id=_turn_id) as _sp:
                            response = self._call_llm()
                            _sp["input_tokens"] = response.usage.input_tokens
                            _sp["output_tokens"] = response.usage.output_tokens
                    else:
                        response = self._call_llm()
                    break  # 成功，跳出内层 while
                except Exception as _llm_exc:
                    from mini_agent.llm.base import LLMContextWindowError as _CWErr
                    if not isinstance(_llm_exc, _CWErr):
                        raise  # 非 context window 错误：向上传播，不做 compact
                    if _auto_compact_done:
                        # compact 后再次超限（历史压缩后仍然太长，罕见但可能）：
                        # 放弃本轮，告知用户
                        R.print_error(
                            "[auto-compact] 压缩后上下文仍超出限制，无法继续。"
                            "请尝试 /compact 手动压缩或开始新对话。"
                        )
                        raise
                    R.print_warning(
                        f"[auto-compact] 上下文窗口超限，自动压缩历史… "
                        f"({type(_llm_exc).__name__})"
                    )
                    try:
                        self.compact_with_skills()
                        # compact 完成后重置 cached_system，强制用新历史重建 system prompt
                        self._cached_system = None
                        _auto_compact_done = True
                        # 继续内层 while，用压缩后的历史重新调用 LLM
                    except Exception as _compact_exc:
                        R.print_error(f"[auto-compact] 压缩失败: {_compact_exc}")
                        raise _llm_exc from _compact_exc
            final_text = response.text
            self.stats.input_tokens += response.usage.input_tokens
            self.stats.output_tokens += response.usage.output_tokens

            # 将 LLMResponse 写入对话历史（provider 无关格式）
            self._append_assistant_response(response)

            # [SYS-REMINDER] assistant 文本输出模式触发
            if response.text:
                self._inject_reminders_for_pattern(response.text)

            # [SYS-SKILL-DETECT] 推理完成后检测哪些 skill 被真正使用
            # 只有「实际使用」的 skill 才更新 tracker LRU 权重
            if self.skill_loader and response.text:
                used = self.skill_loader.record_usage(response.text)
                if used and self.cfg.verbose:
                    R.print_info(f"[skill-detect] used: {used}")

            if not response.has_tool_calls:
                # [SYS-FORMAT-CORRECTION] 解析失败后的第二轮检查：
                # 模型输出里是否有"看起来想调用工具但格式损坏"的痕迹
                # （标签未闭合、标签角色混淆、JSON 损坏等）。命中则不直接
                # break——以 user 角色注入纠错提示，让模型重新输出一次。
                if (
                    self.cfg.format_correction.enabled
                    and format_correction_retries < self.cfg.format_correction.max_retries_per_turn
                ):
                    issue = self._detect_format_issue(response.text)
                    if issue is not None:
                        format_correction_retries += 1
                        self._hist.append_format_correction(issue.message)
                        if self.cfg.format_correction.verbose:
                            R.print_info(
                                f"[format-correction] 检测到格式问题: {issue.issue_type!r}，"
                                f"已注入纠错提示，重试 {format_correction_retries}/"
                                f"{self.cfg.format_correction.max_retries_per_turn}"
                            )
                        continue  # 跳过 break，回到循环顶部重新调用一次 LLM（仍计入 loop_count/max_turns 预算）
                # [SYS-HOOKS] Stop：LLM 准备结束本轮输出（无工具调用）
                try:
                    from mini_agent.hooks import get_hook_manager as _ghm_stop
                    _hm_stop = _ghm_stop()
                    if _hm_stop is not None:
                        _stop_res = _hm_stop.run("Stop", {
                            "text": response.text,
                            "turn": self.stats.turns,
                        })
                        # Stop hook 可返回 context 注入，作为后续 user 消息前缀
                        # （blocked 字段对 Stop 无意义，主流程不可中断）
                        if _stop_res.context:
                            self._hist.append_user(
                                f"[stop hook context] {_stop_res.context}"
                            )
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.agent')
                    pass
                break

            # 执行工具调用，结果写回历史
            # [Stage 6 / 6.1] execute_tools 追踪
            if self._tracer:
                with self._tracer.span("execute_tools", turn_id=self.stats.turns) as _sp:
                    tool_results, result_strs = self._execute_tools(response)
                    _sp["tool_count"] = len(response.tool_calls)
                    from mini_agent.perception.lesson_rules import is_tool_error as _ite
                    _sp["tool_error_count"] = sum(1 for r in result_strs if _ite(r))
                    # [具身改进 工具透明性] 把本批工具调用按意图分组，写入 trace
                    # 的 action_events 字段——给"调用了 read_file×3 + patch×2"
                    # 这类原始流水账加一层"做了一次代码重构"的语义标注，
                    # 不改变 history 本身，只在可观测性侧补充。
                    try:
                        from mini_agent.perception.intent_action_mapper import IntentActionMapper
                        _events = IntentActionMapper.group_calls(response.tool_calls, result_strs)
                        if _events:
                            _sp["action_events"] = [e.to_dict() for e in _events]
                            self._last_action_events = _events
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.agent')
                        pass
            else:
                tool_results, result_strs = self._execute_tools(response)
            self._hist.append_tool_results(response.tool_calls, result_strs)

            # [具身改进 B1] 更新本体感知状态：记录最近工具名（供下一轮 risk_perception
            # 估算）+ 按每个工具结果是否出错累积/衰减 frustration。
            if self._proprioception is not None:
                self._last_tool_names = [tc.name for tc in response.tool_calls]
                from mini_agent.perception.lesson_rules import is_tool_error as _ite_pp
                for _r in result_strs:
                    self._proprioception.record_tool_outcome(success=not _ite_pp(_r))

            # [SYS-REMINDER] 工具执行后：检查出错 / 成功输出，注入对应 reminder
            self._inject_reminders_for_tool_results(response.tool_calls, result_strs)

            # [SYS-ROLE-AGENT] tool_use 触发：CoachAgent 等在特定工具调用后给出建议
            self._trigger_role_agents_tool_use(response.tool_calls, result_strs)

        self._last_turn_hit_max_turns = loop_count >= _turns_budget
        if self._last_turn_hit_max_turns:
            R.print_warning(f"Reached max turns ({loop_count}).")

        # [SYS-TOKEN] 本轮（turn）结束，重新估算一次并只打印这一次。
        # 注意：不能直接复用循环内保存的 _last_token_breakdown ——
        # 那是每次 while 迭代"顶部"算的，取自当时的 self._history，
        # 而当次迭代的 LLM 回复 / 工具结果是在那之后才 append 进 history 的，
        # 所以循环内的值永远落后最后一步。这里用当前最终的 history 重新算一遍。
        if self.cfg.token_estimate_enabled and self.cfg.verbose:
            from mini_agent.llm.system_tool_call import convert_tool_use_to_text
            _final_sys = self._build_system()
            _final_msgs = convert_tool_use_to_text(self._history)
            _final_est = estimate_messages_tokens(_final_msgs, _final_sys)
            _final_sys_tokens = estimate_messages_tokens([], _final_sys)
            _final_hist_tokens = _final_est - _final_sys_tokens
            _final_skill_text = ""
            if self._ctx_builder is not None:
                _final_skill_text = getattr(self._ctx_builder, "last_skill_text", "") or ""
            _final_skill_tokens = estimate_tokens(_final_skill_text) if _final_skill_text else 0
            _final_sys_only = max(_final_sys_tokens - _final_skill_tokens, 0)
            _final_ctx_window = self._resolve_context_window()
            _final_budget_pct = _final_est / max(_final_ctx_window, 1)
            R.print_info(
                f"[token] ~{_final_est:,} tokens ({_final_budget_pct:.0%} of {_final_ctx_window:,}) "
                f"| system={_final_sys_only:,} skill={_final_skill_tokens:,} "
                f"history={_final_hist_tokens:,} total={_final_est:,}"
            )

        return final_text

    # ── LLM 调用 ───────────────────────────────────────────────────────────────

    def _append_assistant_response(self, response: LLMResponse) -> None:
        """
        将 LLMResponse 转换为对话历史条目（委托给 HistoryManager）。
        使用 provider 无关的通用格式（Anthropic/OpenAI 均可接受）。
        <skill_used> 标签在此处剥离，不写入历史（避免污染后续对话上下文）。
        """
        self._hist.append_assistant(response)

    # ── Reminder 注入辅助方法 ──────────────────────────────────────────────────

    def _execute_tools(self, response: LLMResponse) -> tuple[list, list[str]]:
        """
        [已整合到 ToolExecutor.execute_all]

        代理到 self._tool_executor.execute_all()。
        原有的权限检查、缓存、截断、文件追踪、hook、lesson、dedup、tracer
        全部在 ToolExecutor 中统一实现，此处仅做转发，保持调用点不变。
        """
        return self._tool_executor.execute_all(response)

    def _maybe_trim_result(self, tool_name: str, result: str) -> str:
        """
        [已废弃 / 整合到 ToolExecutor._trim_result]

        截断逻辑已迁移到 tool_executor.py，保留此方法仅作兼容占位。
        实际不再被调用（_execute_tools 已代理到 ToolExecutor.execute_all）。
        """
        return self._tool_executor._trim_result(tool_name, result)

    def _build_tool_result_message(self, tool_calls, results: list[str]) -> dict:
        """
        构造回注工具结果的 user 消息。
        统一使用 <tool_result> 文本格式（与 tool_call_protocol.md 对应）。
        """
        from mini_agent.llm.system_tool_call import render_tool_results
        content = render_tool_results(tool_calls, results)
        return {"role": "user", "content": content}

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _resolve_context_window(self) -> int:
        """
        [SYS-TOKEN][BUGFIX] 解析用于计算 token 占用率的"分母"——模型的
        上下文窗口大小（而不是 cfg.max_tokens，那是单次响应的输出 token
        上限，默认只有 8192，跟上下文窗口是完全不同的两个概念）。

        修复前的 bug：`_budget_pct = _est / cfg.max_tokens` 把估算出的
        "历史 + system prompt 的输入 token 数" 除以了"单次输出上限"，
        分母天然远小于真实上下文窗口（如 8192 vs 真实的 120000），
        导致占用率轻易算出 100%+（例如报错里看到的 109%），
        token_threshold 触发器几乎必然频繁误报、频繁强制 compact。

        解析优先级（与 compaction.py::_should_use_chunked_compact 保持一致）：
          1. 当前 LLM client 若暴露了 context_window 属性（provider 自带的
             准确值），优先使用；
          2. 否则用 cfg.compress.model_context_window（用户在配置文件里
             显式指定的窗口大小，如 120000）；
          3. 都没有时，保守兜底为 100_000。
        """
        return (
            getattr(getattr(self, "_llm", None), "context_window", None)
            or getattr(self.cfg.compress, "model_context_window", None)
            or 100_000  # 保守默认值
        )

    def _build_system(self) -> str:
        """
        [SYS-SYSTEM] 组装 system prompt。

        委托给 ContextBuilder.build()，利用其 turn 级缓存：
        - skill 目录：只在 skill 集合变化时重建
        - 记忆检索：turn 开始时 refresh_turn_context() 预填充，同 turn 内不重复检索
        - 项目快照：通过 getter 懒取

        [SYS-SYSCACHE] turn 内缓存：_cached_system 在同一 turn 的首次调用时填充，
        后续 _call_llm()（含 token 估算）直接复用，turn 结束时由 clear_turn_cache() 清理。
        """
        if self._cached_system is not None:
            return self._cached_system

        if self._ctx_builder is not None:
            result = self._ctx_builder.build(self._history)
        else:
            # 兜底：ContextBuilder 未初始化时直接构建（不应发生）
            result = build_system_prompt(
                self.cfg,
                self.skill_loader.active if self.skill_loader else [],
            )
        self._cached_system = result
        return result

    def _build_tool_schemas(self) -> list[ToolSchema]:
        """将 ToolRegistry 的工具定义转换为 provider 无关的 ToolSchema 列表。"""
        return [
            ToolSchema(
                name=td.name,
                description=td.description,
                input_schema=td.input_schema,
            )
            for td in (self.registry.get(n) for n in self.registry.names)
            if td is not None
        ]