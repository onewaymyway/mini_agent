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


class SessionLifecycleMixin:
    """会话生命周期：初始化、启动、加载/新建/保存会话、关闭。"""

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
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.agent')
                    pass

        t = _threading.Thread(target=_watch, daemon=True, name="file-watcher")
        t.start()

    def _get_notepad_render_text(self) -> Optional[str]:
        """
        返回当前 session 记事本的渲染文本，供 ContextBuilder 每轮注入 system prompt。
        返回 None 表示记事本系统当前不可用（尚无 session 等），此时
        ContextBuilder 会整体跳过记事本块，而不是注入一个空壳。
        失败时同样返回 None，避免异常影响 system prompt 组装主流程。
        """
        try:
            from mini_agent.tools.notepad import get_current_notepad
            store = get_current_notepad()
            if store is None:
                return None
            return store.render()
        except Exception:
            return None

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
            # [具身改进 C1] AgentSelfModel getter：每轮 build() 时读取最新状态
            # 注意：self._self_model 在下方的 C1 初始化块里赋值，而该块
            # 在 ContextBuilder 构造之后——lambda 捕获 self 引用，调用时才求值，
            # 所以不存在"先用后赋"问题：getter 在第一次 build() 被调用时，
            # self._self_model 已经完成初始化（或为 None）。
            self_model_getter=lambda: (
                self._self_model.to_system_prompt_fragment()
                if self._self_model is not None else None
            ),
            # 角色扮演（Persona）系统：每轮读取当前 active_persona，
            # /role use|exit 直接修改 self.active_persona 即可生效
            # （_cached_system 在每轮结束时清空，下一轮 build() 会读到最新值）。
            persona_getter=lambda: self.active_persona,
            # 记事本：每轮读取当前 session 的记事本渲染文本（NotepadStore 内部按
            # session_id 缓存，重复实例化 AgentPaths 无实际 IO 开销）。
            notepad_getter=lambda: self._get_notepad_render_text(),
        )

        # [Notepad] 注入 project_root + session_id 懒引用，供 notepad_add/update/
        # remove/list/summarize 等工具定位到当前 session 的 notepad.json。
        from mini_agent.tools.notepad import configure_notepad_store
        from mini_agent.storage.paths import AgentPaths as _NotepadAgentPaths
        configure_notepad_store(
            lambda: _NotepadAgentPaths(self.cfg.project_root),
            lambda: (self._session.id if self._session else ""),
            enabled_getter=lambda: getattr(self.cfg, "notepad_enabled", True),
        )

        # [compact_mechanism_improvement_plan P2-B] recall_from_raw_history 只读
        # 工具：注入"当前 session 的 raw history 条目列表"懒引用。放在这里
        # （而不是等 self._hist 构造完之后）是因为下面几行马上就会构造
        # self._hist = HistoryManager(...)；用 getattr(self, "_hist", None) 做
        # 懒引用即可保证调用时（工具真正被执行时）self._hist 已经就绪，不需要
        # 调整初始化顺序。
        from mini_agent.tools.recall_history import configure_recall_history
        configure_recall_history(
            lambda: (
                self._hist._raw.entries if getattr(self, "_hist", None) is not None else []
            ),
            enabled_getter=lambda: getattr(self.cfg, "recall_history_enabled", False),
        )

        # ToolExecutor：持有 file_changes 列表和锁的引用（共享，不拷贝）
        # [SYS-LESSON] 规则触发引擎（Stage 1.2）：仅当记忆功能启用且规则开关打开时创建
        _lesson_engine = None
        if (
            self._memory is not None
            and getattr(self.cfg.memory, "lesson_rules_enabled", True)
        ):
            from mini_agent.perception.lesson_rules import LessonRuleEngine
            _lesson_engine = LessonRuleEngine(
                session_id=self._session.id if self._session else "",
                model=self.cfg.model,
                fail_threshold=getattr(self.cfg.memory, "lesson_fail_threshold", 3),
            )

        # [record_artifact 工具] 注入 project_root + session_id 懒引用，供 Agent
        # 主动调用 record_artifact 工具登记产出物（perception/artifact_detector.py
        # 是被动自动侦测，这里是工具主动调用，二者并存）。
        from mini_agent.tools.builtin import configure_artifact_tool
        configure_artifact_tool(
            getattr(self.cfg, "project_root", None),
            lambda: (self._session.id if self._session else ""),
        )

        self._tool_executor = ToolExecutor(
            cfg=self.cfg,
            registry=self.registry,
            guard=self.guard,
            stats=self.stats,
            tool_cache=self._tool_cache,
            file_watcher=self._file_watcher,
            file_changes_list=self._pending_file_changes,
            file_changes_lock=self._file_changes_lock,
            lesson_engine=_lesson_engine,
            memory_sink=self._memory,
            on_edit_detected=self._on_edit_detected,
            # [Stage 6] tracer 通过 _update_executor_tracer() 在 _init_tracer 后注入；
            # turn_id_getter / history_getter 用 lambda 懒引用，调用时已就绪。
            tracer=None,
            turn_id_getter=lambda: self.stats.turns,
            history_getter=lambda: self._history,
            llm_client=self._llm,
            raw_result_store=self._raw_result_store,
            # [二期] 角色扮演 allowed_tools 白名单：懒引用，/role use|exit 修改
            # self.active_persona 后下一次工具调用即可读到最新值。
            persona_getter=lambda: self.active_persona,
            # [产出物自动侦测] 懒引用 session id，供 write_file/create_file/bash
            # 等工具成功执行后自动登记产出 manifest（perception/artifact_detector.py）。
            session_id_getter=lambda: (self._session.id if self._session else ""),
            # [auto_quarantine] 懒引用，供工具调用失败时归因给当前 active skill
            skill_loader_getter=lambda: getattr(self, "skill_loader", None),
        )
        # [SYS-MCP] 注入 MCPManager（_init_components 在 MCP 注册后调用，此时已就绪）
        self._tool_executor._mcp_manager = getattr(self, "_mcp_manager", None)

        # HistoryManager：接管 _history 列表，并让 self._history 指向同一对象
        self._hist = HistoryManager(cfg=self.cfg, skill_loader=self.skill_loader)
        self._history = self._hist._history   # 共享同一列表对象，无需全量替换引用

        # [SYS-COMPACT-TRIGGERS] compact 触发器组合（token/轮次/工具调用计数/
        # 话题切换/冗余检测），各自独立开关，见 history/triggers.py
        from mini_agent.history.triggers import CompositeTrigger
        self._compact_triggers = CompositeTrigger()
        # 上次 compact 时的 turns / tool_calls 快照，用于计算轮次/工具调用增量触发
        self._last_compact_turns: int = 0
        self._last_compact_tool_calls: int = 0
        # 距上次 compact 经过的轮数（用于冷却期判断）
        self._turns_since_last_compact: int = 0
        # [BUGFIX 重入保护] compact_with_skills() 的"正常路径"会调用 run_turn()，
        # 而 run_turn() 内部又会重新进入 _agentic_loop()。如果不加保护，
        # 这个嵌套的 _agentic_loop() 会在压缩尚未完成、self._history 尚未清空
        # （甚至因为新塞入的 compact_prompt 而 token 数不降反升）的情况下，
        # 再次命中 token_threshold 等触发器，导致 compact 过程中递归/重复
        # 触发 compact。此标志位在 _auto_compress_history() 执行期间置 True，
        # _agentic_loop() 触发检查前先看它，为 True 则直接跳过本轮触发检查。
        self._compacting_in_progress: bool = False

        # raw history 路径绑定（_init_session 在 _init_components 之前调用，
        # 彼时 _hist 尚不存在，set_path 被吞掉了。在这里补绑定。）
        self._bind_raw_path()

        # ReminderManager：动态 reminder 注入系统
        self._reminder_mgr: Optional[ReminderManager] = None
        if getattr(self.cfg, "reminder", None) and getattr(self.cfg.reminder, "enabled", True):
            try:
                self._reminder_mgr = ReminderManager(self.cfg)
            except Exception as _e:
                # reminder 系统初始化失败不影响 agent 主流程
                import warnings
                warnings.warn(f"[ReminderManager] 初始化失败，已禁用: {_e}")

        # [具身改进 B1] ProprioceptionModule：本体感知快照，O(1) 不调用 LLM
        self._proprioception: Optional["ProprioceptionModule"] = None
        self._last_tool_names: list = []  # 供 sense() 估算 risk_perception
        # [具身改进 工具透明性] 最近一批工具调用的意图分组结果（ActionEvent 列表），
        # 供 digest / 自维护扫描等读取，不持久化，仅 session 内有效。
        self._last_action_events: list = []
        if getattr(self.cfg, "proprioception", None) and self.cfg.proprioception.enabled:
            from mini_agent.perception.proprioception import ProprioceptionModule
            self._proprioception = ProprioceptionModule()
        # [B1 → Stage 9 信号桥接] 上次写入快照文件时的 frustration 值，用于判断
        # 本轮是否值得再写一次（避免无意义变化时的重复磁盘 IO）。
        self._last_written_frustration: Optional[float] = None
        # [方案三新增] 连续高不确定性轮次计数，供 _maybe_publish_uncertainty_signal()
        # 判断是否达到"连续 N 轮 uncertainty 都超过阈值"的限流发布条件。
        self._uncertainty_streak: int = 0

        # [具身改进 C1] AgentSelfModel：三个 profile 概念的聚合视图
        # 慢变量（capability_snapshot, affordance_summary）在此构建一次，
        # 快变量（internal_state）每轮 turn 开始时由 _update_self_model 更新。
        self._self_model: Optional["AgentSelfModel"] = None
        try:
            from mini_agent.perception.self_model import AgentSelfModel, AgentSelfModelBuilder
            _skill_count = len(self.skill_loader.active) if self.skill_loader else 0
            self._self_model = AgentSelfModelBuilder().build(
                project_root=self.cfg.project_root,
                affordance_map=None,   # B4 注入在 session_pool 阶段，此处不重建
                active_skill_count=_skill_count,
                use_capability_map=getattr(
                    getattr(self.cfg, 'affordance', None), 'use_capability_map', True
                ),
            )
        except Exception:
            pass  # AgentSelfModel 构建失败不阻断 Agent 启动

        # [具身改进 A3] ReminderManager 在 ToolExecutor 构造之后才初始化完成，
        # 这里补注入 reminder_mgr + 注入回调，使 pre_tool 前馈检查可用
        # （与 _mcp_manager 的延迟注入方式一致）。
        self._tool_executor._reminder_mgr = self._reminder_mgr
        self._tool_executor._inject_reminder = self._inject_reminder

        # ── [SYS-INTROSPECTION] 自感知与运行时调整工具 ─────────────────────────
        # 注册 agent_status / agent_inspect / agent_patch / agent_policy 四个工具，
        # 让 agent 具备实时感知并动态调整自身状态的能力。
        # 必须在所有其他组件（history / tool_executor / skill 等）初始化完毕后注册，
        # 以确保采集器可访问完整的 agent 内部对象。
        try:
            from mini_agent.tools.introspection import register_introspection_tools
            register_introspection_tools(self.registry, self)
        except Exception as _e:
            import warnings
            warnings.warn(f"[Introspection] 自省工具注册失败，已跳过: {_e}")

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
            self._bind_session_extras()
            self._maybe_ensure_project_meta()
            self._maybe_register_global_project()
            # 注：认知锚点不在这里加载——新建 session 是全新随机 id，其目录下
            # 不可能已存在锚点文件。锚点只在 resume 一个已有 session 时才有
            # 意义，见 load_session() 里对应的 _maybe_load_cognitive_anchor 调用。
            # [SYS-HOOKS] SessionStart：session 初始化完成后触发（通知型）
            try:
                from mini_agent.hooks import get_hook_manager as _ghm_ss
                _hm_ss = _ghm_ss()
                if _hm_ss is not None:
                    _hm_ss.run("SessionStart", {
                        "session_id": self._session.id if self._session else "",
                        "model": self.cfg.model,
                        "provider": getattr(self.cfg, "llm_provider", "unknown"),
                    })
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent')
                pass
        except Exception as e:
            R.print_warning(f"Session init failed: {e}")

    def _maybe_ensure_project_meta(self) -> None:
        """
        [W2 / 4.1 + 12.2] agent 进程启动时（不是每次 session 切换/resume）确保
        project.json 存在并更新 last_active / total_sessions / environment_fingerprint。

        只在 _init_session 调用一次，不在 load_session() / new_session() 里重复
        调用——resume 一个已有 session 不是"新的一次工作"，不应该把
        total_sessions 算两遍；new_session()（/session new）则是用户在同一进程
        内开了一个新会话，同样不重复计入"启动一次 agent 进程"。

        【横向加固 12.2】顺手检测 environment_fingerprint 漂移并打印提醒——
        只做"检测并报告"，不做"自动降低 lesson/skill 置信度"的下游联动
        （那部分价值中等、可独立排期，强行在这里捆绑实现会让一次启动检查
        牵连读写 memory.jsonl/skills/，与本方法"轻量、不可阻塞启动"的定位冲突）。
        """
        if not getattr(self.cfg, "workdir_knowledge_enabled", True):
            return
        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.perception.workdir_knowledge import (
                ensure_project_meta, load_project_meta, capture_environment_fingerprint,
                detect_environment_drift,
            )
            paths = AgentPaths(self.cfg.project_root)
            old_meta = load_project_meta(paths)
            old_fp = dict(old_meta.environment_fingerprint) if old_meta else {}

            ensure_project_meta(paths, self.cfg.project_root)

            if old_fp:
                new_fp = capture_environment_fingerprint(self.cfg.project_root)
                drift = detect_environment_drift(old_fp, new_fp)
                if drift:
                    R.print_info(
                        "检测到运行环境变化（" + "; ".join(drift[:3]) + "）："
                        "之前积累的部分经验/技能可能需要重新验证。"
                    )
        except Exception:
            pass  # 观察性数据，失败不应影响 agent 主流程

    def _maybe_register_global_project(self) -> None:
        """
        [W3 / 5.2] agent 进程启动时（与 _maybe_ensure_project_meta 同一次调用
        时机，只在 _init_session 跑一次）把当前 workdir 注册/更新进
        ~/.agent/projects_index.json，并顺手跑一遍全部已注册项目的 dormant
        状态巡检（5.2 节"定期检查"——不需要专门后台任务，任意 session 启动时
        顺手检查一遍即可，O(项目数) 量级足够轻量）。

        与 _maybe_ensure_project_meta 各自独立 try/except——Global 知识层
        与 Workdir 知识层是两个平行的子系统（5.5 节"维护机制与 context 注入"
        强调两者对称但不耦合），一方失败不应该影响另一方。
        """
        if not getattr(self.cfg, "global_knowledge_enabled", True):
            return
        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.perception import global_knowledge as gk

            paths = AgentPaths(self.cfg.project_root)
            gk.register_or_touch_project(paths, self.cfg.project_root)
            dormant_after_days = getattr(
                self.cfg.global_knowledge, "dormant_after_days", 30.0
            )
            gk.refresh_dormant_status(paths, dormant_after_days=dormant_after_days)
        except Exception:
            pass  # 观察性数据，失败不应影响 agent 主流程

    def _cognitive_anchor_path(self, session_id: str):
        """
        [具身改进 C3 / 认知锚点 session 化] 返回指定 session 目录下的认知锚点
        文件路径：`<sessions_dir>/<session_id>/cognitive_anchor.md`。

        锚点记录的是"某个具体 session 被打断时脑子里在想什么"，天然应该
        跟着那个 session 走，而不是挂在整个 workdir 下——旧实现里锚点是
        workdir 级单文件，会导致"在 session-1 被打断留下的锚点，被后续
        任意一个新建/恢复的 session（哪怕是完全不相关的 session-99）读到"
        这种跨 session 串味的问题。改为存进 session 自己的目录后，读取时
        天然只会命中"正在恢复的这个 session"，不需要额外的 session_id
        匹配逻辑。
        """
        from pathlib import Path
        return Path(self._session_mgr.session_dir) / session_id / "cognitive_anchor.md"

    def _maybe_load_cognitive_anchor(self, session_id: str) -> None:
        """
        [具身改进 C3 / 认知锚点 session 化] resume 一个已有 session
        （`load_session()`）时检查该 session 自己目录下是否存在认知锚点
        文件，若存在则注入 `system_extra`（"恢复记忆"），并归档（重命名加
        时间戳后缀）——消费一次即归档，避免同一份锚点被无限期重复注入。

        注意：这里**不**在 `_init_session()`（新建 session）里调用——新建的
        session 是一个全新的随机 id，其目录下必然还没有任何锚点文件，
        检查它没有意义；锚点只可能存在于"之前被打断过、现在正在被 resume"
        的那个已有 session 目录下。

        与 B4 AffordanceMap 的协作：二者都写入 cfg.system_extra，但分别在
        不同时机调用（AffordanceMap 由 SessionAgentPool 在多用户路径里注入，
        认知锚点在这里对本地/daemon 两条路径统一生效）——拼接顺序不强制，
        system_extra 是简单的文本累加。
        """
        if not getattr(self.cfg, "cognitive_anchor_enabled", True):
            return
        try:
            anchor_path = self._cognitive_anchor_path(session_id)
            if not anchor_path.exists():
                return
            content = anchor_path.read_text(encoding="utf-8").strip()
            if not content:
                return

            fragment = (
                "## 上次中断时留下的认知锚点（自动恢复，仅供参考）\n" + content
            )
            existing = getattr(self.cfg, "system_extra", "") or ""
            self.cfg.system_extra = (existing + "\n\n" + fragment).strip()

            # 归档：重命名为带时间戳的文件，原路径不再存在，避免重复注入。
            import time as _time
            archived = anchor_path.with_name(
                f"cognitive_anchor.{int(_time.time())}.md"
            )
            anchor_path.rename(archived)
        except Exception:
            pass  # 锚点恢复失败不应影响 session 加载流程

    def _save_cognitive_anchor(self) -> None:
        """
        [具身改进 C3] 任务被用户明确打断时（Ctrl-C / /stop）调用，生成一份
        "思维状态重建指南"写入**当前 session 自己目录下**的
        `cognitive_anchor.md`（见 `_cognitive_anchor_path`），供下次
        `load_session()` 恢复这个具体 session 时读取（见
        `_maybe_load_cognitive_anchor`）。

        内容由 LLM 生成，格式固定（见 prompts/system/cognitive_anchor.md），
        是"给被打断后返回的自己看的便条"，不是给人类看的进展报告——后者已经
        由 history/timeline 覆盖。失败静默降级，不影响中断流程本身。
        """
        if not getattr(self.cfg, "cognitive_anchor_enabled", True):
            return
        if self._llm is None or not self._history:
            return
        if not self._session_mgr or not self._session:
            return
        try:
            recent: list[str] = []
            for m in self._history[-12:]:
                role = m.get("role")
                content = m.get("content")
                if role not in ("user", "assistant") or not isinstance(content, str):
                    continue
                if not content.strip():
                    continue
                recent.append(f"[{role}] {content[:300]}")
            if not recent:
                return
            turns_text = "\n".join(recent)

            from mini_agent.prompts import pm
            prompt = pm.render("user/cognitive_anchor_request", turns_text=turns_text)
            resp = self._llm.chat_with_retry(
                messages=[{"role": "user", "content": prompt}],
                system=pm.render("system/cognitive_anchor"),
                tools=[],
                max_retries=2,   # 这是锦上添花的便条，不值得为它重试太多次
            )
            anchor_content = (resp.text or "").strip()
            if not anchor_content:
                return

            anchor_path = self._cognitive_anchor_path(self._session.id)
            anchor_path.parent.mkdir(parents=True, exist_ok=True)
            anchor_path.write_text(anchor_content, encoding="utf-8")
            R.print_info("[cognitive-anchor] 已记录当前思路，下次 resume 这个 session 时会自动提醒。")
        except Exception:
            pass  # 认知锚点生成失败不应影响中断流程本身

    def _bind_session_extras(self) -> None:
        """
        将当前 self._session 绑定到 TaskManager / debug logger 等周边组件。

        在以下场景调用：
          - _init_session（首次创建 session）
          - load_session（resume 一个已有 session）
          - new_session（清空历史开始新 session）
        确保 SubAgent 任务日志、LLM debug 日志始终写入"当前激活 session"对应的目录，
        而不是停留在进程启动时绑定的那个 session（这在 Web 端切换 session 时尤其重要）。
        """
        if self._session is None:
            return

        # 通知 TaskManager 当前 session_id，使 SubAgent 任务日志写到正确目录
        try:
            from mini_agent.tools.orchestration import get_task_manager
            tm = get_task_manager()
            if tm is not None:
                tm.set_session_id(self._session.id)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
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
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent')
                pass

        # raw history 路径绑定：调用独立方法（确保 _hist 已初始化后再绑定）
        self._bind_raw_path()

        # [Stage 6 / 6.1] 初始化 SessionTracer（绑定到当前 session_dir）
        self._init_tracer()

        # ExecutionPlan 持久化快照绑定 + 恢复（W1，对应设计文档 8.1 节）
        # session 启动时检测 plan_snapshot.json 是否存在，存在则尝试恢复
        # （new_session 创建的是全新 session_id，天然不会撞上旧快照文件，
        # 因此这里的"存在即恢复"逻辑对三个调用场景都是安全的）。
        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.orchestrator.plan import bind_plan_session, try_restore_plan, clear_plan

            snapshot_path = AgentPaths(self.cfg.project_root).session_plan_snapshot(self._session.id)
            clear_plan()  # 切换 session 时先清空旧 session 残留的内存计划
            try_restore_plan(snapshot_path)   # 存在则恢复，不存在则静默跳过
            bind_plan_session(snapshot_path)  # 无论是否恢复成功，都绑定为当前 session 路径
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

    def _init_tracer(self) -> None:
        """[Stage 6 / 6.1] 初始化 SessionTracer，绑定到当前 session_dir。

        在 _bind_session_extras 里调用（三个场景：_init_session / load_session /
        new_session），确保 tracer 始终指向"当前激活 session"对应的目录。
        """
        if self._session is None:
            return
        try:
            from mini_agent.perception.observability import SessionTracer
            from mini_agent.storage.paths import AgentPaths
            paths = AgentPaths(self.cfg.project_root)
            session_dir = paths.session_dir(self._session.id)
            enabled = getattr(self.cfg, "tracing_enabled", True)
            self._tracer = SessionTracer(session_dir, self._session.id, enabled=enabled)
        except Exception:
            self._tracer = None
        # 同步更新 ToolExecutor 的 tracer 引用
        if hasattr(self, '_tool_executor') and self._tool_executor is not None:
            self._tool_executor._tracer = self._tracer

    def _bind_raw_path(self) -> None:
        """将 raw history 的 .jsonl 文件路径绑定到当前 session，启用即时落盘。

        必须在 self._hist 和 self._session 都已就绪后调用。
        _bind_session_extras 在 _init_session 时被调用，此时 _hist 尚未创建，
        所以这里做防御性检查；真正的绑定发生在 _init_components 末尾的补充调用。
        """
        if self._session is None or not hasattr(self, "_hist"):
            return
        try:
            from mini_agent.storage.paths import AgentPaths
            from pathlib import Path as _Path
            raw_path = (
                AgentPaths(self.cfg.project_root)
                .session_dir(self._session.id) / "raw_history.jsonl"
            )
            self._hist._raw.set_path(raw_path)
        except Exception as _e:
            import sys
            print(f"[raw_history] set_path failed: {_e}", file=sys.stderr)

    def save_session(self) -> Optional[str]:
        """保存当前对话历史到 Session 文件，返回路径，失败返回 None。"""
        if not self._session_mgr or not self._session:
            return None
        try:
            # 角色扮演系统：将当前激活角色同步进 session，随 meta.json 持久化
            self._session.active_persona = self.active_persona
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
                raw_history=self._hist._raw,
            )

            # [SYS-SUMMARY] 达到门槛后生成/刷新 session 摘要与记忆（耗时较长，放到后台线程，避免阻塞主流程）
            # 条件：达到最小轮次，且自上次生成以来又新增了至少 min_turns 轮（而非"只生成一次"）
            turns_since_last = self.stats.turns - getattr(self._session, "summary_at_turns", 0)
            if (self.cfg.session_summary_enabled
                    and self.stats.turns >= self.cfg.session_summary_min_turns
                    and turns_since_last >= self.cfg.session_summary_min_turns):
                self.trigger_summary_and_profile(str(path))

            return str(path)
        except Exception as e:
            R.print_warning(f"Session save failed: {e}")
            return None

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
        self.stats.tool_stats    = session.stats.get("tool_stats", {}) or {}
        self.stats.skill_activations = session.stats.get("skill_activations", {}) or {}
        # 角色扮演系统：从 session 恢复当前激活的角色（未激活则为 None）
        self.active_persona = session.active_persona

        # 同步加载 raw_history（先 clear，再 load，最后 set_path 绑定实时写入）
        self._hist._raw.clear()
        if session.file_path:
            from pathlib import Path as _Path
            session_dir = _Path(session.file_path).parent
            # 优先加载新格式 .jsonl，回退旧格式 .json
            raw_jsonl = session_dir / "raw_history.jsonl"
            raw_json  = session_dir / "raw_history.json"
            if raw_jsonl.exists():
                self._hist._raw.load_from_file(raw_jsonl)
            elif raw_json.exists():
                self._hist._raw.load_from_file(raw_json)

        # 切换 session 后重新绑定 TaskManager / debug logger / raw_history 路径
        self._bind_session_extras()

        # [具身改进 C3 / 认知锚点 session 化] resume 到具体某个 session 时，
        # 检查这个 session 自己目录下是否留有认知锚点——用 self._session.id
        # （已经过 SessionManager.load() 的前缀匹配解析出的完整 id）而不是
        # 入参 session_id（可能只是前缀），确保定位到正确的目录。
        self._maybe_load_cognitive_anchor(self._session.id)

        return True

    def new_session(self) -> bool:
        """
        清空当前历史与统计，开始一个全新的 session（尚未写文件）。
        与 CLI `/session new` 等价，但额外完成 TaskManager / debug logger 重绑定，
        供 HTTP API（Web 端"新建会话"）调用。
        """
        if not self._session_mgr:
            return False
        self._history.clear()
        self.stats = SessionStats()
        # 角色扮演系统：新 session 不继承上一个 session 的角色状态
        self.active_persona = None
        self._session = self._session_mgr.new_session(
            provider=getattr(self.cfg, "llm_provider", "unknown"),
            model=self.cfg.model,
        )
        self._bind_session_extras()

        # [FIX] /session new 应彻底清空状态：TaskManager 是模块级单例，
        # _bind_session_extras() 里只做了 set_session_id()（切换日志落盘路径），
        # 并不会清空上一个 session 遗留下来的 SubAgent 任务记录（_records/_agents）。
        # 这里显式 reset，避免旧 session 的任务状态泄漏到新 session
        # （例如 /task list、终端任务面板仍能看到上一个 session 的任务）。
        # 注意：resume（load_session）不调用此逻辑，因为 resume 应该看到
        # 该 session 原有的任务记录。
        try:
            from mini_agent.tools.orchestration import get_task_manager
            tm = get_task_manager()
            if tm is not None:
                tm.reset()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

        return True

    @property
    def session_id(self) -> Optional[str]:
        return self._session.id if self._session else None

    @property
    def session_meta(self):
        """返回当前 session 的 SessionMeta（含实时 stats），尚无 session 时返回 None。

        与 session_manager.list_sessions() 不同，本属性反映的是
        Agent 内存中的实时状态（包括尚未 save_session() 落盘的最新 session），
        主要供 HTTP API 在列举 session 时补充"当前会话"信息。
        """
        if self._session is None:
            return None
        meta = self._session.meta
        meta.turns         = self.stats.turns
        meta.input_tokens  = self.stats.input_tokens
        meta.output_tokens = self.stats.output_tokens
        meta.tool_calls    = self.stats.tool_calls
        return meta

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

    def clear_history(self) -> None:
        self._history.clear()

    def close(self) -> None:
        """显式关闭 Agent 持有的所有文件句柄（raw_history 等）。
        
        测试代码应在 tearDown 中调用此方法，确保 Windows 下
        TemporaryDirectory 清理时不会出现 PermissionError (WinError 32)。
        """
        try:
            if hasattr(self, '_hist') and self._hist is not None:
                if hasattr(self._hist, '_raw') and self._hist._raw is not None:
                    self._hist._raw._close_file()
        except Exception:
            pass

