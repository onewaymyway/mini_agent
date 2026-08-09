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


class ProfileMixin:
    """用户画像：读取、刷新、生成会话摘要并写入画像。"""

    def _get_profile_text(self) -> str:
        """供 ContextBuilder 注入 system prompt 的用户画像摘要（无画像则返回空串）。"""
        if not self._profile_mgr:
            return ""
        try:
            profile = self._profile_mgr.load()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent.profile.ProfileMixin._get_profile_text')
            return ""
        return profile.derived.get("summary", "") if profile.derived else ""

    def _maybe_refresh_profile(self, force: bool = False, rebuild: bool = False) -> None:
        """
        [SYS-PROFILE] 检查是否需要(重新)生成用户画像，若需要则同步生成。

        本方法预期在 _generate_and_save_summary 的后台线程中被调用（已经
        不阻塞主流程），因此这里直接同步调用 LLM，不再额外开线程。

        force=True 时跳过 should_refresh 的间隔判断，只要有记忆条目就重新生成
        （由 /profile、/memory 命令触发）。

        rebuild=True 时额外要求 generate() 走全量重建分支（不参考上一版
        画像，从最近 max_entries_for_profile 条记忆重新生成），对应显式的
        `/profile rebuild` 入口——用户觉得画像跑偏了、想从头再来的场景。
        `force=True, rebuild=False`（`/profile`/`/memory` 的默认行为）
        仍然是"立即刷新，但走增量更新，不丢弃已有画像"。
        [next_doc/memory_backfill_and_profile_update_plan.md 3.3 节]
        """
        if not self._profile_mgr:
            if force:
                _locked_print_warning("用户画像功能未开启（profile.enabled=false）。")
            return
        # 画像基于长期记忆：默认条目写入 project-scope（self._memory），
        # global-scope（self._global_memory）为可选的跨项目记忆，两者都要合并考虑。
        sources = [s for s in (self._memory, self._global_memory) if s is not None]
        if not sources:
            if force:
                _locked_print_warning("记忆功能未开启，无法生成用户画像。")
            return
        try:
            entries = []
            for s in sources:
                entries.extend(s.all_entries())
            if not entries:
                if force:
                    _locked_print_warning("暂无可用于生成画像的长期记忆。")
                return
            count = len(entries)
            if not force and not self._profile_mgr.should_refresh(count, self.cfg):
                return
            # [next_doc/memory_backfill_and_profile_update_plan.md 方向二]
            # 排序后整段传给 generate()，不再在这里截断成"最近 N 条"——
            # 是否只取最近一段、要不要参考上一版画像，由 generate() 内部
            # 根据 rebuild/是否已有画像自己决定（增量分支只需要"自上次
            # 以来新增"的那一小段，反而比这里手写的固定截断更精确）。
            entries = sorted(entries, key=lambda e: e.created_at)
            _locked_print_info("正在更新用户画像(profile)...")
            self._profile_mgr.generate(
                self._llm, entries,
                max_entries_for_profile=self.cfg.profile.max_entries_for_profile,
                stale_after_days=self.cfg.profile.stale_after_days,
                rebuild=rebuild,
            )
            _locked_print_info("用户画像(profile)已更新")
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.agent.profile.ProfileMixin._maybe_refresh_profile')
            _locked_print_warning(f"用户画像生成失败: {e}")

    def trigger_summary_and_profile(self, session_path: Optional[str] = None, force: bool = False) -> bool:
        """
        触发"生成/刷新 session 摘要 + 写入长期记忆 + 刷新用户画像"的后台任务。

        Args:
            session_path: session 文件路径；为 None 时使用当前 session 路径。
            force: 为 True 时忽略 _summary_lock 占用提示之外的逻辑限制——
                注意：仍会跳过若已有任务在运行（避免并发写同一文件），
                但会跳过"轮次间隔"门槛检查（调用方——如 /memory 命令——
                已明确要求立即生成）。

        Returns:
            True — 已成功提交后台任务；False — 因已有任务在运行而跳过。
        """
        if session_path is None:
            if not self._session_mgr or not self._session:
                _locked_print_warning("当前没有可保存的会话。")
                return False
            session_path = self._session.file_path or ""

        if self._summary_lock.locked():
            _locked_print_warning("摘要/画像生成任务正在进行中，请稍后再试。")
            return False

        _locked_print_info("正在后台生成会话摘要 / 更新长期记忆...")
        history_snapshot = list(self._history)
        threading.Thread(
            target=self._generate_and_save_summary,
            args=(session_path, history_snapshot, force),
            daemon=True,
            name="mini-agent-summary",
        ).start()
        return True

    def _generate_and_save_summary(self, session_path: str, history: Optional[list] = None, force: bool = False) -> None:
        """
        [SYS-SUMMARY] 用 LLM 生成 session 摘要，写回 session 文件，并写入长期记忆。

        本方法可能在后台线程中运行（由 save_session 触发），因此通过 `history`
        参数接收调用时刻的历史快照，不直接访问 self._history，避免与主线程并发修改冲突。

        修复：
        - 不再使用 json.loads(path.read_text()) + path.write_text() 裸读写，
          改为通过 session_mgr.save() 写回，享受原子写入 + 文件锁保护，
          避免多 SubAgent 并发时互相覆盖。
        - 写回前先将 summary 赋给 self._session.summary，save() 会自动持久化。
        """
        if not self._summary_lock.acquire(blocking=False):
            if force:
                _locked_print_warning("摘要/画像生成任务正在进行中，请稍后再试。")
            return
        try:
            if history is None:
                history = self._history
            from mini_agent.history.entry import is_real_user_input
            user_turns = [
                m["content"] for m in history
                if is_real_user_input(m) and isinstance(m.get("content"), str)
            ]
            if not user_turns:
                if force:
                    _locked_print_warning("当前会话没有可摘要的用户消息。")
                return

            turns_text = "\n".join(f"- {t[:200]}" for t in user_turns[:10])
            from mini_agent.prompts import pm
            prompt = pm.render("user/session_summary_request", turns_text=turns_text)
            resp = self._llm.chat_with_retry(
                messages=[{"role": "user", "content": prompt}],
                system=pm.render("system/summarizer"),
                tools=[],
                max_retries=10,
            )
            summary = resp.text.strip()
            if not summary:
                return

            # 写回 session（通过 session_mgr，享受原子写入 + 文件锁）
            if self._session and self._session_mgr:
                self._session.summary = summary
                self._session.summary_at_turns = self.stats.turns
                stats = {
                    "turns":             self.stats.turns,
                    "input_tokens":      self.stats.input_tokens,
                    "output_tokens":     self.stats.output_tokens,
                    "tool_calls":        self.stats.tool_calls,
                    "tool_stats":        self.stats.tool_stats,
                    "skill_activations": self.stats.skill_activations,
                }
                try:
                    self._session_mgr.save(
                        self._session,
                        history=history,
                        stats=stats,
                        raw_history=self._hist._raw,
                    )
                except Exception as e:
                    from mini_agent.errors import log_exception
                    log_exception(e, where='mini_agent.agent.profile.ProfileMixin._generate_and_save_summary')
                    _locked_print_warning(f"[summary] session re-save failed: {e}")

            # [SYS-MEMORY] 写入长期记忆
            if self._memory and self._session:
                import re as _re
                tags = list({
                    w.lower() for w in _re.findall(r"[a-zA-Z一-鿿]{3,}", summary)
                })[:8]
                entry = MemoryEntry(
                    session_id=self._session.id,
                    summary=summary,
                    key_outcomes=user_turns[:3],
                    tags=tags,
                    model=self.cfg.model,
                )
                # 根据 scope 分流：project 写项目记忆，global 写全局记忆
                if entry.scope == "global" and self._global_memory:
                    self._global_memory.upsert(entry)
                else:
                    self._memory.upsert(entry)
                # 同时写入 memory_delta.jsonl（session 审计）
                self._append_memory_delta(entry)
            _locked_print_info("会话摘要记忆已生成")

            # wiki 改进计划 P2（会话级正面经验路径）：session 结束、摘要成功
            # 生成、且这个 session 里一次纠正都没发生（self._session_correction_count
            # 由 reminders_correction.py 在纠正命中/编辑纠正时递增）——满足这两个
            # 条件才认为"这轮做得不错，值得沉淀成经验"，避免和现有的负面
            # lesson 路径一样被高频事件淹没成毫无信息量的默认行为（大多数
            # session 本来就没有纠正，如果不加限制，几乎每个 session 都会
            # 生成一条经验，反而稀释掉真正有参考价值的案例）。这里额外要求
            # 至少发生过一次工具调用，排除纯聊天、没有实际产出的 session。
            try:
                correction_count = getattr(self, "_session_correction_count", 0)
                had_tool_activity = bool(self.stats.tool_calls)
                if correction_count == 0 and had_tool_activity and self.cfg.memory.enabled:
                    from mini_agent.storage.paths import AgentPaths
                    from mini_agent.wiki.experience_writer import write_experience

                    write_experience(
                        AgentPaths(self.cfg.project_root),
                        trigger=turns_text[:300] or "（未记录具体用户请求）",
                        approach=summary,
                        outcome=(
                            f"session 全程 {self.stats.tool_calls} 次工具调用、"
                            f"{self.stats.turns} 轮，期间没有触发任何人工纠正，"
                            "判定为一次顺利完成的正面案例。"
                        ),
                        reusable=True,
                        source_kind="experience_session_reflection",
                        confidence=0.5,
                    )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent.profile.ProfileMixin._generate_and_save_summary')
                pass  # 经验沉淀失败不应影响摘要/记忆本身已经成功写入

            # [SYS-PROFILE] 同一后台线程内顺带检查并刷新用户画像
            self._maybe_refresh_profile(force=force)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.agent.profile.ProfileMixin._generate_and_save_summary')
            _locked_print_warning(f"[summary] generation failed: {e}")
        finally:
            self._summary_lock.release()

