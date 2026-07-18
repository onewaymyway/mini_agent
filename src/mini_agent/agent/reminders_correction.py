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


class RemindersCorrectionMixin:
    """情境提醒注入与人类反馈纠正检测。"""

    def _reminder_already_in_turn(self, reminder_name: str) -> bool:
        """检查当前 turn 内是否已注入过同名 reminder（去重守卫）。

        "当前 turn" 定义为：从最近一条 user_input 条目之后到历史末尾。
        只扫 _type=reminder 的条目，按 content 中是否含 reminder_name 判断。
        这样同一个 reminder 在同一轮内只注入一次，避免历史里堆积重复噪音。
        """
        from mini_agent.history.entry import HType
        history = self._history
        # 找最近一条 user_input 的位置
        turn_start = 0
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("_type") == HType.USER_INPUT:
                turn_start = i + 1
                break
        # 扫 turn_start 之后的 reminder 条目
        for msg in history[turn_start:]:
            if msg.get("_type") == HType.REMINDER:
                content = msg.get("content", "")
                if isinstance(content, str) and reminder_name in content:
                    return True
        return False

    def _inject_reminder(self, reminder) -> None:
        """将单条 reminder 格式化后追加到对话历史（带 _type=reminder）。

        同一轮内同名 reminder 只注入一次（去重），避免历史里堆积重复噪音。
        """
        if getattr(self, "_reminder_mgr", None) is None:
            return
        # 去重：当前 turn 已存在同名 reminder 则跳过
        if self._reminder_already_in_turn(reminder.name):
            if getattr(self.cfg.reminder, "verbose", False):
                R.print_info(f"[reminder] 跳过重复注入: {reminder.name!r}")
            return
        msg = ReminderManager.format_injection(reminder)
        # 通过 append_raw_dict 追加，msg 中已有 role/content，补上 _type
        from mini_agent.history.entry import HType
        msg_typed = dict(msg, _type=HType.REMINDER)
        self._hist.append_raw_dict(msg_typed)
        if getattr(self.cfg.reminder, "verbose", False):
            R.print_info(f"[reminder] 注入: {reminder.name!r} → role={msg['role']}:{reminder.content}")

    def _inject_reminders_for_user_intent(self, user_message: str) -> None:
        """用户消息进入时检查并注入 user_intent 类型 reminder。"""
        if getattr(self, "_reminder_mgr", None) is None:
            return
        for r in self._reminder_mgr.check_user_intent(user_message):
            self._inject_reminder(r)

    def _maybe_recall_decisions_for_user_message(self, user_message: str) -> None:
        """[决策/取舍知识提炼计划 5.4 节，路径 B] 每轮用户消息进入时的启发式门控。

        跟 `_inject_reminders_for_user_intent` 是同一个挂载时机，但触发条件不同：
        不是靠预先写好的 reminder 规则文件匹配，而是靠
        `decision_recall.should_trigger_recall()` 便宜的关键词判断"这轮像不像
        在重新讨论一个方案取舍"，命中才真正调用 `wiki_shelf_search` 做检索
        （成本比常驻 reminder 高，所以必须先过一道门控）。

        默认关闭（`CompressConfig.decision_recall_turn_gate_enabled=False`），
        先观察启发式命中率再决定要不要默认打开，避免对正常对话造成噪音。
        命中后走跟 lesson reminder 一样的一次性注入 + 同轮去重逻辑
        （`_inject_reminder` 内部的 `_reminder_already_in_turn` 守卫），不会在
        历史里常驻占用 context。任何异常都静默降级，不影响正常对话。
        """
        if not getattr(self.cfg.compress, "decision_recall_turn_gate_enabled", False):
            return
        if not isinstance(user_message, str) or not user_message.strip():
            return
        try:
            from mini_agent.evolution.decision_recall import (
                should_trigger_recall, recall_related_decisions,
            )
            if not should_trigger_recall(user_message):
                return

            from mini_agent.storage.paths import AgentPaths
            paths = AgentPaths(self.cfg.project_root)

            llm_call = None
            _pool = getattr(self, "_client_pool", None)
            if _pool is not None:
                from mini_agent.perception.memory_factory import build_llm_call
                llm_call = lambda prompt: build_llm_call(_pool.current_client)(prompt)

            k = getattr(self.cfg.compress, "decision_recall_gate_k", 5)
            note = recall_related_decisions(paths, user_message, k=k, llm_call=llm_call)
            if not note:
                return

            from mini_agent.reminders.loader import Reminder, TRIGGER_USER_INTENT
            reminder = Reminder(
                name="decision_recall_gate",
                trigger_event=TRIGGER_USER_INTENT,
                inject_as="user",
                content=note,
            )
            self._inject_reminder(reminder)
        except Exception:
            pass  # 决策召回失败不应影响正常对话主流程

    def _detect_and_record_correction(self, user_message: str) -> bool:
        """
        [SYS-LESSON] 人类反馈纠正检测（Stage 1.4）。

        在新追加的用户消息中检测纠正性短语；命中时立即生成
        entry_type="lesson", source="human_feedback" 的记忆条目并写入。
        "上一轮 agent 做了什么"取最近一条 assistant 回复作为 trigger 的上下文。

        返回是否命中（供调用方/测试断言；Stage 1.5 的 (e)dit 接入复用本方法的
        核心逻辑，故拆成独立方法而非内联在 run_turn 里）。
        """
        if not getattr(self.cfg.memory, "correction_detection_enabled", True):
            return False
        if self._memory is None or not self.cfg.memory.enabled:
            return False
        if not isinstance(user_message, str):
            return False

        from mini_agent.perception.correction_detector import (
            detect_correction, make_correction_lesson_fields,
        )
        if not detect_correction(user_message):
            return False

        # 取最近一条 assistant 回复作为"上一轮 agent 做了什么"的上下文
        from mini_agent.history.entry import HType
        prior_action = ""
        for msg in reversed(self._history):
            if msg.get("_type") == HType.ASSISTANT_REPLY or (
                msg.get("_type") is None and msg.get("role") == "assistant"
            ):
                content = msg.get("content", "")
                prior_action = content if isinstance(content, str) else ""
                break

        fields = make_correction_lesson_fields(user_message, prior_action=prior_action)
        entry = MemoryEntry(
            session_id=self._session.id if self._session else "",
            summary="",
            key_outcomes=[],
            tags=["lesson", "human_feedback"],
            model=self.cfg.model,
            entry_type="lesson",
            occurrence_count=1,
            **fields,
        )
        if entry.scope == "global" and self._global_memory:
            self._global_memory.add(entry)
        else:
            self._memory.add(entry)
        self._append_memory_delta(entry)

        # [改进1+5] 人类纠正 → 定位刚才被检索命中、可能已经过时的旧知识 →
        # 标记冲突/推翻，而不是让新旧知识并存靠时间衰减慢慢覆盖。
        # 优先用 self._memory 的 library（project scope 更可能是本次纠正的
        # 对象），global 侧同样尝试一遍（同一批 injected_ids 里可能混有
        # 来自 global 的记忆，mark_stale_from_correction 对查不到的 id
        # 会静默跳过，不会误伤）。
        try:
            injected_ids = list(getattr(self._ctx_builder, "last_injected_memory_ids", []) or [])
            if injected_ids:
                for backend in (self._memory, self._global_memory):
                    library = getattr(backend, "library", None) if backend else None
                    if library is not None:
                        library.mark_stale_from_correction(
                            backend, injected_ids, correction_text=user_message,
                        )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent.mark_stale_from_correction')

        # wiki 改进计划 P2：会话级正面经验路径（agent/profile.py::
        # _generate_and_save_summary）需要知道"这个 session 有没有发生过
        # 纠正"，才能判断要不要在 session 结束时补一条正面经验。这里只做
        # 计数，不做任何写入，失败也不应该影响纠正检测本身已经完成的工作。
        try:
            self._session_correction_count = getattr(self, "_session_correction_count", 0) + 1
        except Exception:
            pass

        return True

    def _on_edit_detected(self, edit: dict) -> None:
        """
        [SYS-LESSON] (e)dit 审批编辑事件回调（Stage 1.5）。

        由 ToolExecutor 在检测到 PermissionGuard.last_edit 后调用。对应设计文档
        16.1 节："把编辑后的内容追加为一条 user 消息（_type="user_correction"），
        这条消息对 Phase B 的纠正检测也是高质量的人类反馈信号"。

        做两件事：
        1. 把编辑内容追加为一条 _type=user_correction 的 history 消息
           （计入对话上下文，让 LLM 看到用户做了什么修改）
        2. 复用 Stage 1.4 的纠正检测逻辑，尝试生成 source=human_feedback 的 lesson
           （编辑内容本身未必含纠正性短语，检测不到时静默跳过，不是所有编辑都构成"纠正"）
        """
        tool_name = edit.get("tool_name", "")
        original = edit.get("original", "")
        edited = edit.get("edited", "")
        if not edited or edited == original:
            return

        correction_text = (
            f"[edited {tool_name} call] original: {original!r} → edited: {edited!r}"
        )
        from mini_agent.history.entry import make_user_correction
        self._hist.append_raw_dict(make_user_correction(correction_text))

        # 编辑内容本身可能不含"不对/应该"之类纠正短语（用户可能只是默默改了参数），
        # 这里直接当作高质量人类反馈处理，不依赖 detect_correction() 的短语匹配——
        # "用户主动编辑了 agent 提议的操作"这件事本身就是明确的纠正信号。
        if self._memory is not None and self.cfg.memory.enabled:
            try:
                from mini_agent.perception.correction_detector import make_correction_lesson_fields
                fields = make_correction_lesson_fields(
                    correction_text=f"应该是：{edited}" if tool_name == "bash" else edited,
                    prior_action=f"提议执行 {tool_name}：{original}",
                )
                entry = MemoryEntry(
                    session_id=self._session.id if self._session else "",
                    summary="",
                    key_outcomes=[],
                    tags=["lesson", "human_feedback", "edit"],
                    model=self.cfg.model,
                    entry_type="lesson",
                    occurrence_count=1,
                    **fields,
                )
                if entry.scope == "global" and self._global_memory:
                    self._global_memory.add(entry)
                else:
                    self._memory.add(entry)
                self._append_memory_delta(entry)
            except Exception:
                pass  # lesson 生成失败不应影响编辑本身已经成功写入 history
            else:
                # 与 _detect_and_record_correction 一致：编辑类纠正也计入
                # session 级计数（wiki 改进计划 P2）。放在 else 分支：只有
                # 上面的 lesson 记录真正成功写入才计数，避免异常路径下
                # "记录失败但仍计数"造成误判。
                try:
                    self._session_correction_count = getattr(self, "_session_correction_count", 0) + 1
                except Exception:
                    pass

    def _inject_reminders_for_tool_results(self, tool_calls, result_strs: list) -> None:
        """工具执行后，逐个工具检查 tool_error / post_tool reminder。"""
        if getattr(self, "_reminder_mgr", None) is None:
            return
        for tc, result_str in zip(tool_calls, result_strs):
            tool_name = getattr(tc, "name", "") or ""
            if _is_tool_error(result_str):
                # [Stage 7 / 15.2] 传入 error_category 供精确路由
                from mini_agent.perception.observability import classify_error as _ce
                _ecat = _ce(result_str)
                for r in self._reminder_mgr.check_tool_error(tool_name, result_str,
                                                               error_category=_ecat):
                    self._inject_reminder(r)
            else:
                for r in self._reminder_mgr.check_post_tool(tool_name, result_str):
                    self._inject_reminder(r)

    def _inject_reminders_for_pattern(self, assistant_text: str) -> None:
        """assistant 输出后检查 pattern 类型 reminder。"""
        if getattr(self, "_reminder_mgr", None) is None:
            return
        for r in self._reminder_mgr.check_assistant_text(assistant_text):
            self._inject_reminder(r)

    def _detect_format_issue(self, assistant_text: str):
        """[SYS-FORMAT-CORRECTION] 检测 assistant 输出中"格式损坏的工具调用"痕迹。

        仅在 response.has_tool_calls 为假（即 parse_tool_calls 已判定无有效
        工具调用）之后调用。判定逻辑委托给 perception.format_correction_detector
        （新增检测规则只需改那个模块，这里不需要变动）；命中后展示给模型的
        文案则统一走 reminders 系统（trigger_event: format_issue），实现
        "检测规则写死在代码里、提示文案可由用户在 reminder 文件里自定义"。

        返回 FormatIssue | None（issue.message 已经是最终要注入的文本：优先取
        reminder 系统里 issue_type 匹配到的自定义内容，找不到/未启用则退回
        format_correction_detector 自带的内置默认文案）。
        """
        from mini_agent.perception.format_correction_detector import (
            detect_format_issue, PROMPT_HEADER,
        )
        issue = detect_format_issue(assistant_text)
        if issue is None:
            return None
        if getattr(self, "_reminder_mgr", None) is not None:
            matched = self._reminder_mgr.check_format_issue(issue.issue_type)
            if matched:
                custom_body = "\n\n".join(r.content for r in matched)
                issue = type(issue)(
                    issue_type=issue.issue_type,
                    message=PROMPT_HEADER + custom_body,
                )
        return issue

    # ── Tool execution ─────────────────────────────────────────────────────────

