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

import re as _re

# ── 工具错误识别（Stage 1.2 起迁移至 perception/lesson_rules.py，供 ──────────
#    tool_executor.py 共享复用，避免循环依赖；这里保留 _is_tool_error 别名
#    以兼容本文件内现有调用点）─────────────────────────────────────────────────
from mini_agent.perception.lesson_rules import is_tool_error as _is_tool_error


def _clamp_confidence(value) -> float:
    """把 LLM 返回的 confidence 字段安全转换并裁剪到 [0, 1]。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, v))


def _parse_lesson_candidates(text: str) -> list[dict]:
    """
    解析 SessionEnd 反思 LLM 调用返回的 lesson 候选 JSON 数组。

    容错处理：
    - 模型偶尔会用 ```json ... ``` 包裹，先尝试剥离代码块围栏
    - 解析失败或返回的不是数组时，返回空列表（不抛异常，反思失败应静默降级）
    - 数组中非 dict 的元素会被过滤掉
    """
    if not text or not text.strip():
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 剥离 ```json\n...\n``` 或 ```\n...\n``` 围栏
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    import json as _json
    try:
        data = _json.loads(cleaned)
    except Exception:
        return []

    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _parse_timeline_summary(text: str) -> dict:
    """
    解析 timeline 反思 LLM 调用返回的 {theme, key_outcomes} JSON 对象（W2，4.2）。

    与 _parse_lesson_candidates 的容错策略一致（剥离 ```json 围栏、解析失败时
    静默降级），但目标结构是单个 dict 而不是数组。解析失败或字段缺失时返回
    空 dict，调用方据此决定是否跳过本次 timeline 追加。
    """
    if not text or not text.strip():
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    import json as _json
    try:
        data = _json.loads(cleaned)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}
    return data


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
        tool_cache: Optional[ToolResultCache] = None,
        is_subagent: bool = False,
    ) -> None:
        self.cfg = cfg
        self._is_subagent = is_subagent

        # [SYS-TURN-JUDGE] 本轮是否撞到 max_turns 硬顶（供 TurnJudge 判定参考）；
        # 连续自动接管计数（每次真正进入真人输入等待后由 repl 重置为 0）。
        self._last_turn_hit_max_turns: bool = False
        self._turn_judge_auto_count: int = 0

        from mini_agent.tools.builtin import configure_web_search
        configure_web_search(cfg)

        self.registry = registry or get_default_registry()
        self.skill_loader = skill_loader

        # 角色扮演（Persona）系统：当前激活的角色 name，None = 未激活/默认人格。
        # 由 /role use|exit 命令读写；随 session 持久化（见 session meta）。
        self.active_persona: Optional[str] = None
        self.guard = guard or PermissionGuard(
            auto_approve=cfg.auto_approve,
            sandbox=cfg.sandbox,
            project_root=cfg.project_root,
        )

        # [Phase C / 3.1] 注册 project_root provider（thread-local，与 active-skills
        # provider 同款写法），供 skill_propose 等无状态工具函数读取当前 agent
        # 所在的项目根目录。不放在 `if self.skill_loader:` 分支里——project_root
        # 是比 skill 更基础的上下文，任何 Agent（不论是否携带 skill_loader）
        # 都应该让同线程内的 skill_propose 调用能找到正确的项目根目录。
        from mini_agent.tools.evolution import set_project_root_provider
        set_project_root_provider(lambda: self.cfg.project_root)

        # [W2 / Stage 4] 同样为 tools/workdir_knowledge.py（add_open_thread /
        # update_work_thread / update_knowledge / search_knowledge）注册
        # project_root + session_id provider。session_id 用懒读取的 lambda
        # （`self._session` 此时尚未创建，但 lambda 在工具被调用时才执行，
        # 届时 _init_session() 已经跑完）。
        from mini_agent.tools.workdir_knowledge import (
            set_project_root_provider as _set_wk_project_root_provider,
            set_session_id_provider as _set_wk_session_id_provider,
        )
        _set_wk_project_root_provider(lambda: self.cfg.project_root)
        _set_wk_session_id_provider(
            lambda: self._session.id if self._session else ""
        )

        # daemon 多用户架构 Phase 2：导入 tools/user_memory.py 触发
        # remember_about_user 的 @tool 注册（与上面两处同样的写法——
        # 仅仅是 import 这个模块就会执行模块级的 @tool 装饰器）。
        # 这个工具不需要 thread-local provider 在这里注册——它的"当前用户"
        # 是由 AgentRunner.run() 在每轮调用前直接设置的（原因见
        # tools/user_memory.py 模块docstring），与 project_root/session_id
        # 这种"Agent 生命周期内基本不变"的上下文性质不同，不适合在这里注册。
        import mini_agent.tools.user_memory  # noqa: F401

        self.stats = SessionStats()
        self._history: list[dict] = []

        # [SYS-PRIVACY] 隐私保护：发送前屏蔽，收到后还原
        from mini_agent.perception.privacy_guard import PrivacyGuard, SecretEntry
        self._privacy_guard = PrivacyGuard.from_config(cfg.privacy)

        # 自动把所有 provider key 注册进隐私保护，无论 auto_env_patterns 是否覆盖到
        if cfg.privacy.enabled:
            _keys_to_guard: list[SecretEntry] = []
            # 主 provider key
            if cfg.api_key:
                _keys_to_guard.append(SecretEntry(name=f"{cfg.llm_provider}.api_key", value=cfg.api_key))
            # fallback chain 里的所有 key
            for _entry in (cfg.llm_fallback_chain or []):
                _p = _entry.get("provider", "unknown")
                for _k in (_entry.get("api_keys") or []):
                    _keys_to_guard.append(SecretEntry(name=f"{_p}.api_key", value=_k))
                if _entry.get("api_key"):
                    _keys_to_guard.append(SecretEntry(name=f"{_p}.api_key", value=_entry["api_key"]))
            # web search key
            if getattr(cfg, "web_search", None) and cfg.web_search.api_key:
                _keys_to_guard.append(SecretEntry(name="web_search.api_key", value=cfg.web_search.api_key))
            # HTTP API token
            if getattr(cfg, "http", None) and cfg.http.api_token:
                _keys_to_guard.append(SecretEntry(name="http.api_token", value=cfg.http.api_token))
            for _e in _keys_to_guard:
                self._privacy_guard._register(_e)

        if cfg.privacy.verbose and self._privacy_guard.active:
            import mini_agent.ui.renderer as _R
            _R.console.print(
                f"[dim][privacy] active — registered secrets:\n"
                f"{self._privacy_guard.summary()}[/dim]"
            )

        # LLMClient 可从外部注入（便于测试），否则从 AppConfig 自动创建
        self._llm: LLMClient = llm_client or create_client(
            LLMConfig.from_app_config(cfg)
        )
        # LLMClientPool：多配置故障转移 + 多 key 轮转
        # 若外部注入了 llm_client（测试场景），pool 也退化为单条链
        if llm_client is not None:
            from mini_agent.llm.client_pool import ProviderEntry
            _entry = ProviderEntry(
                config=LLMConfig.from_app_config(cfg),
                client=llm_client,
                key_pool=None,
            )
            self._client_pool = LLMClientPool(entries=[_entry])
        else:
            self._client_pool = LLMClientPool.from_config(cfg)
            # 保持 self._llm 与 pool 主 client 同步
            self._llm = self._client_pool.current_client
        # Session 持久化
        self._session_mgr: Optional[SessionManager] = None
        self._session: Optional[Session] = None
        # [Stage 6 / 6.1] Session tracer — 在 _bind_session_extras 里绑定到具体 session_dir
        self._tracer: Optional[object] = None  # SessionTracer | None
        self._init_session()

        # ── [SYS-RETRY] LLM 重试策略初始化 ──────────────────────────────────
        # 默认使用 EmptyOutputCondition（空输出即重试），可通过 cfg 调整参数，
        # 也可在实例化后替换 self._retry_policy 以使用自定义策略。
        _retry_max   = getattr(cfg, "llm_retry_max", 2)
        _retry_delay = getattr(cfg, "llm_retry_delay", 0.0)
        _backoff_mode     = getattr(cfg, "llm_retry_backoff_mode", "fixed")
        _backoff_step     = getattr(cfg, "llm_retry_backoff_step", 60.0)
        _backoff_max_delay = getattr(cfg, "llm_retry_backoff_max_delay", 0.0)
        _backoff = parse_backoff(
            mode=_backoff_mode,
            initial=_retry_delay,
            step_or_multiplier=_backoff_step,
            max_delay=_backoff_max_delay,
        )
        self._retry_policy: RetryPolicy = (
            default_retry_policy(max_retries=_retry_max, backoff=_backoff)
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

            # [Phase E / 3.3] 注册"当前激活 skill 列表"provider，供 spawn_agent /
            # spawn_named_agent 工具读取，写入新建 Task 的 active_skills 字段，
            # 使 SubAgent 启动时能继承主 agent 当前激活的 skill（设计文档第 5 节）。
            from mini_agent.tools.orchestration import set_active_skills_provider
            set_active_skills_provider(lambda: self.skill_loader.active)

        # [SYS-HOT-RELOAD] 热重载监视器：自动感知 skills/ 和 .agent/agents/ 目录变化
        from mini_agent.perception.hot_reload import HotReloader
        self._hot_reloader = HotReloader(
            min_interval_s=getattr(cfg, "hot_reload_interval_s", 2.0)
        )
        if self.skill_loader:
            self._hot_reloader.register(
                dirs=self.skill_loader._dirs,
                reload_fn=self.skill_loader.rediscover,
                category="skill",
            )
        # agent profiles 由模块级单例管理，这里取引用
        from mini_agent.orchestrator.agent_profiles import get_profile_loader
        _apl = get_profile_loader()
        if _apl is not None:
            self._hot_reloader.register(
                dirs=_apl._dirs,
                reload_fn=_apl.rediscover,
                category="agent",
            )
        # personas（角色扮演）由模块级单例管理，与 agent profiles 同模式接入热重载
        from mini_agent.orchestrator.persona_profiles import get_persona_loader
        _ppl = get_persona_loader()
        if _ppl is not None:
            self._hot_reloader.register(
                dirs=_ppl._dirs,
                reload_fn=_ppl.rediscover,
                category="persona",
            )

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
        # [Phase E / 3.3] 若构造时显式传入 tool_cache（SubAgent 跨任务共享场景，
        # 见 orchestrator/task_manager.py 的 _shared_tool_cache），直接复用该实例，
        # 不再各自新建一份私有缓存——ToolResultCache 内部已加锁，可安全跨线程共享。
        if tool_cache is not None:
            self._tool_cache: Optional[ToolResultCache] = tool_cache
        else:
            self._tool_cache = (
                ToolResultCache(max_entries=cfg.perception.tool_cache_max_entries) if cfg.tool_cache_enabled else None
            )

        # [SYS-RAWSTORE] 原始工具结果留存（截断/摘要后仍可通过 view_raw_result 回看）
        self._raw_result_store: Optional[RawResultStore] = None
        if getattr(cfg, "raw_store_enabled", True):
            from mini_agent.perception.raw_result_store import RawResultStore as _RawResultStore
            from mini_agent.tools.builtin import configure_raw_result_store
            self._raw_result_store = _RawResultStore(
                max_entries=cfg.tool_trim.raw_store_max_entries,
                max_total_chars=cfg.tool_trim.raw_store_max_total_chars,
            )
            configure_raw_result_store(self._raw_result_store)

        # [SYS-MEMORY] 跨 session 长期记忆（通过工厂创建，支持多后端）
        self._memory: Optional[MemoryBackend] = None
        self._global_memory: Optional[MemoryBackend] = None
        if cfg.memory_enabled:
            from mini_agent.perception.memory_factory import create_both_memory_backends
            self._memory, self._global_memory = create_both_memory_backends(cfg)

            # [Phase E / 3.3] 向已存在的 TaskManager 登记【主 agent】的 memory
            # backend，使 SubAgent 结束时能触发 reload()（详见
            # TaskManager._reload_main_memory_sinks）。
            #
            # 必须用 is_subagent 显式区分，不能简单地"谁先构造谁登记"：
            # SubAgent 是在 TaskManager 的后台调度线程里异步构造的，时间上完全
            # 可能晚于主 agent（例如主 agent 在某个 turn 里调用 spawn_agent 之后），
            # 如果不加区分，SubAgent 自己的 Agent.__init__ 会把主 agent 的登记
            # 覆盖掉，导致本应回灌给主 agent 的 reload() 调用错误地作用在某个
            # 已经跑完、即将被回收的 SubAgent 的 memory 实例上——表现为"主 agent
            # 再也收不到任何 SubAgent 产生的新 lesson"，且没有任何报错，非常隐蔽。
            if not self._is_subagent:
                from mini_agent.tools.orchestration import get_task_manager
                _tm = get_task_manager()
                if _tm is not None:
                    _tm.set_memory_sinks(memory=self._memory, global_memory=self._global_memory)

        # [SYS-PROFILE] 用户画像（单用户模式：user_id=None -> ~/.agent/profile.json）
        self._profile_mgr: Optional["UserProfileManager"] = None
        if cfg.profile_enabled:
            from mini_agent.profile import UserProfileManager
            from mini_agent.storage.paths import AgentPaths
            self._profile_mgr = UserProfileManager(AgentPaths(cfg.project_root), user_id=None)

        # [SYS-SUMMARY] 防止多个摘要/记忆生成任务并发运行（互斥，非阻塞获取）
        self._summary_lock = threading.Lock()

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
            self._maybe_load_cognitive_anchor()
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
            except Exception:
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

    def _maybe_load_cognitive_anchor(self) -> None:
        """
        [具身改进 C3] session 启动时检查是否存在认知锚点文件，若存在则
        优先注入 system_extra（"恢复记忆"），并归档（重命名加时间戳后缀）——
        消费一次即归档，避免同一份锚点被无限期重复注入到后续每个 session。

        与 B4 AffordanceMap 的协作：二者都写入 cfg.system_extra，但分别在
        不同时机调用（AffordanceMap 由 SessionAgentPool 在多用户路径里注入，
        认知锚点在 agent.py 这里对本地/daemon 两条路径统一生效）——拼接顺序
        不强制，system_extra 是简单的文本累加。
        """
        if not getattr(self.cfg, "cognitive_anchor_enabled", True):
            return
        try:
            from mini_agent.storage.paths import AgentPaths
            paths = AgentPaths(self.cfg.project_root)
            anchor_path = paths.workdir_cognitive_anchor
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
            pass  # 锚点恢复失败不应影响 session 启动

    def _save_cognitive_anchor(self) -> None:
        """
        [具身改进 C3] 任务被用户明确打断时（Ctrl-C / /stop）调用，生成一份
        "思维状态重建指南"写入 .agent/cognitive_anchor.md，供下次 session
        恢复时读取（见 _maybe_load_cognitive_anchor）。

        内容由 LLM 生成，格式固定（见 prompts/system/cognitive_anchor.md），
        是"给被打断后返回的自己看的便条"，不是给人类看的进展报告——后者已经
        由 history/timeline 覆盖。失败静默降级，不影响中断流程本身。
        """
        if not getattr(self.cfg, "cognitive_anchor_enabled", True):
            return
        if self._llm is None or not self._history:
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

            from mini_agent.storage.paths import AgentPaths
            paths = AgentPaths(self.cfg.project_root)
            anchor_path = paths.workdir_cognitive_anchor
            anchor_path.parent.mkdir(parents=True, exist_ok=True)
            anchor_path.write_text(anchor_content, encoding="utf-8")
            R.print_info("[cognitive-anchor] 已记录当前思路，下次恢复时会自动提醒。")
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
        except Exception:
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

    def _get_profile_text(self) -> str:
        """供 ContextBuilder 注入 system prompt 的用户画像摘要（无画像则返回空串）。"""
        if not self._profile_mgr:
            return ""
        try:
            profile = self._profile_mgr.load()
        except Exception:
            return ""
        return profile.derived.get("summary", "") if profile.derived else ""

    def _maybe_refresh_profile(self, force: bool = False) -> None:
        """
        [SYS-PROFILE] 检查是否需要(重新)生成用户画像，若需要则同步生成。

        本方法预期在 _generate_and_save_summary 的后台线程中被调用（已经
        不阻塞主流程），因此这里直接同步调用 LLM，不再额外开线程。

        force=True 时跳过 should_refresh 的间隔判断，只要有记忆条目就重新生成
        （由 /profile 命令触发）。
        """
        if not self._profile_mgr:
            if force:
                R.print_warning("用户画像功能未开启（profile.enabled=false）。")
            return
        # 画像基于长期记忆：默认条目写入 project-scope（self._memory），
        # global-scope（self._global_memory）为可选的跨项目记忆，两者都要合并考虑。
        sources = [s for s in (self._memory, self._global_memory) if s is not None]
        if not sources:
            if force:
                R.print_warning("记忆功能未开启，无法生成用户画像。")
            return
        try:
            entries = []
            for s in sources:
                entries.extend(s.all_entries())
            if not entries:
                if force:
                    R.print_warning("暂无可用于生成画像的长期记忆。")
                return
            count = len(entries)
            if not force and not self._profile_mgr.should_refresh(count, self.cfg):
                return
            # 按 created_at 升序取最近 N 条
            entries = sorted(entries, key=lambda e: e.created_at)[-self.cfg.profile.max_entries_for_profile:]
            R.print_info("正在更新用户画像(profile)...")
            self._profile_mgr.generate(self._llm, entries)
            R.print_info("用户画像(profile)已更新")
        except Exception as e:
            R.print_warning(f"用户画像生成失败: {e}")

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
                R.print_warning("当前没有可保存的会话。")
                return False
            session_path = self._session.file_path or ""

        if self._summary_lock.locked():
            R.print_warning("摘要/画像生成任务正在进行中，请稍后再试。")
            return False

        R.print_info("正在后台生成会话摘要 / 更新长期记忆...")
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
                R.print_warning("摘要/画像生成任务正在进行中，请稍后再试。")
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
                    R.print_warning("当前会话没有可摘要的用户消息。")
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
                    self._global_memory.upsert(entry)
                else:
                    self._memory.upsert(entry)
                # 同时写入 memory_delta.jsonl（session 审计）
                self._append_memory_delta(entry)
            R.print_info("会话摘要记忆已生成")

            # [SYS-PROFILE] 同一后台线程内顺带检查并刷新用户画像
            self._maybe_refresh_profile(force=force)
        except Exception as e:
            R.print_warning(f"[summary] generation failed: {e}")
        finally:
            self._summary_lock.release()

    def trigger_session_end(self) -> None:
        """
        [SYS-SESSION-END] 会话真正结束时调用：触发 SessionEnd hook + 反思生成 lesson。

        对应 self_evolution_implementation_plan.md Stage 1.3 / 设计文档第 3 节
        "SessionEnd hook（目前是预留未接的事件）"。

        调用时机：REPL 退出（EOFError / exit / quit / /exit / /quit），即将退出进程前。
        因此本方法是同步执行（不开后台线程）——进程退出后台线程来不及跑完没有意义，
        但内部做好超时与异常隔离，确保反思失败/缓慢不会导致退出流程卡死或抛出异常。
        """
        if not self._session:
            return

        # [SYS-HOOKS] 触发 SessionEnd 事件（先于 LLM 反思，给 hook 一个"看到原始数据"的机会）
        payload = {
            "session_id": self._session.id,
            "tool_stats": dict(self.stats.tool_stats),
            "turns": self.stats.turns,
            "input_tokens": self.stats.input_tokens,
            "output_tokens": self.stats.output_tokens,
        }
        from mini_agent.hooks import get_hook_manager
        hook_mgr = get_hook_manager()
        if hook_mgr is not None:
            try:
                hook_mgr.run("SessionEnd", payload)
            except Exception:
                pass  # SessionEnd hook 失败不应阻塞退出流程

        # [W2+W3 / 4.2-4.4 + 5.3 + 5.5] Workdir + Global 知识层更新：timeline /
        # work_index / open_threads / activity_log / self_profile。纯写入为主
        # （无 LLM 依赖），theme/key_outcomes 这一项需要一次轻量反思
        # 调用，单独捕获异常，不让其失败影响 lesson 反思或退出流程。
        try:
            self._update_workdir_knowledge_on_session_end()
        except Exception as e:
            R.print_warning(f"[session-end] workdir knowledge update failed: {e}")

        # [Stage 6 / 6.3] 观察性：SessionEnd 时写入量化指标 + 异常检测
        try:
            self._run_observability_on_session_end()
        except Exception:
            pass

        # [Stage 8 / 8.1] Phase G 时间门控：每 24h 自动触发一次后台循环扫描
        try:
            self._maybe_run_phase_g()
        except Exception:
            pass

        # [具身改进 C4] 自维护模块：每 24h 自动触发一次健康检查
        # （可能失效的工具 / 过时 skill / 矛盾的 lesson），与 Phase G
        # 采用同款时间门控模式，互不干扰（各自独立的 last_run_at 状态文件）。
        try:
            self._maybe_run_self_maintenance()
        except Exception:
            pass

        # [SYS-LESSON] 反思 LLM 调用：基于 tool_stats + 最后若干轮 history 生成 lesson 候选
        if not self.cfg.memory.enabled or self._memory is None:
            return
        try:
            self._reflect_and_save_lessons()
        except Exception as e:
            # 反思失败是可接受的降级（不影响本次对话已有的价值），仅打印警告
            R.print_warning(f"[session-end] reflection failed: {e}")

    def _reflect_and_save_lessons(self, max_lessons: int = 5) -> int:
        """
        跑一次轻量 LLM 反思调用，基于 tool_stats + 最后若干轮 history（用
        is_turn_boundary 精确截取"用户意图轮"）生成结构化 lesson 候选并写入记忆。

        返回实际写入的 lesson 条数（供调用方/测试断言）。
        """
        from mini_agent.history.entry import is_turn_boundary

        user_turns = [
            m["content"] for m in self._history
            if is_turn_boundary(m) and isinstance(m.get("content"), str)
        ]
        if not user_turns and not self.stats.tool_stats:
            return 0  # 没有任何可反思的内容，跳过 LLM 调用

        tool_stats_lines = [
            f"- {name}: {s.get('calls', 0)} calls, {s.get('success', 0)} succeeded, {s.get('fail', 0)} failed"
            for name, s in self.stats.tool_stats.items()
        ] or ["(no tool calls this session)"]
        turns_text = "\n".join(f"- {t[:200]}" for t in user_turns[-10:]) or "(no user turns)"

        from mini_agent.prompts import pm
        prompt = pm.render(
            "user/session_reflection_request",
            tool_stats_text="\n".join(tool_stats_lines),
            turns_text=turns_text,
        )
        resp = self._llm.chat_with_retry(
            messages=[{"role": "user", "content": prompt}],
            system=pm.render("system/session_reflection"),
            tools=[],
            max_retries=3,   # 反思是锦上添花，不值得像主对话那样重试 10 次
        )
        candidates = _parse_lesson_candidates(resp.text)
        saved = 0
        for cand in candidates[:max_lessons]:
            entry = MemoryEntry(
                session_id=self._session.id,
                summary="",
                key_outcomes=[],
                tags=["lesson", "session_reflection"],
                model=self.cfg.model,
                entry_type="lesson",
                trigger=str(cand.get("trigger", ""))[:500],
                outcome=str(cand.get("outcome", ""))[:500],
                root_cause=str(cand.get("root_cause", ""))[:500],
                suggested_action=str(cand.get("suggested_action", ""))[:500],
                confidence=_clamp_confidence(cand.get("confidence", 0.5)),
                occurrence_count=1,
                source="self_reflection",
            )
            if entry.scope == "global" and self._global_memory:
                self._global_memory.add(entry)
            else:
                self._memory.add(entry)
            self._append_memory_delta(entry)
            saved += 1

        # ── [W3 / 5.5 事件驱动更新] lesson 生成是即时事件，不等 SessionEnd
        #    的批量维护路径——直接在产生的那一刻 +saved，与设计文档原话
        #    "在对应事件发生时直接 +1，不等 session 结束"一致。失败不影响
        #    已经成功写入的 lesson（self_profile 是衍生统计，不是权威数据）。
        if saved and getattr(self.cfg, "global_knowledge_enabled", True):
            try:
                from mini_agent.storage.paths import AgentPaths
                from mini_agent.perception import global_knowledge as gk
                import time as _time
                paths = AgentPaths(self.cfg.project_root)
                profile = gk.ensure_self_profile(paths)
                profile.evolution_state.lifetime_lessons_generated += saved
                profile.evolution_state.last_reflection_at = _time.time()
                gk.save_self_profile(paths, profile)
            except Exception:
                pass

        return saved

    # ── [W2+W3 / Stage 4-5] Workdir + Global 知识层：SessionEnd 维护路径 ─────

    def _update_workdir_knowledge_on_session_end(self) -> None:
        """
        SessionEnd hook 轻量路径（设计文档 8.2/8.3 节"三条触发路径"之一）：
          - 追加 timeline.jsonl 一条 session 概览（4.2）
          - 尝试把本次 session 关联到一个 active WorkThread（4.3 最简版本）
          - 把本次 session 各 task manifest 的 outcome.unresolved 推进
            open_threads.json（4.4）
          - 追加 activity_log.jsonl 一条全局活动记录，复用同一次 theme/
            duration 计算（5.3）
          - 更新 self_profile.json 的 operating_state（5.5）

        纯写入部分（work_index 关联、open_threads 推进、activity_log/
        self_profile 更新）无 LLM 依赖，始终执行；theme/key_outcomes 需要
        一次独立的轻量反思调用（与 _reflect_and_save_lessons 的诊断型反思
        目标不同，见 Stage 4.2 计划文档的取舍说明），调用失败时
        theme/key_outcomes 留空但仍写入 timeline 行（保留
        task_count/status/duration 等无需 LLM 的字段）。方法名沿用 W2 阶段
        命名，未改名为更通用的名字——调用方（trigger_session_end）只有一处，
        改名收益不大，保留命名稳定性。
        """
        if not getattr(self.cfg, "workdir_knowledge_enabled", True):
            return
        if not self._session:
            return

        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception import workdir_knowledge as wk

        paths = AgentPaths(self.cfg.project_root)
        session_id = self._session.id

        # ── 收集本次 session 的 task manifest（来源：磁盘上的 manifest.json，
        #    覆盖主线程内 TaskManager 已知的任务，也覆盖跨进程恢复的场景）──
        unresolved_all: list[str] = []
        task_count = 0
        try:
            tasks_root = paths.tasks_dir(session_id)
            if tasks_root.is_dir():
                for task_dir in tasks_root.iterdir():
                    manifest_path = task_dir / "manifest.json"
                    if not manifest_path.is_file():
                        continue
                    task_count += 1
                    try:
                        import json as _json
                        data = _json.loads(manifest_path.read_text(encoding="utf-8"))
                        outcome = data.get("outcome") or {}
                        unresolved_all.extend(outcome.get("unresolved", []) or [])
                    except Exception:
                        continue
        except Exception:
            pass

        # ── 4.4：把 unresolved 推进 open_threads.json ────────────────────────
        if unresolved_all:
            try:
                wk.import_unresolved_from_manifest(paths, session_id, unresolved_all)
            except Exception:
                pass

        # ── 4.3：关联到 active WorkThread（轻量启发式，不新建 WorkThread）───
        try:
            relation_days = getattr(
                self.cfg.workdir_knowledge, "work_thread_relation_days", 7.0
            )
            from mini_agent.history.entry import is_turn_boundary
            first_user_turn = next(
                (m["content"] for m in self._history
                 if is_turn_boundary(m) and isinstance(m.get("content"), str)),
                "",
            )
            wk.relate_session_to_work_thread(
                paths, session_id, first_user_turn, relation_days=relation_days,
            )
        except Exception:
            pass

        # ── 4.2：timeline.jsonl 一行概览 ──────────────────────────────────
        duration_min = self._session_duration_minutes()
        theme, key_outcomes = self._reflect_timeline_summary()
        try:
            wk.append_timeline_entry(
                paths,
                session_id=session_id,
                duration_min=duration_min,
                theme=theme,
                key_outcomes=key_outcomes,
                task_count=task_count,
                status="done",
            )
        except Exception:
            pass

        # ── [W3 / 5.3 + 5.5] Global 知识层 SessionEnd 维护：复用上面已经
        #    计算好的 theme/duration_min 写一行 activity_log.jsonl，避免
        #    两次遍历 session 数据（计划文档 5.3 节要求）；同时更新
        #    self_profile.json 的 operating_state（5.5 节，纯计数器更新，
        #    无 LLM 依赖）。两者独立 try/except，与 W2 部分互不阻塞。 ──
        if getattr(self.cfg, "global_knowledge_enabled", True):
            try:
                from mini_agent.perception import global_knowledge as gk
                project_id = gk.project_id_for(self.cfg.project_root)
                gk.append_activity_log(
                    paths,
                    project_id=project_id,
                    session_id=session_id,
                    theme=theme,
                    duration_min=duration_min,
                )
            except Exception:
                pass
            try:
                from mini_agent.perception import global_knowledge as gk
                gk.update_self_profile_on_session_end(
                    paths,
                    active_project=str(self.cfg.project_root.resolve()),
                    tokens_used=self.stats.input_tokens + self.stats.output_tokens,
                )
            except Exception:
                pass

    def _maybe_run_phase_g(self) -> None:
        """
        [Stage 8 / 8.1] SessionEnd 时的 Phase G 时间门控。

        每次 session 结束时检查"上次 Phase G 运行距今是否超过 24h"，
        是则自动触发一次轻量扫描（剪枝 + 能力地图 + 晋升候选）。
        不需要后台调度器，用 phase_g_rhythm.json 的 _last_run_at 字段实现。
        结果只打印摘要（有发现时），不阻塞退出流程。
        """
        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.evolution.phase_g import run_phase_g, should_run_phase_g

            paths = AgentPaths(self.cfg.project_root)
            if not should_run_phase_g(paths):
                return

            report = run_phase_g(
                paths,
                skill_loader=getattr(self, "skill_loader", None),
                memory_backend=getattr(self, "_memory", None),
            )

            # 只在有发现时打印摘要（避免每次退出都打印噪音）
            if report.prune_candidates:
                R.print_info(
                    f"[phase-g] 发现 {len(report.prune_candidates)} 个剪枝候选，"
                    "用 /evolve phase-g 查看详情。"
                )
            if report.promotion_candidates:
                R.print_info(
                    f"[phase-g] 发现 {len(report.promotion_candidates)} 个跨项目晋升候选，"
                    "用 /evolve phase-g 查看详情。"
                )
        except Exception:
            pass  # Phase G 失败不影响退出流程

    def _maybe_run_self_maintenance(self) -> None:
        """
        [具身改进 C4] SessionEnd 时的自维护时间门控。

        每次 session 结束时检查"上次自维护扫描距今是否超过 24h"，是则触发
        一次健康检查（可能失效的工具 / 过时 skill / 矛盾的 lesson）。结果
        写入 activity_digest.jsonl，只在有发现时打印摘要，不阻塞退出流程。
        """
        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.evolution.self_maintenance import (
                run_self_maintenance, should_run_self_maintenance,
            )

            paths = AgentPaths(self.cfg.project_root)
            if not should_run_self_maintenance(paths):
                return

            report = run_self_maintenance(
                paths,
                skill_loader=getattr(self, "skill_loader", None),
                memory_backend=getattr(self, "_memory", None),
            )

            if report.has_findings:
                R.print_info(
                    f"[self-maintenance] 发现 {len(report.stale_tools)} 个可能失效工具、"
                    f"{len(report.stale_skills)} 个过时 skill、"
                    f"{len(report.conflicting_lessons)} 组可能矛盾的经验，"
                    "详情见 activity_digest.jsonl / 下次连接的晨报。"
                )
        except Exception:
            pass  # 自维护扫描失败不影响退出流程

    def _run_observability_on_session_end(self) -> None:
        """[Stage 6 / 6.3] SessionEnd 时：
        1. 把本 session 的 total_tokens / tool_count 写入 activity_log（为异常检测提供基线数据）
        2. 运行异常检测，若触发则打印警告
        写入 activity_log 的字段是对 gk.append_activity_log 的补充（后者已写 theme/duration，
        这里追加 tool_count / total_tokens 供 detect_anomalies 使用）。
        两个步骤都是观察性数据，任何异常静默降级。
        """
        if not getattr(self.cfg, "observability_enabled", True):
            return
        if not self._session:
            return
        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.perception.observability import detect_anomalies
            paths = AgentPaths(self.cfg.project_root)
            al_path = paths.global_activity_log()

            total_tokens = self.stats.input_tokens + self.stats.output_tokens
            tool_count = getattr(self.stats, "tool_calls", 0)
            duration_min = self._session_duration_minutes()

            # 1. 把当前 session 的量化指标追加到 activity_log（追加字段，不重写已有行）
            # activity_log 条目本身由 gk.append_activity_log 写入，这里追加一条补充记录
            # 格式：单独一行 JSON，flag 字段为 "session_metrics"（与主 activity_log 行区分）
            import json as _json
            al_path.parent.mkdir(parents=True, exist_ok=True)
            metrics_entry = {
                "ts":           __import__("time").time(),
                "record_type":  "session_metrics",
                "session_id":   self._session.id,
                "tool_count":   tool_count,
                "total_tokens": total_tokens,
                "duration_min": duration_min,
            }
            with open(al_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(metrics_entry, ensure_ascii=False) + "\n")

            # 2. 异常检测（基于历史 session_metrics 记录）
            k_sigma = getattr(self.cfg.observability, "anomaly_k_sigma", 3.0)
            min_samples = getattr(self.cfg.observability, "anomaly_min_samples", 10)
            current = {
                "session_id":   self._session.id,
                "tool_count":   tool_count,
                "total_tokens": total_tokens,
                "duration_min": duration_min,
            }
            flags = detect_anomalies(al_path, current, k_sigma=k_sigma, min_samples=min_samples)
            for flag in flags:
                R.print_warning(
                    f"[anomaly] {flag.flag_type}: 当前值 {flag.value:.1f} 超出基线 "
                    f"(均值 {flag.baseline:.1f}, 阈值 {flag.threshold:.1f})"
                )
        except Exception:
            pass

    def _session_duration_minutes(self) -> float:
        """从 Session.created_at（ISO 字符串，_now_iso() 格式）估算本次 session 时长（分钟）。
        解析失败时返回 0.0（不阻塞 timeline 写入）。"""
        if not self._session or not getattr(self._session, "created_at", ""):
            return 0.0
        try:
            from datetime import datetime, timezone
            created = datetime.strptime(self._session.created_at, "%Y-%m-%dT%H:%M:%S")
            created = created.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (now - created).total_seconds() / 60.0)
        except Exception:
            return 0.0

    def _reflect_timeline_summary(self) -> tuple[str, list[str]]:
        """
        独立的轻量反思调用：生成 {theme, key_outcomes}（4.2 节方案①）。

        与 _reflect_and_save_lessons 的诊断型反思（trigger/root_cause/
        suggested_action）目标不同，故不复用同一次 LLM 调用——两种反思
        目标混在一起容易互相干扰输出质量，详见 Stage 4.2 计划文档。

        没有任何用户意图轮次时跳过 LLM 调用，直接返回空概览。
        """
        from mini_agent.history.entry import is_turn_boundary

        user_turns = [
            m["content"] for m in self._history
            if is_turn_boundary(m) and isinstance(m.get("content"), str)
        ]
        if not user_turns:
            return "", []

        turns_text = "\n".join(f"- {t[:200]}" for t in user_turns[-10:])

        try:
            from mini_agent.prompts import pm
            prompt = pm.render("user/timeline_reflection_request", turns_text=turns_text)
            resp = self._llm.chat_with_retry(
                messages=[{"role": "user", "content": prompt}],
                system=pm.render("system/timeline_reflection"),
                tools=[],
                max_retries=3,
            )
            data = _parse_timeline_summary(resp.text)
        except Exception:
            return "", []

        theme = str(data.get("theme", ""))[:200]
        raw_outcomes = data.get("key_outcomes", []) or []
        if not isinstance(raw_outcomes, list):
            raw_outcomes = []
        key_outcomes = [str(o)[:200] for o in raw_outcomes[:5]]
        return theme, key_outcomes

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
        except Exception:
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

    @property
    def llm_client(self) -> LLMClient:
        return self._llm

    def clear_history(self) -> None:
        self._history.clear()

    def switch_provider(self, llm_config: LLMConfig) -> None:
        """
        运行时切换 LLM provider，不影响对话历史。
        同时重建 LLMClientPool 为单条链（新 config）。

        Example:
            agent.switch_provider(LLMConfig(provider="openai", model="gpt-4o", api_key="..."))
        """
        from mini_agent.llm.client_pool import ProviderEntry
        new_client = create_client(llm_config)
        self._llm = new_client
        entry = ProviderEntry(config=llm_config, client=new_client, key_pool=None)
        self._client_pool = LLMClientPool(entries=[entry])
        self.cfg.model = llm_config.model
        self.cfg.llm_provider = llm_config.provider
        R.print_info(f"Switched to {self._llm}")

    def switch_model(self, model: str) -> "ProviderEntry":  # noqa: F821 (前向引用，运行时从 client_pool 导入)
        """
        运行时切换模型（保持当前 provider 不变，除非该模型属于 fallback chain
        中的另一个 provider）。

        行为：
          1. 先在当前 LLMClientPool 的 fallback chain 中查找匹配该 model 名称
             的已配置条目——若找到，直接切换 _current_idx 指向它（该条目早已
             持有一个就绪的 client，无需重建，API key/provider 也随之带过去）。
          2. 若 fallback chain 中没有这个模型，则在**当前 provider**下用新的
             model 名构造一个新的 LLMConfig（沿用当前 api_key/base_url 等），
             创建新 client，作为新条目追加进 fallback chain 并激活。

        这保证了 /model <name> 不再只是改一个不会被实际使用的字符串，而是
        真正让后续的 LLM 调用使用新模型对应的 client。

        Returns:
            切换后激活的 ProviderEntry。
        """
        from mini_agent.llm.client_pool import ProviderEntry

        idx = self._client_pool.find_entry_index(model=model)
        if idx is not None:
            entry = self._client_pool.switch_to_index(idx)
        else:
            current = self._client_pool.current_entry
            new_cfg = LLMConfig(
                provider=current.config.provider,
                model=model,
                api_key=current.config.api_key,
                base_url=current.config.base_url,
                max_tokens=current.config.max_tokens,
                temperature=current.config.temperature,
                timeout=current.config.timeout,
                extra=current.config.extra,
                requires_api_key=current.config.requires_api_key,
                use_system_tool_call=current.config.use_system_tool_call,
                system_message_format=current.config.system_message_format,
            )
            new_client = create_client(new_cfg)
            entry = ProviderEntry(config=new_cfg, client=new_client, key_pool=None)
            self._client_pool.add_entry(entry, activate=True)

        self._llm = entry.client
        self.cfg.model = entry.config.model
        self.cfg.llm_provider = entry.config.provider
        return entry

    def switch_to_provider_default(
        self, provider: str, model: Optional[str] = None,
    ) -> "ProviderEntry":  # noqa: F821
        """
        运行时切换到指定 provider（供 `/provider switch <name> [model]` 使用）。

        行为：
          1. 若 fallback chain 中已有该 provider 的条目：
             - 给了 model：要求 provider+model 都匹配；
             - 没给 model：使用该 provider 在 chain 中出现的**第一条**（即
               "默认模型"）。
             命中后直接切换 _current_idx，复用已就绪的 client。
          2. 若 fallback chain 中完全没有该 provider：构造一条全新配置
             （model 用调用方传入的值；若也没传，退回当前 model），从标准
             环境变量解析 api_key，创建 client 并作为新条目追加进 chain。

        Returns:
            切换后激活的 ProviderEntry。
        """
        from mini_agent.llm.client_pool import ProviderEntry, _get_env_api_key

        idx = self._client_pool.find_entry_index(provider=provider, model=model)
        if idx is not None:
            entry = self._client_pool.switch_to_index(idx)
        else:
            resolved_model = model or self.cfg.model
            api_key = _get_env_api_key(provider)
            new_cfg = LLMConfig(
                provider=provider,
                model=resolved_model,
                api_key=api_key,
                requires_api_key=(provider not in ("ollama", "local")),
            )
            new_client = create_client(new_cfg)
            entry = ProviderEntry(config=new_cfg, client=new_client, key_pool=None)
            self._client_pool.add_entry(entry, activate=True)

        self._llm = entry.client
        self.cfg.model = entry.config.model
        self.cfg.llm_provider = entry.config.provider
        return entry

    # ── [SYS-ROLE-AGENT] 角色 Agent 触发 ────────────────────────────────────

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
                    )
                else:
                    from mini_agent.role_agents.dispatcher import RoleAgentDispatcher
                    raw = dispatcher._run_custom_role(
                        profile, current_output, original_request
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

        # ── 尝试正常路径：run_turn ───────────────────────────────────────────
        R.print_info("[compact] Generating summary…")
        result = ""
        used_chunked = False
        try:
            result = self.run_turn(compact_prompt)
        except Exception as e:
            from mini_agent.llm.base import LLMContextWindowError
            if isinstance(e, LLMContextWindowError):
                # 历史已超限，run_turn 无法执行 → 切换到分批路径
                R.print_warning(
                    "[compact] History exceeds context limit — switching to chunked compact…"
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
        # （不拆 turn 内部，LLM 调用会自动截断，摘要质量降低但不会崩溃）
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
            llm_messages = to_llm_messages(chunk) + [
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

                    decision = should_trigger_ensemble(user_message, self.cfg)
                    if decision.trigger and get_task_manager() is not None:
                        R.print_info(
                            f"[ensemble] auto-triggered (source={decision.source}): {decision.reason}"
                        )
                        ens_result = run_subagent_ensemble(
                            self.cfg, user_message,
                            strategy=decision.judge_strategy,
                            session_id=getattr(self, "session_id", None),
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
            except Exception:
                pass  # TurnEnd hook 失败不影响主流程

            # [SYS-TURN-JUDGE] TurnEnd hook 没有接管（未配置或未返回替代输入）时，
            # 若开启了 turn_judge，则让 TurnJudgeAgent 核查一次：这到底是真的
            # 需要真人输入，还是主 Agent 遇到了技术性问题，应该自动代替用户反馈
            # 让主 Agent 继续处理。
            if self._turn_end_user_input is None:
                try:
                    self._maybe_run_turn_judge(result)
                except Exception:
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
            return

        from mini_agent.role_agents.turn_judge import run_turn_judge, build_turn_judge_prompt
        from mini_agent.role_agents.feedback import RoleFeedback, format_feedback, extract_turn_status, build_inject_message
        from mini_agent.orchestrator.agent_profiles import AgentProfile

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

        profile = AgentProfile(
            name="turn_judge",
            role_type="turn_judge",
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
        )

        status = extract_turn_status(raw) or "NEED_USER"  # 解析失败时保守按 NEED_USER 处理

        feedback_obj = RoleFeedback(
            role_name="turn_judge",
            role_type="turn_judge",
            raw_output=raw,
            inject_as="user",
            turn_status=status,
        )

        R.console.print()
        R.console.print(format_feedback(feedback_obj))
        R.console.print()

        if status == "NEED_USER":
            self._turn_judge_auto_count = 0
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
                R.print_error(f"[TurnJudge] compact 失败：{e}，回退到等待真人输入。")
                self._turn_judge_auto_count = 0
                return
            auto_msg = "[TurnJudge 自动接管] 历史已压缩，请根据目标继续推进任务。"
        else:  # AUTO_CONTINUE
            auto_msg = raw
            # 尽量提取"反馈"段落作为注入文本，找不到就用完整判定文本兜底
            import re as _re
            m = _re.search(r"\*\*反馈\*\*\s*\n(.+?)(?:\n\nTURN_STATUS|\Z)", raw, _re.DOTALL)
            if m and m.group(1).strip():
                auto_msg = (
                    "[TurnJudge 自动接管] 检测到技术性问题（而非任务真正完成），"
                    "以下是系统代替用户给出的下一步指令：\n\n" + m.group(1).strip()
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

        while loop_count < self.cfg.max_turns:
            loop_count += 1

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
                _budget_pct = _est / max(self.cfg.max_tokens, 1)
                if self.cfg.token_estimate_enabled and self.cfg.verbose:
                    R.print_info(
                        f"[token] ~{_est:,} tokens "
                        f"({_budget_pct:.0%} of {self.cfg.max_tokens:,})"
                    )
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
            _trigger_result = self._compact_triggers.check(_trigger_ctx, self.cfg)
            if _trigger_result.triggered:
                self._maybe_run_compact(_trigger_result)

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

            # [具身改进 C1] AgentSelfModel 快变量更新：把刚 sense() 到的内部状态
            # 同步给 self_model，ContextBuilder.build() 下次调用时会自动读取。
            if self._self_model is not None and self._proprioception is not None:
                try:
                    self._self_model.update_internal_state(_pp_state)
                except Exception:
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
                except Exception:
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
                    except Exception:
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

        self._last_turn_hit_max_turns = loop_count >= self.cfg.max_turns
        if self._last_turn_hit_max_turns:
            R.print_warning(f"Reached max turns ({self.cfg.max_turns}).")

        return final_text

    # ── LLM 调用 ───────────────────────────────────────────────────────────────

    def _call_llm(self) -> LLMResponse:
        """
        调用 LLMClient，根据 cfg.stream 选择流式或非流式。
        通过 LLMClientPool 支持多 key 轮转和多配置故障转移。
        """
        system = self._build_system()
        tools = self._build_tool_schemas()

        import inspect as _inspect
        from mini_agent.llm.system_tool_call import convert_tool_use_to_text
        from mini_agent.history.entry import to_llm_messages
        messages_for_llm = convert_tool_use_to_text(to_llm_messages(self._history))

        # [SYS-PRIVACY] 发送前：屏蔽隐私值
        _guard = self._privacy_guard
        if _guard.active:
            messages_for_llm = _guard.redact_messages(messages_for_llm)
            system = _guard.redact_system(system)

        def _do_single_call(client: LLMClient) -> LLMResponse:
            """单次 LLM 调用（流式/非流式），接受 client 参数供 pool 切换。"""
            _stream_sig = _inspect.signature(client.stream)
            _supports_on_reasoning = "on_reasoning" in _stream_sig.parameters
            _reasoning_started = [False]

            def _on_reasoning(token: str) -> None:
                if not _reasoning_started[0]:
                    R.print_reasoning_header()
                    _reasoning_started[0] = True
                R.print_reasoning(token)

            # [SYS-PRIVACY] 流式打印时，占位符可能被拆成多个 token
            # （如 "{{SECRET_" 和 "1}}" 分两次到达）。
            # 用一个小缓冲区：遇到 "{{" 开头但还没有 "}}" 闭合时暂缓打印，
            # 等完整占位符到齐后 restore 再输出。
            # 注意：_make_guarded_write(w) 在 writer 实例化之后调用，避免前向引用。
            def _make_guarded_write(w: "R.StreamWriter"):
                _ph_buf: list[str] = []

                def _guarded_write(token: str) -> None:
                    if _ph_buf:
                        _ph_buf.append(token)
                        combined = "".join(_ph_buf)
                        if "}}" in combined:
                            _ph_buf.clear()
                            w.write(_guard.restore(combined))
                        elif len(combined) > 40:
                            # 超长未闭合，不是合法占位符，直接输出
                            _ph_buf.clear()
                            w.write(combined)
                    else:
                        if "{{" in token:
                            idx = token.rfind("{{")
                            before, after = token[:idx], token[idx:]
                            if "}}" in after:
                                w.write((before + _guard.restore(after)) if before else _guard.restore(after))
                            else:
                                if before:
                                    w.write(before)
                                _ph_buf.append(after)
                        else:
                            w.write(token)

                return _guarded_write

            try:
                if self.cfg.stream:
                    R.print_assistant_prefix(agent_name=self.cfg.agent_name)
                    writer = R.StreamWriter()
                    _on_token_fn = _make_guarded_write(writer) if _guard.active else writer.write
                    stream_kwargs: dict = dict(
                        messages=messages_for_llm,
                        system=system,
                        tools=tools,
                        on_token=_on_token_fn,
                    )
                    if _supports_on_reasoning:
                        stream_kwargs["on_reasoning"] = _on_reasoning
                    resp = client.stream(**stream_kwargs)
                    if not _reasoning_started[0] and resp.reasoning:
                        R.print_reasoning_header()
                        R.console.print(resp.reasoning, style="dim")
                    if _reasoning_started[0] or resp.reasoning:
                        R.print_reasoning_footer()
                    writer.flush()
                else:
                    resp = client.chat(
                        messages=messages_for_llm,
                        system=system,
                        tools=tools,
                    )
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
                from mini_agent.llm.base import LLMProviderError
                raise LLMProviderError(f"Unexpected LLM error: {e}") from e

            return resp

        def _on_retry(attempt: int, reason: str) -> None:
            if getattr(self.cfg, "llm_retry_verbose", True):
                R.print_warning(
                    f"[retry {attempt}/{self._retry_policy.max_retries}] {reason}"
                )

        def _on_switch_key(old_suffix: str, new_suffix: str, exc: Exception) -> None:
            R.print_warning(
                f"[key-switch] ...{old_suffix} → ...{new_suffix} "
                f"({type(exc).__name__})"
            )

        def _on_switch_config(old_label: str, new_label: str, exc: Exception) -> None:
            R.print_warning(
                f"[llm-fallback] {old_label} → {new_label} "
                f"({type(exc).__name__}: {str(exc)[:80]})"
            )
            self._llm = self._client_pool.current_client

        response = self._client_pool.call_with_pool(
            call_fn=_do_single_call,
            retry_policy=self._retry_policy,
            on_switch_key=_on_switch_key,
            on_switch_config=_on_switch_config,
        )
        self._llm = self._client_pool.current_client

        # [SYS-PRIVACY] 收到回复后：还原占位符 → 真实值
        if _guard.active:
            from dataclasses import replace as _dc_replace
            import json as _json
            if response.text:
                response = _dc_replace(response, text=_guard.restore(response.text))
            if response.tool_calls:
                restored_calls = []
                for tc in response.tool_calls:
                    raw = _json.dumps(tc.input)
                    restored_raw = _guard.restore(raw)
                    if restored_raw != raw:
                        from mini_agent.llm.base import ToolCall as _ToolCall
                        tc = _ToolCall(id=tc.id, name=tc.name, input=_json.loads(restored_raw))
                    restored_calls.append(tc)
                response = _dc_replace(response, tool_calls=restored_calls)

        return response

    # ── History management ─────────────────────────────────────────────────────

    def _append_assistant_response(self, response: LLMResponse) -> None:
        """
        将 LLMResponse 转换为对话历史条目（委托给 HistoryManager）。
        使用 provider 无关的通用格式（Anthropic/OpenAI 均可接受）。
        <skill_used> 标签在此处剥离，不写入历史（避免污染后续对话上下文）。
        """
        self._hist.append_assistant(response)

    # ── Reminder 注入辅助方法 ──────────────────────────────────────────────────

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
        工具调用）之后调用。委托给 perception.format_correction_detector，
        新增检测规则只需改那个模块，这里不需要变动。

        返回 FormatIssue | None。
        """
        from mini_agent.perception.format_correction_detector import detect_format_issue
        return detect_format_issue(assistant_text)

    # ── Tool execution ─────────────────────────────────────────────────────────

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
                _term.print(
                    f"[dim]即将执行历史压缩（原因: {trigger_result.reason} — "
                    f"{trigger_result.message}），是否继续？[/dim]"
                )
                choice = _term.confirm(prompt_lines=[], choices="(y)es  (n)o", default="y")
            except Exception:
                # 非交互环境（如 headless/daemon）下无法弹确认，降级为自动执行
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
        except Exception:
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
                    except Exception:
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
        except Exception:
            pass

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