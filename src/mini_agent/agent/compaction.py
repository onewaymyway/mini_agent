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


class CompactionMixin:
    """历史压缩：skill compact、分块压缩、自动压缩触发。"""

    # [记事本] 记事本总字数超过该阈值时，compact 提示中追加"建议总结记事本"
    # 的引导语（不自动截断，取舍仍由 agent 通过 notepad_summarize 决定）。
    NOTEPAD_COMPACT_HINT_THRESHOLD = 20000

    def _build_notepad_compact_hint(self) -> str:
        """
        若当前记事本总字数超过阈值，返回一段追加到 compact prompt 末尾的提示文本，
        建议模型调用 notepad_summarize 合并冗余/过时条目；否则返回空字符串。
        失败时静默返回空字符串，不影响 compact 主流程。
        """
        try:
            from mini_agent.tools.notepad import get_current_notepad
            store = get_current_notepad()
            if store is None:
                return ""
            total = store.total_chars()
            if total <= self.NOTEPAD_COMPACT_HINT_THRESHOLD:
                return ""
            return (
                "\n\n---\n"
                f"Note: your notepad currently holds {total} characters across "
                f"{len(store.entries)} entries, above the "
                f"{self.NOTEPAD_COMPACT_HINT_THRESHOLD}-character guideline. "
                "After finishing the summary above, consider calling `notepad_summarize` "
                "to merge redundant or outdated notepad entries into more condensed ones. "
                "Do not delete anything still relevant to the ongoing task."
            )
        except Exception:
            return ""

    def _build_skill_compact_block(self) -> str:
        """
        按 LRU 顺序、受 budget 约束构建 skill 重附上下文块。
        无 skill_loader 或无调用记录时返回空字符串。
        """
        if not self.skill_loader:
            return ""
        compact_text, included, dropped = self.skill_loader.build_compact_context(
            include_inactive=True   # 曾经用过但已卸载的 skill 也参与竞争
        )
        budget  = getattr(self.cfg, "skill_compact_budget",     25_000)
        per_sk  = getattr(self.cfg, "skill_compact_per_skill",   5_000)

        # 有 dropped 时无论是否有 included 都警告
        if dropped:
            R.print_warning(
                f"[skill-compact] budget exhausted — "
                f"{len(included)} skill(s) included, "
                f"{len(dropped)} dropped: {dropped}"
            )

        if not compact_text:
            return ""

        header  = (
            f"\n\n## Skill Context (re-attached after compression)\n"
            f"_Budget: {budget} tokens total / {per_sk} per skill. "
            f"Included: {included}. "
            + (f"Dropped (budget exhausted): {dropped}." if dropped else "")
            + "_\n\n"
        )
        if not dropped:
            R.print_info(
                f"[skill-compact] {len(included)} skill(s) re-attached "
                f"after compression."
            )
        return header + compact_text

    def _should_use_chunked_compact(self, compact_prompt: str) -> bool:
        """
        主动预估：compact 前估算 (history + system + compact_prompt) 的 token 数，
        与模型上下文窗口比较。超限返回 True（建议走分批路径）。

        估算包含：
          - system prompt (含 skill context、reminder 等)
          - 完整 history (转为 LLM 格式)
          - compact prompt
          - 预留输出空间 (max_tokens)

        使用 tiktoken (cl100k_base) 优先，不可用时退化为字符启发式估算。
        """
        # 尊重配置开关 (在 compress 子配置下)
        if not getattr(self.cfg.compress, "compact_precheck_enabled", True):
            return False

        try:
            from mini_agent.perception.token_counter import estimate_messages_tokens
            from mini_agent.llm.system_tool_call import convert_tool_use_to_text
        except Exception:
            # 估算工具不可用，保守返回 False 走正常路径（兜底靠异常捕获）
            return False

        # 1. 构建 system prompt（同 run_turn 逻辑）
        system = self._build_system()

        # 2. 转换 history 为 LLM 格式（剥离 _type 等内部字段）
        msgs = convert_tool_use_to_text(self._history)

        # 3. 估算总 token
        history_tokens = estimate_messages_tokens(msgs, system)
        prompt_tokens = estimate_messages_tokens([{"role": "user", "content": compact_prompt}], "")
        output_reserve = getattr(self.cfg, "max_tokens", 8192)  # 预留输出空间 (来自主配置)

        total_est = history_tokens + prompt_tokens + output_reserve

        # 4. 获取模型上下文窗口
        #    [BUGFIX] 与 turn_loop.py::_resolve_context_window() 共用同一份
        #    解析逻辑（LLM client.context_window > cfg.compress.model_context_window
        #    > 100_000 保守默认值），避免两处各自维护导致口径不一致。
        model_ctx = self._resolve_context_window()

        # 5. 判断：估算总量超过上下文的 85% 视为超限（留 15% 安全边际）
        #    也可通过 cfg.compress.compact_precheck_threshold 自定义
        threshold = getattr(self.cfg.compress, "compact_precheck_threshold", 0.85)
        limit = int(model_ctx * threshold)

        if total_est > limit:
            R.print_info(
                f"[compact] Pre-check: est_tokens={total_est:,} > limit={limit:,} "
                f"(ctx={model_ctx:,}, threshold={threshold:.0%}) — will use chunked compact"
            )
            return True

        return False

    def compact_with_skills(self) -> str:
        """
        [SYS-SKILL-COMPACT] 主动触发：用 LLM 生成对话摘要，然后重附 skill 上下文。

        与 /compact 的区别：
          - /compact（旧）：仅压缩对话历史，不处理 skill
          - compact_with_skills()：先生成 LLM 摘要，再按 LRU + budget 重附 skill 内容

        可以通过以下途径触发：
          1. 命令行 /compact（已升级调用此方法）
          2. tool `compact_history`（供 agent 自主调用）
          3. 直接调用 agent.compact_with_skills()
          4. auto-compact（上下文超限时自动触发）

        实现路径（自动选择）：
          - 正常路径：历史未超限时，通过 run_turn() 发送 compact prompt，
            让 LLM 在完整历史上下文中生成高质量摘要。
          - 分批路径（chunked compact）：历史已超限，run_turn() 本身无法执行时，
            把历史按 turn 边界切成多个小批，每批独立调用 LLM 生成摘要，
            最后合并成一个统一摘要替换历史。此路径完全绕开 run_turn()，
            直接使用 _llm.chat_with_retry。

        Returns:
            摘要文本（assistant 的压缩结果），失败时返回空字符串
        """
        if not self._history:
            R.print_info("[compact] History is empty, nothing to compact.")
            return ""

        from mini_agent.prompts import pm as _pm
        compact_prompt = _pm.get_compact_prompt()
        compact_prompt += self._build_notepad_compact_hint()

        # ── 主动预估：compact 前先算 token，超限直接走分批路径 ────────────────
        if self._should_use_chunked_compact(compact_prompt):
            R.print_warning(
                "[compact] Pre-check: history + compact prompt exceeds context limit — "
                "using chunked compact directly."
            )
            try:
                result = self._compact_chunked()
                used_chunked = True
            except Exception as ce:
                R.print_error(f"[compact] Chunked compact failed: {ce}")
                return ""
        else:
            # ── 尝试正常路径：run_turn ───────────────────────────────────────────
            R.print_info("[compact] Generating summary…")
            result = ""
            used_chunked = False
            try:
                result = self.run_turn(compact_prompt)
            except Exception as e:
                from mini_agent.llm.base import LLMContextWindowError
                if isinstance(e, LLMContextWindowError):
                    # 兜底：预估漏报时捕获异常再切分批
                    R.print_warning(
                        "[compact] History exceeds context limit (fallback) — switching to chunked compact…"
                    )
                    try:
                        result = self._compact_chunked()
                        used_chunked = True
                    except Exception as ce:
                        R.print_error(f"[compact] Chunked compact failed: {ce}")
                        return ""
                else:
                    R.print_error(f"[compact] Summary generation failed: {e}")
                    return ""

        if not result:
            R.print_warning("[compact] Got empty summary, aborting.")
            return ""

        # ── 重附 skill 块 ────────────────────────────────────────────────────
        skill_block = self._build_skill_compact_block()

        from mini_agent.history.entry import (
            make_session_resume, make_compact_summary, make_skill_context
        )

        _hist = getattr(self, "_hist", None)
        strategy = "compact_chunked" if used_chunked else "compact_with_skills"

        # chunked 路径已在 _compact_chunked 内完成历史替换，
        # 正常路径需要在这里做替换（run_turn 追加了摘要轮次，需清理并重建）
        if not used_chunked:
            if _hist is not None:
                _hist._raw.append_compact_event(
                    before_count=len(self._history),
                    after_count=2,
                    strategy=strategy,
                )
            new_history: list[dict] = [
                make_session_resume("[Previous session summary]"),
                make_compact_summary(result),
            ]
            if skill_block:
                new_history.append(make_skill_context(skill_block))
            self._history.clear()
            self._history.extend(new_history)
            if _hist is not None:
                for msg in new_history:
                    _hist._raw.append(msg)
        else:
            # chunked 路径：历史已替换为 [session_resume + compact_summary]，
            # 仅追加 skill_block（如果有）
            if skill_block:
                skill_msg = make_skill_context(skill_block)
                self._history.append(skill_msg)
                if _hist is not None:
                    _hist._raw.append(skill_msg)

        if getattr(self.cfg, "auto_save_session", True):
            self.save_session()

        R.print_success("[compact] History compacted with skill context re-attached.")
        return result

    def _compact_chunked(self) -> str:
        """
        [SYS-COMPACT-CHUNKED] 分批摘要：当历史已超出上下文限制时使用。

        算法：
          1. 把 _history 按 turn 边界切成若干 chunk，每 chunk 的 token 估算
             控制在模型上下文的 50% 以内，保留足够空间给摘要 prompt 和输出。
          2. 每个 chunk 独立调用 _llm.chat_with_retry 生成小摘要（绕开 run_turn）。
          3. 若 chunk 数 > 1，再做一次合并调用，把所有小摘要归并为最终摘要。
          4. 用最终摘要原地替换 _history（[session_resume, compact_summary]）。

        调用方（compact_with_skills）负责后续追加 skill_block 和 save_session。

        Returns:
            合并后的最终摘要文本。失败时抛出异常（由调用方处理）。
        """
        from mini_agent.history.entry import (
            to_llm_messages, is_turn_boundary,
            make_session_resume, make_compact_summary,
        )
        from mini_agent.prompts import pm as _pm

        history = list(self._history)

        # ── 1. 估算 token budget（每 chunk 目标：模型上下文的 50%）────────────
        # 用粗略字符估算：1 token ≈ 4 chars（英文）/ 2 chars（中文混合取中间值）
        # 保守取 3 chars/token，给 prompt overhead 留余量
        CHARS_PER_TOKEN = 3
        # 从 cfg 或 llm 尝试获取模型最大上下文；找不到时默认 100K token
        model_ctx_tokens: int = (
            getattr(getattr(self, "_llm", None), "context_window", None)
            or getattr(self.cfg, "model_context_window", None)
            or 100_000
        )
        # 每 chunk 最多使用 50% 上下文（另 50% 留给 system prompt、chunk prompt 和输出）
        chunk_budget_chars = int(model_ctx_tokens * 0.50 * CHARS_PER_TOKEN)

        # ── 2. 按 turn 边界切分 chunk ─────────────────────────────────────────
        # 收集所有 turn 起始索引（真实用户输入）
        turn_starts: list[int] = [
            i for i, m in enumerate(history) if is_turn_boundary(m)
        ]
        if not turn_starts:
            turn_starts = [0]

        chunks: list[list[dict]] = []
        current_chunk: list[dict] = []
        current_chars = 0

        for ti, start in enumerate(turn_starts):
            end = turn_starts[ti + 1] if ti + 1 < len(turn_starts) else len(history)
            turn_msgs = history[start:end]
            turn_chars = sum(
                len(str(m.get("content", ""))) for m in turn_msgs
            )

            if current_chunk and current_chars + turn_chars > chunk_budget_chars:
                # 当前 turn 放不下，先提交当前 chunk
                chunks.append(current_chunk)
                current_chunk = list(turn_msgs)
                current_chars = turn_chars
            else:
                current_chunk.extend(turn_msgs)
                current_chars += turn_chars

        if current_chunk:
            chunks.append(current_chunk)

        # 极端情况：单个 turn 本身就超限 → 强制每个 turn 单独成 chunk
        # （不拆 turn 内部；单条消息若仍然超限，靠上面 cap_oversized_messages
        # 兜底截断，不会再导致该 chunk 的 LLM 调用直接报错）
        if not chunks:
            chunks = [[m] for m in history]

        total_chunks = len(chunks)
        R.print_info(f"[compact] Chunked compact: {total_chunks} chunk(s) from {len(history)} messages…")

        # ── 3. 对每个 chunk 独立生成摘要 ─────────────────────────────────────
        from mini_agent.prompts import pm as _pm
        chunk_summaries: list[str] = []
        system_prompt = _pm.render("system/compress_summarizer")

        for idx, chunk in enumerate(chunks):
            chunk_num = idx + 1
            R.print_info(f"[compact]   chunk {chunk_num}/{total_chunks} ({len(chunk)} messages)…")

            # 构建发给 LLM 的消息列表：chunk 内容 + chunk 摘要请求
            chunk_prompt = _pm.render(
                "user/compact_chunk_request",
                chunk_index=chunk_num,
                total_chunks=total_chunks,
            )
            from mini_agent.history.compression import (
                cap_oversized_messages, DEFAULT_MAX_MESSAGE_CHARS_FOR_COMPACT,
            )
            from mini_agent.llm.system_tool_call import convert_tool_use_to_text
            max_chars = getattr(
                self.cfg.compress, "max_message_chars_for_compact",
                DEFAULT_MAX_MESSAGE_CHARS_FOR_COMPACT,
            )
            # 与 _should_use_chunked_compact 的预估口径保持一致：
            # 先剥离 tool_use content block（转为纯文本），避免某些模型
            # （如 NVIDIA NIM 等 OpenAI 兼容接口）因 assistant content 里
            # 含 tool_use block 而报 schema 校验错误
            # （data did not match any variant of untagged enum
            #  ChatCompletionRequestAssistantMessageContent）。
            safe_chunk = convert_tool_use_to_text(to_llm_messages(chunk))
            llm_messages = cap_oversized_messages(safe_chunk, max_chars) + [
                {"role": "user", "content": chunk_prompt}
            ]

            try:
                resp = self._llm.chat_with_retry(
                    messages=llm_messages,
                    system=system_prompt,
                    tools=[],
                    max_retries=3,
                )
                chunk_text = resp.text.strip()
            except Exception as e:
                # 单 chunk 失败：用字符串摘要降级（不中断整体流程）
                R.print_warning(f"[compact]   chunk {chunk_num} LLM failed ({e}), using fallback summary.")
                from mini_agent.history.compression import _build_summary_text
                chunk_text = _build_summary_text(chunk, len(chunk))

            chunk_summaries.append(f"=== Chunk {chunk_num}/{total_chunks} ===\n{chunk_text}")

        # ── 4. 合并摘要 ───────────────────────────────────────────────────────
        if total_chunks == 1:
            final_summary = chunk_summaries[0].split("\n", 1)[-1].strip()
        else:
            R.print_info(f"[compact] Merging {total_chunks} chunk summaries…")
            merged_text = "\n\n".join(chunk_summaries)
            merge_prompt = _pm.render(
                "user/compact_merge_request",
                total_chunks=total_chunks,
                chunk_summaries=merged_text,
            )
            try:
                resp = self._llm.chat_with_retry(
                    messages=[{"role": "user", "content": merge_prompt}],
                    system=system_prompt,
                    tools=[],
                    max_retries=3,
                )
                final_summary = resp.text.strip()
            except Exception as e:
                R.print_warning(f"[compact] Merge LLM call failed ({e}), concatenating chunks.")
                final_summary = "\n\n".join(chunk_summaries)

        # ── 5. 原地替换历史 ───────────────────────────────────────────────────
        _hist = getattr(self, "_hist", None)
        new_history: list[dict] = [
            make_session_resume("[Previous session summary — chunked compact]"),
            make_compact_summary(final_summary),
        ]

        if _hist is not None:
            _hist._raw.append_compact_event(
                before_count=len(self._history),
                after_count=len(new_history),
                strategy="compact_chunked",
            )

        self._history.clear()
        self._history.extend(new_history)

        if _hist is not None:
            for msg in new_history:
                _hist._raw.append(msg)

        return final_summary

    def _maybe_run_compact(self, trigger_result) -> None:
        """
        [SYS-COMPACT-TRIGGERS] 触发器命中后的统一入口。

        根据 cfg.compress.require_confirmation 决定是否需要用户确认：
          False（默认）—— 全自动静默压缩，仅打印提示（保持原有行为）
          True          —— 先询问用户 y/n，拒绝则本次跳过（下一轮循环还会再检查一次）
        """
        R.print_info(f"[compact] 触发条件命中（{trigger_result.reason}）：{trigger_result.message}")

        if self.cfg.compress.require_confirmation:
            try:
                from mini_agent.ui.terminal import term as _term
                from mini_agent import interaction
                _term.print(
                    f"[dim]即将执行历史压缩（原因: {trigger_result.reason} — "
                    f"{trigger_result.message}），是否继续？[/dim]"
                )

                def _local_read(interrupt_event):
                    try:
                        c = _term.confirm(
                            prompt_lines=[], choices="(y)es  (n)o",
                            default="y", interrupt_event=interrupt_event,
                        )
                    except Exception:
                        return None
                    if interrupt_event.is_set():
                        return None
                    return {"confirmed": c in ("y", "yes", "")}

                # daemon 适配：之前这里直接调用 _term.confirm()（无 interrupt_event、
                # 无 HTTP 广播），daemon 进程没有本地终端时会永久阻塞，且远程
                # 客户端完全看不到这个确认请求。现在走通用交互网关双路提问。
                result = interaction.ask(
                    "compact_confirm",
                    {"reason": trigger_result.reason, "message": trigger_result.message},
                    _local_read,
                )
                confirmed = bool((result or {}).get("confirmed", True))
                choice = "y" if confirmed else "n"
            except Exception:
                # 非交互环境（如 headless/daemon 且 interaction 模块不可用）下无法弹确认，降级为自动执行
                choice = "y"
            if choice not in ("y", "yes"):
                R.print_info("[compact] 用户拒绝，本次跳过压缩。")
                return

        # 压缩后 system 内容可能变化，清除缓存强制重建
        self._cached_system = None
        self._auto_compress_history(trigger_result=trigger_result)

    def _auto_compress_history(self, trigger_result=None) -> None:
        """
        [SYS-COMPRESS] 自动压缩历史。

        委托给 HistoryManager.auto_compress()，使用 cfg.compress.strategy
        指定的可插拔压缩策略（turn_aligned / sliding_window / llm_summary /
        selective），而不是硬编码的切割逻辑，从而让 trigger 建议的
        suggested_strategy（例如话题切换建议 llm_summary）真正生效。
        """
        strategy_name = "auto_compress"
        trigger_reason = None
        if trigger_result is not None:
            trigger_reason = trigger_result.reason
            strategy_name = trigger_result.reason

        # [SYS-HOOKS] PreCompact：压缩前通知 hook（可阻止）
        try:
            from mini_agent.hooks import get_hook_manager as _ghm_pre
            _hm_pre = _ghm_pre()
            if _hm_pre is not None:
                _pre_res = _hm_pre.run("PreCompact", {
                    "history_len": len(self._history),
                    "strategy": strategy_name,
                })
                if _pre_res.blocked:
                    R.print_info("[compress] PreCompact hook blocked compression.")
                    return
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

        if len(self._history) < 6:
            return

        _hist = getattr(self, "_hist", None)
        if _hist is None:
            return

        # ── 临时切换压缩策略（若 trigger 给出了建议策略）───────────────────
        from mini_agent.history.compression import create_strategy
        original_strategy = _hist._strategy
        if trigger_result is not None and trigger_result.suggested_strategy:
            try:
                saved_cfg_strategy = self.cfg.compress.strategy
                self.cfg.compress.strategy = trigger_result.suggested_strategy
                _hist._strategy = create_strategy(self.cfg)
            except Exception:
                _hist._strategy = original_strategy
            finally:
                self.cfg.compress.strategy = saved_cfg_strategy

        before_count = len(self._history)
        try:
            _hist.auto_compress(
                skill_compact_fn=self._build_skill_compact_block,
                llm_client=self._llm,
            )
        finally:
            # 恢复原策略实例，避免临时覆盖影响后续默认压缩
            _hist._strategy = original_strategy

        # ── 若使用了 trigger_reason，重写最后一条 compact_event 的 reason ───
        # （HistoryManager.auto_compress 内部已写入不带 reason 的 compact_event，
        #  这里补充写入 trigger_reason，便于事后统计各触发器命中效果）
        if trigger_reason and _hist._raw.entries:
            for entry in reversed(_hist._raw.entries):
                if entry.get("_type") == "compact_event":
                    try:
                        import json as _json
                        payload = _json.loads(entry.get("content", "{}"))
                        payload["trigger_reason"] = trigger_reason
                        entry["content"] = _json.dumps(payload, ensure_ascii=False)
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.agent')
                        pass
                    break

        after_count = len(self._history)

        # ── 更新 last_compact 计数快照（供 turn/tool_call 计数触发器使用）───
        self._last_compact_turns = self.stats.turns
        self._last_compact_tool_calls = self.stats.tool_calls
        self._turns_since_last_compact = 0

        # [SYS-HOOKS] PostCompact：压缩完成后通知 hook（通知型）
        try:
            from mini_agent.hooks import get_hook_manager as _ghm_post
            _hm_post = _ghm_post()
            if _hm_post is not None:
                _hm_post.run("PostCompact", {
                    "history_len": after_count,
                    "strategy": strategy_name,
                    "before_count": before_count,
                    "after_count": after_count,
                })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass