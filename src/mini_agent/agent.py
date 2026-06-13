"""
Agent loop.
Orchestrates the conversation with Claude, dispatches tool calls,
manages history, and streams responses.

与具体 LLM 的交互全部通过 llm.LLMClient 接口进行，
切换 provider 只需修改 LLMConfig.provider，不改动 Agent 代码。

架构改进（拆分三个独立组件）：
- ContextBuilder：负责 system prompt 组装（skill/memory/project 注入）
- ToolExecutor：负责权限检查、工具调用、截断、缓存
- HistoryManager：负责历史追加、压缩、快照恢复
Agent 本身退化为纯编排层。
"""

from __future__ import annotations

import copy
import threading
from typing import Optional

from mini_agent.config import AppConfig, SessionStats, build_system_prompt
from mini_agent.llm import (
    LLMClient, LLMConfig, LLMResponse, ToolSchema,
    create_client, LLMError,
)
from mini_agent.llm.retry import RetryPolicy, default_retry_policy, no_retry_policy
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


class Agent:
    """
    Stateful agent that maintains conversation history and runs the agentic loop.

    Typical usage:
        agent = Agent(cfg, registry, skill_loader, guard)
        agent.run_turn("Fix the bug in app.py")
    """

    def __init__(
        self,
        cfg: AppConfig,
        registry: Optional[ToolRegistry] = None,
        skill_loader: Optional[SkillLoader] = None,
        guard: Optional[PermissionGuard] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry or get_default_registry()
        self.skill_loader = skill_loader
        self.guard = guard or PermissionGuard(
            auto_approve=cfg.auto_approve,
            sandbox=cfg.sandbox,
            project_root=cfg.project_root,
        )
        self.stats = SessionStats()
        self._history: list[dict] = []
        # LLMClient 可从外部注入（便于测试），否则从 AppConfig 自动创建
        self._llm: LLMClient = llm_client or create_client(
            LLMConfig.from_app_config(cfg)
        )
        # Session 持久化
        self._session_mgr: Optional[SessionManager] = None
        self._session: Optional[Session] = None
        self._init_session()

        # ── [SYS-RETRY] LLM 重试策略初始化 ──────────────────────────────────
        # 默认使用 EmptyOutputCondition（空输出即重试），可通过 cfg 调整参数，
        # 也可在实例化后替换 self._retry_policy 以使用自定义策略。
        _retry_max = getattr(cfg, "llm_retry_max", 2)
        _retry_delay = getattr(cfg, "llm_retry_delay", 0.0)
        self._retry_policy: RetryPolicy = (
            default_retry_policy(max_retries=_retry_max, retry_delay=_retry_delay)
            if _retry_max > 0
            else no_retry_policy()
        )

        # ── [SYS-UNDO] 手动重试 / 回退快照 ──────────────────────────────────
        # 每次 run_turn 开始时保存一份快照，支持：
        #   retry_last_turn()  — 丢弃本轮结果，用同一用户消息重新调用模型
        #   rollback_turn()    — 完全回退到本轮开始前的状态（撤销整个 turn）
        #
        # 快照格式：
        #   {
        #     "history":      list[dict],   # _history 的深拷贝
        #     "stats_turns":  int,          # stats.turns
        #     "stats_input":  int,          # stats.input_tokens
        #     "stats_output": int,          # stats.output_tokens
        #     "stats_tool":   int,          # stats.tool_calls
        #   }
        self._turn_snapshot: Optional[dict] = None

        # ── [SYS-SKILL-TOOL] 注册 skill 管理工具 ──────────────────────────────
        # 让模型可以主动调用 skill_list / skill_activate / skill_deactivate，
        # 而不是依赖关键词自动匹配（关键词匹配作为辅助，两者并存）。
        if self.skill_loader:
            from mini_agent.tools.skill_manager import (
                register_skill_tools,
                register_compact_tool,
                register_skill_stats_tool,
            )
            register_skill_tools(self.registry, self.skill_loader)
            register_compact_tool(self.registry, self)          # 需要 agent 实例
            register_skill_stats_tool(self.registry, self.skill_loader)

        # ── 感知与记忆子系统（按开关初始化）────────────────────────────────

        # [SYS-SYSCACHE] turn 级 system prompt 缓存。
        # _build_system() 首次调用时填充，同 turn 内所有 _call_llm() 复用。
        # clear_turn_cache() 在每次 run_turn 结束时清除。
        self._cached_system: Optional[str] = None

        # [SYS-PROJ] 项目结构感知 — 懒加载：在后台线程中扫描，不阻塞 REPL 启动
        # 扫描完成前 _project_snapshot 为 None，第一次 _build_system() 调用时
        # 若扫描已完成则注入，否则跳过本次注入（下一轮会自动获得结果）。
        self._project_snapshot: Optional[str] = None
        if cfg.project_scan_enabled:
            self._start_project_scan_async(cfg.project_root)

        # [SYS-WATCH] 文件变化感知 — FileWatcher 只维护哈希表，check_changes()
        # 的 IO 改由后台线程每 2s 执行一次，run_turn 只读取已计算好的结果集。
        self._file_watcher: Optional[FileWatcher] = None
        self._pending_file_changes: list[str] = []   # 后台线程填充，run_turn 消费
        self._file_changes_lock = __import__("threading").Lock()
        if cfg.file_watch_enabled:
            self._file_watcher = FileWatcher()
            self._start_file_watch_thread()

        # [SYS-TOOLCACHE] 工具调用结果缓存
        self._tool_cache: Optional[ToolResultCache] = (
            ToolResultCache(max_entries=cfg.perception.tool_cache_max_entries) if cfg.tool_cache_enabled else None
        )

        # [SYS-MEMORY] 跨 session 长期记忆（通过工厂创建，支持多后端）
        self._memory: Optional[MemoryBackend] = None
        self._global_memory: Optional[MemoryBackend] = None
        if cfg.memory_enabled:
            from mini_agent.perception.memory_factory import create_both_memory_backends
            self._memory, self._global_memory = create_both_memory_backends(cfg)

        # [SYS-PROFILE] 用户画像（单用户模式：user_id=None -> ~/.agent/profile.json）
        self._profile_mgr: Optional["UserProfileManager"] = None
        if cfg.profile_enabled:
            from mini_agent.profile import UserProfileManager
            from mini_agent.storage.paths import AgentPaths
            self._profile_mgr = UserProfileManager(AgentPaths(cfg.project_root), user_id=None)

        # ── [SYS-MCP] MCP 工具注册 ─────────────────────────────────────────────
        # 连接 agent_config.json 中配置的所有 MCP server，
        # 将其工具动态注册进 ToolRegistry（group="mcp:{server_name}"）。
        # 单个 server 连接失败不阻断启动，仅打印警告。
        self._mcp_manager = None
        if cfg.mcp.enabled:
            from mini_agent.mcp import MCPManager
            self._mcp_manager = MCPManager(cfg.mcp, global_auto_approve=cfg.auto_approve)
            self._mcp_manager.register_all(self.registry)

        # 所有字段已就绪，初始化三个拆分组件（ContextBuilder / ToolExecutor / HistoryManager）
        self._init_components()

    def _start_project_scan_async(self, project_root) -> None:
        """在后台线程中执行项目扫描，完成后写入 _project_snapshot。"""
        import threading as _threading

        def _scan():
            try:
                snap = ProjectScanner().scan(project_root)
                self._project_snapshot = snap.to_prompt_block()
            except Exception as e:
                R.print_warning(f"[perception] project scan failed: {e}")

        t = _threading.Thread(target=_scan, daemon=True, name="project-scan")
        t.start()

    def _start_file_watch_thread(self) -> None:
        """后台线程每 2s 检查文件变化，结果存入 _pending_file_changes。"""
        import threading as _threading
        import time as _time

        def _watch():
            while True:
                _time.sleep(2.0)
                if self._file_watcher is None:
                    break
                try:
                    changed = self._file_watcher.check_changes()
                    if changed:
                        with self._file_changes_lock:
                            # 合并（避免重复路径）
                            existing = set(self._pending_file_changes)
                            for p in changed:
                                if p not in existing:
                                    self._pending_file_changes.append(p)
                except Exception:
                    pass

        t = _threading.Thread(target=_watch, daemon=True, name="file-watcher")
        t.start()

    def _init_components(self) -> None:
        """
        初始化三个拆分组件（在 __init__ 末尾调用，确保所有字段已就绪）。

        ContextBuilder / ToolExecutor / HistoryManager 各自持有所需依赖的引用，
        Agent 编排层通过它们完成具体工作。
        """
        # ContextBuilder：感知 project_snapshot 通过 getter 懒取，避免扫描未完成时传 None
        # 传入 global_memory 以支持 merge_search 合并两级记忆
        self._ctx_builder = ContextBuilder(
            cfg=self.cfg,
            skill_loader=self.skill_loader,
            memory=self._memory,
            global_memory=self._global_memory,
            project_snapshot_getter=lambda: self._project_snapshot,
            profile_text_getter=self._get_profile_text,
        )

        # ToolExecutor：持有 file_changes 列表和锁的引用（共享，不拷贝）
        self._tool_executor = ToolExecutor(
            cfg=self.cfg,
            registry=self.registry,
            guard=self.guard,
            stats=self.stats,
            tool_cache=self._tool_cache,
            file_watcher=self._file_watcher,
            file_changes_list=self._pending_file_changes,
            file_changes_lock=self._file_changes_lock,
        )
        # [SYS-MCP] 注入 MCPManager（_init_components 在 MCP 注册后调用，此时已就绪）
        self._tool_executor._mcp_manager = getattr(self, "_mcp_manager", None)

        # HistoryManager：接管 _history 列表，并让 self._history 指向同一对象
        self._hist = HistoryManager(cfg=self.cfg, skill_loader=self.skill_loader)
        self._history = self._hist._history   # 共享同一列表对象，无需全量替换引用

    # ── Session 管理 ──────────────────────────────────────────────────────────────

    def _init_session(self) -> None:
        """初始化 SessionManager，创建新 Session（尚未写文件）。"""
        if not getattr(self.cfg, "auto_save_session", True):
            return
        try:
            session_dir = getattr(self.cfg, "session_dir", None)
            fmt = getattr(self.cfg, "session_fmt", "json")
            # session_dir=None 时 SessionManager 内部通过 AgentPaths 推导
            # → <project_root>/.agent/sessions/
            self._session_mgr = SessionManager(
                session_dir=session_dir,
                project_root=self.cfg.project_root,
                fmt=fmt,
            )
            self._session = self._session_mgr.new_session(
                provider=getattr(self.cfg, "llm_provider", "unknown"),
                model=self.cfg.model,
            )
            # 通知 TaskManager 当前 session_id，使 SubAgent 任务日志写到正确目录
            try:
                from mini_agent.tools.orchestration import get_task_manager
                tm = get_task_manager()
                if tm is not None:
                    tm.set_session_id(self._session.id)
            except Exception:
                pass
            # debug logger 绑定到 session：日志写入 sessions/<id>/llm_debug.jsonl
            if getattr(self.cfg, "debug_llm", False):
                try:
                    from mini_agent.llm.debug_logger import (
                        get_debug_logger, init_debug_logger_for_session,
                        DebugConfig as LLMDebugCfg,
                    )
                    existing = get_debug_logger()
                    init_debug_logger_for_session(
                        cfg=existing.cfg,
                        project_root=self.cfg.project_root,
                        session_id=self._session.id,
                    )
                except Exception:
                    pass
        except Exception as e:
            R.print_warning(f"Session init failed: {e}")

    def _append_memory_delta(self, entry) -> None:
        """将本 session 产生的记忆条目追加到 memory_delta.jsonl（审计用）。"""
        if not self._session:
            return
        try:
            from mini_agent.storage.paths import AgentPaths
            from dataclasses import asdict
            import json as _json
            delta_path = AgentPaths(self.cfg.project_root).session_memory_delta(self._session.id)
            delta_path.parent.mkdir(parents=True, exist_ok=True)
            with open(delta_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception:
            pass

    def save_session(self) -> Optional[str]:
        """保存当前对话历史到 Session 文件，返回路径，失败返回 None。"""
        if not self._session_mgr or not self._session:
            return None
        try:
            stats = {
                "turns":            self.stats.turns,
                "input_tokens":     self.stats.input_tokens,
                "output_tokens":    self.stats.output_tokens,
                "tool_calls":       self.stats.tool_calls,
                "tool_stats":       self.stats.tool_stats,
                "skill_activations": self.stats.skill_activations,
            }
            path = self._session_mgr.save(
                self._session,
                history=self._history,
                stats=stats,
            )

            # [SYS-SUMMARY] 达到门槛后生成 session 摘要（耗时较长，放到后台线程，避免阻塞主流程）
            if (self.cfg.session_summary_enabled
                    and self.stats.turns >= self.cfg.session_summary_min_turns
                    and not getattr(self._session, "summary", "")):
                R.print_info("正在后台生成本次会话的摘要记忆...")
                history_snapshot = list(self._history)
                threading.Thread(
                    target=self._generate_and_save_summary,
                    args=(str(path), history_snapshot),
                    daemon=True,
                    name="mini-agent-summary",
                ).start()

            return str(path)
        except Exception as e:
            R.print_warning(f"Session save failed: {e}")
            return None

    def _get_profile_text(self) -> str:
        """供 ContextBuilder 注入 system prompt 的用户画像摘要（无画像则返回空串）。"""
        if not self._profile_mgr:
            return ""
        try:
            profile = self._profile_mgr.load()
        except Exception:
            return ""
        return profile.derived.get("summary", "") if profile.derived else ""

    def _maybe_refresh_profile(self) -> None:
        """
        [SYS-PROFILE] 检查是否需要(重新)生成用户画像，若需要则同步生成。

        本方法预期在 _generate_and_save_summary 的后台线程中被调用（已经
        不阻塞主流程），因此这里直接同步调用 LLM，不再额外开线程。
        """
        if not self._profile_mgr:
            return
        # 画像基于全局记忆（跨项目通用经验）；没有全局记忆则跳过
        source = self._global_memory or self._memory
        if source is None:
            return
        try:
            count = source.count
            if not self._profile_mgr.should_refresh(count, self.cfg):
                return
            entries = source.all_entries()
            # all_entries 不保证按时间排序，按 created_at 升序取最近 N 条
            entries = sorted(entries, key=lambda e: e.created_at)[-self.cfg.profile.max_entries_for_profile:]
            R.print_info("正在后台更新用户画像(profile)...")
            self._profile_mgr.generate(self._llm, entries)
            R.print_info("用户画像(profile)已更新")
        except Exception as e:
            R.print_warning(f"用户画像生成失败: {e}")

    def _generate_and_save_summary(self, session_path: str, history: Optional[list] = None) -> None:
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
        try:
            if history is None:
                history = self._history
            user_turns = [
                m["content"] for m in history
                if m.get("role") == "user"
                and isinstance(m.get("content"), str)
                and not m["content"].startswith("<tool_result")
                and not m["content"].startswith("[Compressed")
                and not m["content"].startswith("[Previous session")
            ]
            if not user_turns:
                return

            turns_text = "\n".join(f"- {t[:200]}" for t in user_turns[:10])
            from mini_agent.prompts import pm
            prompt = pm.render("user/session_summary_request", turns_text=turns_text)
            resp = self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system=pm.render("system/summarizer"),
                tools=[],
            )
            summary = resp.text.strip()
            if not summary:
                return

            # 写回 session（通过 session_mgr，享受原子写入 + 文件锁）
            if self._session and self._session_mgr:
                self._session.summary = summary
                stats = {
                    "turns":             self.stats.turns,
                    "input_tokens":      self.stats.input_tokens,
                    "output_tokens":     self.stats.output_tokens,
                    "tool_calls":        self.stats.tool_calls,
                    "tool_stats":        self.stats.tool_stats,
                    "skill_activations": self.stats.skill_activations,
                }
                try:
                    self._session_mgr.save(self._session, history=history, stats=stats)
                except Exception as e:
                    R.print_warning(f"[summary] session re-save failed: {e}")

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
                    self._global_memory.add(entry)
                else:
                    self._memory.add(entry)
                # 同时写入 memory_delta.jsonl（session 审计）
                self._append_memory_delta(entry)
            R.print_info("会话摘要记忆已生成")

            # [SYS-PROFILE] 同一后台线程内顺带检查并刷新用户画像
            self._maybe_refresh_profile()
        except Exception as e:
            R.print_warning(f"[summary] generation failed: {e}")

    def load_session(self, session_id: str) -> bool:
        """按 session_id（或其前缀）加载历史到当前 agent，返回是否成功。"""
        if not self._session_mgr:
            return False
        session = self._session_mgr.load(session_id)
        if session is None:
            return False
        self._session = session
        # 必须原地替换列表内容，保持 self._history 与 self._hist._history 指向同一对象
        self._history.clear()
        self._history.extend(session.history)
        self.stats.turns         = session.stats.get("turns", 0)
        self.stats.input_tokens  = session.stats.get("input_tokens", 0)
        self.stats.output_tokens = session.stats.get("output_tokens", 0)
        self.stats.tool_calls    = session.stats.get("tool_calls", 0)
        return True

    @property
    def session_id(self) -> Optional[str]:
        return self._session.id if self._session else None

    @property
    def session_file(self) -> Optional[str]:
        return self._session.file_path if self._session else None

    @property
    def session_manager(self) -> Optional[SessionManager]:
        return self._session_mgr

    # ── Public interface ───────────────────────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    @property
    def llm_client(self) -> LLMClient:
        return self._llm

    def clear_history(self) -> None:
        self._history.clear()

    def switch_provider(self, llm_config: LLMConfig) -> None:
        """
        运行时切换 LLM provider，不影响对话历史。

        Example:
            agent.switch_provider(LLMConfig(provider="openai", model="gpt-4o", api_key="..."))
        """
        self._llm = create_client(llm_config)
        R.print_info(f"Switched to {self._llm}")

    # ── [SYS-UNDO] 手动重试 / 回退 ───────────────────────────────────────────

    def _save_turn_snapshot(self) -> None:
        """在每轮 run_turn 开始（用户消息追加前）保存一份完整快照。"""
        self._turn_snapshot = {
            "history":      copy.deepcopy(self._history),
            "stats_turns":  self.stats.turns,
            "stats_input":  self.stats.input_tokens,
            "stats_output": self.stats.output_tokens,
            "stats_tool":   self.stats.tool_calls,
        }

    def _restore_turn_snapshot(self) -> bool:
        """将历史和统计还原到快照时刻，返回是否成功。"""
        if self._turn_snapshot is None:
            return False
        # 原地替换，保持 self._history 与 self._hist._history 指向同一对象
        restored = copy.deepcopy(self._turn_snapshot["history"])
        self._history.clear()
        self._history.extend(restored)
        self.stats.turns          = self._turn_snapshot["stats_turns"]
        self.stats.input_tokens   = self._turn_snapshot["stats_input"]
        self.stats.output_tokens  = self._turn_snapshot["stats_output"]
        self.stats.tool_calls     = self._turn_snapshot["stats_tool"]
        return True

    def retry_last_turn(self) -> str:
        """
        [SYS-UNDO] 重试：丢弃上一轮模型输出，用相同的用户消息重新调用。

        行为：
          1. 从快照恢复到本轮「用户消息刚追加前」的状态
          2. 把用户消息从快照之后的 _history 里提取出来
          3. 重新执行 run_turn（包含保存 session、打印结果）

        适用场景：对模型答案不满意，希望重新生成一个不同版本。

        Returns:
            新的 assistant 文本，失败时返回空字符串。
        """
        if self._turn_snapshot is None:
            R.print_warning("[retry] No previous turn snapshot available.")
            return ""

        # 找出本轮用户消息（快照之后第一条 role=user 的消息）
        snap_len = len(self._turn_snapshot["history"])
        user_msg: Optional[str] = None
        for msg in self._history[snap_len:]:
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                user_msg = msg["content"]
                break

        if user_msg is None:
            R.print_warning("[retry] Could not locate last user message in history.")
            return ""

        turns_before = self.stats.turns
        R.print_retry_banner(turns_before)

        # 恢复到快照（撤销本轮所有历史）
        self._restore_turn_snapshot()
        # 清除快照（run_turn 会重新生成一份）
        self._turn_snapshot = None

        # 用相同消息重新执行
        return self.run_turn(user_msg)

    def rollback_turn(self) -> bool:
        """
        [SYS-UNDO] 回退：完全撤销上一轮 turn，恢复到本轮开始前的状态。

        与 retry_last_turn 的区别：
          - retry  ：保留用户消息，只重新生成 assistant 回复
          - rollback：用户消息也一并撤销，彻底回退到上一轮结束时的状态

        同步更新：
          - self._history（核心历史）
          - self.stats（token 统计）
          - session 文件（调用 save_session 写回磁盘）
          - 终端显示（打印回退分隔线）

        Returns:
            True — 回退成功；False — 无快照可回退
        """
        if self._turn_snapshot is None:
            R.print_warning("[rollback] No previous turn snapshot available.")
            return False

        turns_before = self.stats.turns
        self._restore_turn_snapshot()
        turns_after = self.stats.turns

        # 丢弃快照（回退后下次 run_turn 会重新创建）
        self._turn_snapshot = None

        R.print_rollback_banner(turns_before, turns_after)

        # 同步写回 session 文件
        if getattr(self.cfg, "auto_save_session", True):
            self.save_session()

        return True

    # ── [SYS-SKILL-COMPACT] Skill 压缩上下文 ─────────────────────────────────

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

        Returns:
            摘要文本（assistant 的压缩结果），失败时返回空字符串
        """
        if not self._history:
            R.print_info("[compact] History is empty, nothing to compact.")
            return ""

        from mini_agent.prompts import pm as _pm
        compact_prompt = _pm.get_compact_prompt()

        R.print_info("[compact] Generating summary…")
        try:
            result = self.run_turn(compact_prompt)
        except Exception as e:
            R.print_error(f"[compact] Summary generation failed: {e}")
            return ""

        # 压缩历史：保留摘要 + 重附 skill 块
        skill_block = self._build_skill_compact_block()

        new_history: list[dict] = [
            {"role": "user",      "content": "[Previous session summary]"},
            {"role": "assistant", "content": result},
        ]
        if skill_block:
            new_history.append({"role": "user", "content": skill_block})

        # 原地替换，保持共享引用有效
        self._history.clear()
        self._history.extend(new_history)

        # 同步 session
        if getattr(self.cfg, "auto_save_session", True):
            self.save_session()

        R.print_success("[compact] History compacted with skill context re-attached.")
        return result

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

            if self.skill_loader:
                newly = self.skill_loader.auto_activate(user_message)
                for name in newly:
                    R.print_skill_loaded(name)
                    # [SYS-SKILL-TRACK] 记录技能激活
                    if self.cfg.skill_tracking_enabled:
                        self.stats.record_skill_activation(name)

            # [SYS-MEMORY] 预检索记忆，缓存到 turn 级别。
            # 整个 turn 内的多次 _call_llm() 复用此缓存，不重复遍历记忆条目。
            if self._ctx_builder is not None:
                self._ctx_builder.refresh_turn_context(user_message)

            # [SYS-UNDO] 在追加用户消息前保存快照，用于 retry/rollback
            self._save_turn_snapshot()

            self._history.append({"role": "user", "content": user_message})
            self.stats.turns += 1

            result = self._agentic_loop()

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

        while loop_count < self.cfg.max_turns:
            loop_count += 1

            # [SYS-TOKEN] token 预估 + 自动压缩
            # _build_system() 命中 turn 级缓存，与后续 _call_llm() 共享同一字符串，
            # 不重复构建 system prompt。
            if self.cfg.token_estimate_enabled or self.cfg.auto_compress_enabled:
                from mini_agent.llm.system_tool_call import convert_tool_use_to_text
                _sys_preview = self._build_system()   # 首次调用时填充缓存
                _msgs_preview = convert_tool_use_to_text(self._history)
                _est = estimate_messages_tokens(_msgs_preview, _sys_preview)
                _budget_pct = _est / max(self.cfg.max_tokens, 1)
                if self.cfg.token_estimate_enabled and self.cfg.verbose:
                    R.print_info(
                        f"[token] ~{_est:,} tokens "
                        f"({_budget_pct:.0%} of {self.cfg.max_tokens:,})"
                    )
                if self.cfg.auto_compress_enabled and _budget_pct >= self.cfg.auto_compress_threshold:
                    # 压缩后 system 内容可能变化，清除缓存强制重建
                    self._cached_system = None
                    self._auto_compress_history()

            response = self._call_llm()
            final_text = response.text
            self.stats.input_tokens += response.usage.input_tokens
            self.stats.output_tokens += response.usage.output_tokens

            # 将 LLMResponse 写入对话历史（provider 无关格式）
            self._append_assistant_response(response)

            # [SYS-SKILL-DETECT] 推理完成后检测哪些 skill 被真正使用
            # 只有「实际使用」的 skill 才更新 tracker LRU 权重
            if self.skill_loader and response.text:
                used = self.skill_loader.record_usage(response.text)
                if used and self.cfg.verbose:
                    R.print_info(f"[skill-detect] used: {used}")

            if not response.has_tool_calls:
                break

            # 执行工具调用，结果写回历史
            tool_results, result_strs = self._execute_tools(response)
            self._history.append(
                self._build_tool_result_message(response.tool_calls, result_strs)
            )

        if loop_count >= self.cfg.max_turns:
            R.print_warning(f"Reached max turns ({self.cfg.max_turns}).")

        return final_text

    # ── LLM 调用 ───────────────────────────────────────────────────────────────

    def _call_llm(self) -> LLMResponse:
        """
        调用 LLMClient，根据 cfg.stream 选择流式或非流式。
        内置重试策略：当模型返回空响应时自动重试（由 self._retry_policy 控制）。
        """
        system = self._build_system()
        tools = self._build_tool_schemas()

        # 思维链回调：对支持 on_reasoning 参数的 provider（如 NVIDIA）启用流式 reasoning
        import inspect as _inspect
        _stream_sig = _inspect.signature(self._llm.stream)
        _supports_on_reasoning = "on_reasoning" in _stream_sig.parameters

        # 转换消息：将 tool_use 类型转换为 text 类型（用于不支持 tool_use 的模型）
        from mini_agent.llm.system_tool_call import convert_tool_use_to_text
        messages_for_llm = convert_tool_use_to_text(self._history)

        def _do_single_call() -> LLMResponse:
            """单次 LLM 调用，流式/非流式统一封装。重试时每次重新调用此函数。"""
            # 每次重试前重置 reasoning 状态（避免重复输出 header）
            _reasoning_started = [False]

            def _on_reasoning(token: str) -> None:
                if not _reasoning_started[0]:
                    R.print_reasoning_header()
                    _reasoning_started[0] = True
                R.print_reasoning(token)

            try:
                if self.cfg.stream:
                    R.print_assistant_prefix(agent_name=self.cfg.agent_name)
                    writer = R.StreamWriter()
                    stream_kwargs: dict = dict(
                        messages=messages_for_llm,
                        system=system,
                        tools=tools,
                        on_token=writer.write,
                    )
                    if _supports_on_reasoning:
                        stream_kwargs["on_reasoning"] = _on_reasoning
                    resp = self._llm.stream(**stream_kwargs)
                    # postprocess 已提取 <thinking> 块，非流式 reasoning 在这里显示
                    if not _reasoning_started[0] and resp.reasoning:
                        R.print_reasoning_header()
                        R.console.print(resp.reasoning, style="dim")
                    if _reasoning_started[0] or resp.reasoning:
                        R.print_reasoning_footer()
                    writer.flush()
                else:
                    resp = self._llm.chat(
                        messages=messages_for_llm,
                        system=system,
                        tools=tools,
                    )
                    # postprocess 已提取 <thinking> 块，统一在此显示
                    if resp.reasoning:
                        R.print_reasoning_header()
                        R.console.print(resp.reasoning, style="dim")
                        R.print_reasoning_footer()
                    if resp.text:
                        R.print_assistant_prefix(agent_name=self.cfg.agent_name)
                        R.print_markdown(resp.text)
            except LLMError:
                raise
            except Exception as e:
                from llm import LLMProviderError
                raise LLMProviderError(f"Unexpected LLM error: {e}") from e

            return resp

        def _on_retry(attempt: int, reason: str) -> None:
            """重试时的提示回调。"""
            if getattr(self.cfg, "llm_retry_verbose", True):
                R.print_warning(
                    f"[retry {attempt}/{self._retry_policy.max_retries}] {reason}"
                )

        # 使用重试策略执行调用
        response = self._retry_policy.call_with_retry(
            call_fn=_do_single_call,
            on_retry=_on_retry,
        )
        return response

    # ── History management ─────────────────────────────────────────────────────

    def _append_assistant_response(self, response: LLMResponse) -> None:
        """
        将 LLMResponse 转换为对话历史条目。
        使用 provider 无关的通用格式（Anthropic/OpenAI 均可接受）。
        <skill_used> 标签在此处剥离，不写入历史（避免污染后续对话上下文）。
        """
        from mini_agent.skills.usage_detector import strip_skill_tags
        content: list[dict] = []
        if response.text:
            clean_text = strip_skill_tags(response.text)
            if clean_text:
                content.append({"type": "text", "text": clean_text})
        for tc in response.tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        self._history.append({"role": "assistant", "content": content})

    # ── Tool execution ─────────────────────────────────────────────────────────

    def _execute_tools(self, response: LLMResponse) -> tuple[list, list[str]]:
        """
        运行所有工具调用，返回 (tool_calls列表, result字符串列表)。

        [SYS-DEDUP] 历史层去重：
        同一 turn 内，若相同工具 + 相同参数的结果已经在历史里出现过，
        则写入占位符 "[same result as above: <tool>(...)]" 而非完整内容。
        这直接减少历史里的重复 token，缓存命中时尤为有效。

        去重只看「本 turn 内已追加的 tool_result」，跨 turn 的重复由压缩策略处理。
        """
        import hashlib as _hashlib, json as _json

        result_strs: list[str] = []

        # 收集本 turn 内已见过的 (tool_name, input_hash) → result_str
        # 仅在本次 _execute_tools 调用内累积（一次 LLM 响应里的多个 tool_call）
        _seen_this_batch: dict[tuple[str, str], str] = {}

        # 同时扫描本 turn 内历史里已有的 tool_result 消息，提取已见的结果哈希
        # 避免同 turn 内跨 LLM 调用的重复（例如 turn 里第二次 LLM 调用又 read 同文件）
        _seen_in_history: dict[tuple[str, str], str] = {}
        _TR_OPEN = "<tool_result>"
        _TR_CLOSE = "</tool_result>"
        for _msg in reversed(self._history):
            if _msg.get("role") != "user":
                continue
            _c = _msg.get("content", "")
            if not isinstance(_c, str) or not _c.startswith(_TR_OPEN):
                break   # 碰到非 tool_result 消息就停，本 turn 的都扫完了
            if '"\"name\":' in _c and '"\"output\":' in _c:
                try:
                    _start = len(_TR_OPEN) + 1
                    _end = _c.rfind(_TR_CLOSE) - 1
                    if _end > _start:
                        _entry = _json.loads(_c[_start:_end])
                        _tname = _entry.get("name", "")
                        _tout = _entry.get("output", "")
                        if _tname and not _tout.startswith("[same result"):
                            _h = _hashlib.md5(_tout.encode()).hexdigest()[:12]
                            _seen_in_history[(_tname, _h)] = _tout
                except Exception:
                    pass

        for tc in response.tool_calls:
            R.print_tool_call(tc.name, tc.input, verbose=self.cfg.verbose)
            self.stats.tool_calls += 1

            allowed = self.guard.check(tc.name, tc.input)
            if not allowed:
                result_str = "[Tool call denied by user]"
                R.print_tool_error(tc.name, "denied by user")
                if self.cfg.tool_stats_enabled:
                    self.stats.record_tool_call(tc.name, False, 0)
            else:
                # [SYS-TOOLCACHE] 检查缓存
                _cached = None
                if self._tool_cache:
                    _cached = self._tool_cache.get(tc.name, tc.input)

                if _cached is not None:
                    result_str = _cached
                    R.print_tool_result(tc.name, f"[cache] {result_str[:80]}...")
                    if self.cfg.tool_stats_enabled:
                        self.stats.record_tool_call(tc.name, True, len(result_str))
                else:
                    try:
                        result = self.registry.call(tc.name, tc.input)
                        result_str = str(result) if not isinstance(result, str) else result

                        # [SYS-TRIM] 工具调用结果截断
                        result_str = self._maybe_trim_result(tc.name, result_str)

                        R.print_tool_result(tc.name, result_str)

                        # [SYS-TOOLCACHE] 写入缓存
                        if self._tool_cache:
                            self._tool_cache.put(tc.name, tc.input, result_str)

                        # [SYS-TOOLCACHE] 写操作执行成功后，立即使目标文件缓存失效，
                        # 确保同一 turn 内后续的 read_file / grep 不返回旧数据。
                        if (
                            self._tool_cache
                            and tc.name in ("write_file", "create_file", "patch_file", "delete_file")
                            and not result_str.startswith("[error")
                        ):
                            _target_path = tc.input.get("path", "")
                            if _target_path:
                                self._tool_cache.invalidate_file(_target_path)

                        # [SYS-WATCH] 注册 read_file 的文件
                        if self._file_watcher and tc.name == "read_file":
                            _path = tc.input.get("path", "")
                            if _path:
                                self._file_watcher.register(_path, result_str)

                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, True, len(result_str))
                    except Exception as e:
                        result_str = f"[tool error: {e}]"
                        R.print_tool_error(tc.name, str(e))
                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, False, 0)

            # [SYS-DEDUP] 去重检查：幂等工具（非写操作）才做去重
            # 写操作、bash 等副作用工具不去重（每次执行结果可能不同）
            _DEDUP_TOOLS = {"read_file", "grep", "glob", "list_dir", "web_search"}
            if tc.name in _DEDUP_TOOLS and not result_str.startswith("["):
                _h = _hashlib.md5(result_str.encode()).hexdigest()[:12]
                _key = (tc.name, _h)
                # 检查本 batch 或本 turn 历史里是否已有相同结果
                if _key in _seen_this_batch or _key in _seen_in_history:
                    _short = _json.dumps(tc.input, ensure_ascii=False)[:60]
                    _dedup_str = f"[same result as above: {tc.name}({_short})]"
                    R.print_info(f"[dedup] {tc.name} result deduplicated ({len(result_str)} chars → {len(_dedup_str)} chars)")
                    result_str = _dedup_str
                else:
                    _seen_this_batch[_key] = result_str

            result_strs.append(result_str)

        return response.tool_calls, result_strs

    def _maybe_trim_result(self, tool_name: str, result: str) -> str:
        """
        [SYS-TRIM] 按工具类型分策略截断长结果。

        各工具截断方向：
        - bash：保留头部（命令行 + 前几行输出）+ 尾部（最终输出/错误在末尾）
                尾部权重更高（tail_ratio=0.6），因为 exit code 和 stderr 通常在尾部
        - read_file：头尾各保留一半（结构声明在头，返回值/测试在尾）
                     提示 LLM 使用 start_line/end_line 精确读取
        - grep/glob：平铺匹配行，保留前 N 行（最相关的命中优先）
        - 其他：通用头多尾少截断
        """
        if not self.cfg.tool_result_trim_enabled:
            return result
        threshold = self.cfg.tool_result_trim_threshold
        if len(result) <= threshold:
            return result

        lines = result.splitlines()
        total = len(lines)

        if tool_name == "bash":
            # bash：尾部权重更高——实际输出/错误/exit 通常在尾部
            # 保留头部（命令回显 + 早期 stdout）30% + 尾部 60%，中间省略
            if total > 20:
                tail_ratio = getattr(self.cfg.tool_trim, "bash_tail_ratio", 0.6)
                tail_n = max(8, int(total * tail_ratio))
                head_n = max(5, int(total * 0.3))
                # 确保 head + tail 不超出 total
                if head_n + tail_n >= total:
                    head_n = total // 3
                    tail_n = total - head_n
                omitted = total - head_n - tail_n
                if omitted > 0:
                    return (
                        "\n".join(lines[:head_n])
                        + f"\n... [{omitted} lines omitted] ...\n"
                        + "\n".join(lines[-tail_n:])
                    )

        elif tool_name == "read_file":
            # read_file：头尾各半，提示精确范围读取
            if total > 30:
                window = min(total, max(30, threshold // 60))
                if window < total:
                    head_n = window // 2
                    tail_n = window - head_n
                    omitted = total - head_n - tail_n
                    return (
                        "\n".join(lines[:head_n])
                        + f"\n... [{omitted} lines omitted — use start_line/end_line to read specific range] ...\n"
                        + "\n".join(lines[-tail_n:])
                    )

        elif tool_name in ("grep", "glob"):
            # grep/glob：平铺结果，只保留前 N 行（最相关命中优先）
            grep_max = getattr(self.cfg.tool_trim, "grep_max_lines", 50)
            if total > grep_max:
                keep = min(grep_max, max(20, threshold // 60))
                omitted = total - keep
                return (
                    "\n".join(lines[:keep])
                    + f"\n... [{omitted} more matches omitted] ..."
                )

        # 通用策略（头多尾少）
        if total > 30:
            head_n = 15
            tail_n = 5
            omitted = total - head_n - tail_n
            if omitted > 0:
                return (
                    "\n".join(lines[:head_n])
                    + f"\n... [{omitted} lines omitted] ...\n"
                    + "\n".join(lines[-tail_n:])
                )

        # 字符截断兜底
        return result[:threshold] + f"\n... [{len(result)-threshold} chars omitted]"

    def _build_tool_result_message(self, tool_calls, results: list[str]) -> dict:
        """
        构造回注工具结果的 user 消息。
        统一使用 <tool_result> 文本格式（与 tool_call_protocol.md 对应）。
        """
        from mini_agent.llm.system_tool_call import render_tool_results
        content = render_tool_results(tool_calls, results)
        return {"role": "user", "content": content}

    # ── Helpers ────────────────────────────────────────────────────────────────

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

    def _auto_compress_history(self) -> None:
        """
        [SYS-COMPRESS] 自动压缩历史，保留最近一半，并重附 skill 上下文。

        修复（v2）：
        1. 以「turn」为边界切割。切割点对齐到 user 消息边界，保证：
             - 保留段的第一条消息始终是 user 消息
             - 每条 tool_result 都有对应的 tool_use（不产生孤立工具结果）
        2. tool_call_count 只统计 type=tool_use 的 block，而非 content 列表长度。
        3. 原地替换列表内容，保持 self._history 共享引用有效。
        """
        if len(self._history) < 6:
            return

        # ── 找到以 turn 为边界的切割点 ──────────────────────────────────────
        user_indices = [
            i for i, m in enumerate(self._history)
            if m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and not m["content"].startswith("<tool_result")
            and not m["content"].startswith("[Previous")
            and not m["content"].startswith("[Compressed")
        ]

        if len(user_indices) < 2:
            cutoff = len(self._history) // 2
        else:
            mid = len(self._history) // 2
            cutoff = min(user_indices, key=lambda i: abs(i - mid))
            if cutoff >= user_indices[-1]:
                cutoff = user_indices[len(user_indices) // 2]

        old_turns = self._history[:cutoff]

        # ── 构建摘要文字 ──────────────────────────────────────────────────────
        user_msgs = [
            m["content"] for m in old_turns
            if m.get("role") == "user" and isinstance(m.get("content"), str)
            and not m["content"].startswith("<tool_result")
            and not m["content"].startswith("[Previous session")
        ]
        tool_call_count = sum(
            sum(1 for b in m.get("content", [])
                if isinstance(b, dict) and b.get("type") == "tool_use")
            for m in old_turns
            if m.get("role") == "assistant" and isinstance(m.get("content"), list)
        )
        summary_parts = []
        if user_msgs:
            summary_parts.append("User requests: " + "; ".join(
                (msg[:80] + "\u2026" if len(msg) > 80 else msg)
                for msg in user_msgs[:6]
            ))
            if len(user_msgs) > 6:
                summary_parts.append(f"... and {len(user_msgs)-6} more user turns")
        if tool_call_count:
            summary_parts.append(f"({tool_call_count} tool calls executed)")
        summary_text = " ".join(summary_parts) if summary_parts else f"({cutoff} msgs)"

        # ── 保留段：可选剔除孤立工具结果消息 ─────────────────────────────────
        keep = self._history[cutoff:]
        if self.cfg.forget_policy_enabled:
            keep = [
                m for m in keep
                if not (
                    m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and m["content"].startswith("<tool_result")
                )
            ]

        # ── 原地替换，保持共享引用有效 ───────────────────────────────────────
        compressed_pair = [
            {"role": "user",      "content": "[Previous conversation compressed]"},
            {"role": "assistant", "content": f"[Compressed summary: {summary_text}]"},
        ]
        self._history.clear()
        self._history.extend(compressed_pair + keep)

        # [SYS-SKILL-COMPACT] 压缩后重附 skill 上下文
        skill_block = self._build_skill_compact_block()
        if skill_block:
            self._history.append({
                "role":    "user",
                "content": skill_block,
            })

        R.print_info(f"[compress] History compressed (cutoff={cutoff}, turn-aligned) → summary.")

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