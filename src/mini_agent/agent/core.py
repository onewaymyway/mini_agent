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

本文件（core.py）只保留 Agent 类的骨架与 __init__：具体职责方法按分组
拆分到同目录下的各 Mixin 文件中（lifecycle / reflection / profile /
llm_control / turn_loop / role_judge / reminders_correction / compaction /
snapshot），Agent 通过多重继承把它们组装回同一个类，对外行为、方法签名、
导入路径完全不变。
"""

from __future__ import annotations

import copy
import re as _re
import threading
from typing import Optional

from mini_agent.config import AppConfig, SessionStats, build_system_prompt
from mini_agent.llm import (
    LLMClient, LLMConfig, LLMResponse, ToolSchema,
    LLMError,
)
import mini_agent.agent as _agent_pkg
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

from mini_agent.agent.lifecycle import SessionLifecycleMixin
from mini_agent.agent.reflection import ReflectionMixin
from mini_agent.agent.profile import ProfileMixin
from mini_agent.agent.llm_control import LLMControlMixin
from mini_agent.agent.turn_loop import TurnLoopMixin
from mini_agent.agent.role_judge import RoleJudgeMixin
from mini_agent.agent.reminders_correction import RemindersCorrectionMixin
from mini_agent.agent.compaction import CompactionMixin
from mini_agent.agent.snapshot import SnapshotMixin


class Agent(
    SessionLifecycleMixin,
    ReflectionMixin,
    ProfileMixin,
    LLMControlMixin,
    TurnLoopMixin,
    RoleJudgeMixin,
    RemindersCorrectionMixin,
    CompactionMixin,
    SnapshotMixin,
):
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
        # 卡住检测 + compact 恢复（与 goal_mode 的同名机制共享同一套实现，
        # 见 role_agents/stuck_detector.py::StuckDetector）：内部记录上一轮
        # assistant_output 用于相似度比较、连续雷同计数、已用掉的"卡住恢复"
        # 额度。真正交还真人输入时随 _turn_judge_auto_count 一起 reset()。
        # 具体阈值（similarity_threshold / consecutive_limit / max_recoveries）
        # 由 _maybe_run_turn_judge 按 cfg.turn_judge 的配置动态设置。
        from mini_agent.role_agents.stuck_detector import StuckDetector
        self._turn_judge_stuck_detector: StuckDetector = StuckDetector(consecutive_limit=0)

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
                f"[dim]\\[privacy] active — registered secrets:\n"
                f"{self._privacy_guard.summary()}[/dim]"
            )

        # LLMClient 可从外部注入（便于测试），否则从 AppConfig 自动创建
        self._llm: LLMClient = llm_client or _agent_pkg.create_client(
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
        # [断网感知] 默认开启（RetryPolicy.network_aware 默认 True），这里
        # 只在 cfg 显式配置时覆盖默认值，不加任何配置也能正常工作。
        self._retry_policy.network_aware = getattr(cfg, "llm_network_aware", True)
        self._retry_policy.network_check_interval = getattr(cfg, "llm_network_check_interval", 5.0)
        self._retry_policy.network_max_wait = getattr(cfg, "llm_network_max_wait", 0.0)

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
                register_skill_resource_tools,
            )
            register_skill_tools(self.registry, self.skill_loader)
            register_compact_tool(self.registry, self)          # 需要 agent 实例
            register_skill_stats_tool(self.registry, self.skill_loader)
            # [渐进式加载] 子资源级 skill_resource_list/load/unload，让 agent 能
            # 自主判断是否需要加载某个子文档，而不是只靠关键词自动触发
            register_skill_resource_tools(self.registry, self.skill_loader)

            # [Phase E / 3.3] 注册"当前激活 skill 列表"provider，供 spawn_agent /
            # spawn_named_agent 工具读取，写入新建 Task 的 active_skills 字段，
            # 使 SubAgent 启动时能继承主 agent 当前激活的 skill（设计文档第 5 节）。
            from mini_agent.tools.orchestration import set_active_skills_provider
            set_active_skills_provider(lambda: self.skill_loader.active)

        # ── 代理池管理工具 ────────────────────────────────────────────────────
        # 让 agent 自己也能查看/触发代理池刷新、管理订阅源、控制"agent 自身请求是否
        # 走代理"的开关（llm_use_proxy / web_search_use_proxy 等），而不是只能靠人
        # 在 CLI（scripts/proxy_ctl.py）或 REPL（/proxy）里手动操作。开关默认全部关闭，
        # 工具本身不会替用户打开；agent 调用 proxy_integration_set 时必须说明 reason，
        # 便于事后审计（见 tools/proxy_manager.py 顶部注释）。
        from mini_agent.tools.proxy_manager import register_proxy_tools
        from mini_agent.storage.paths import AgentPaths as _AgentPaths
        # 注意：self.registry 可能是跨 Agent 实例共享的全局默认 registry
        # （get_default_registry()）。/goal 等模式会在同一进程内创建多个 Agent
        # 实例，若不加判断会导致 proxy_status 等工具重复注册并抛出
        # "already registered" 的 ValueError。这里做幂等判断，跳过重复注册。
        if "proxy_status" not in self.registry.names:
            register_proxy_tools(self.registry, _AgentPaths(cfg.project_root))

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
        # [判官接线统一 阶段六] RoleAgentDispatcher 此前未接入热重载：
        # agent profiles 的 rediscover 只刷新 AgentProfileLoader 自己的
        # _all，不会联动刷新 dispatcher 内部的 _output_roles/_tool_roles/
        # _goal_review_roles/_turn_end_review_roles 四张注册表，导致磁盘上
        # 新增/修改的 .agent/agents/goal_judge.md 等自定义 profile 文件在
        # 运行时不会被 dispatcher 感知（需要重启进程）。这里补上：监视同一批
        # 目录，变化时额外调用一次 dispatcher.rediscover()。
        from mini_agent.role_agents import get_dispatcher
        _rad = get_dispatcher()
        if _rad is not None and _apl is not None:
            self._hot_reloader.register(
                dirs=_apl._dirs,
                reload_fn=_rad.rediscover,
                category="role_agent",
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
            from mini_agent.perception.memory_factory import (
                create_both_memory_backends,
                set_llm_classify_call,
                build_llm_call,
            )
            self._memory, self._global_memory = create_both_memory_backends(cfg)

            # [SYS-LIBRARY-INDEX] 图书馆式索引的分类兜底（规则未命中时）复用
            # Agent 当前正在用的 LLMClient，不单独接一个新 provider。
            # 复用 self._client_pool.current_client 而不是固定住某个 client 引用，
            # 是因为 client_pool 支持故障转移/切换模型（见 switch_model 等方法），
            # 这里每次分类调用时都会取当时的 current_client，天然跟随切换。
            if getattr(cfg.memory, "library_index_enabled", True):
                _pool = self._client_pool
                _llm_call = lambda prompt: build_llm_call(_pool.current_client)(prompt)
                if self._memory is not None:
                    set_llm_classify_call(self._memory, _llm_call)
                if self._global_memory is not None:
                    set_llm_classify_call(self._global_memory, _llm_call)

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

        # [决策/取舍知识提炼计划 5.4 节，路径 C] recall_decisions 只读工具：
        # 注入 AgentPaths + 复用 client_pool 的 llm_call（跟随 switch_model 切换，
        # 不固定住某个 client 引用，与上面 library_index 分类兜底 llm_call 同一模式）。
        # 只读、免审批，注册与否由 CompressConfig.decision_recall_tool_enabled 控制。
        if getattr(cfg.compress, "decision_recall_tool_enabled", True):
            from mini_agent.storage.paths import AgentPaths as _DecisionAgentPaths
            from mini_agent.tools.builtin import configure_decision_recall
            from mini_agent.perception.memory_factory import build_llm_call as _build_llm_call_dr
            _dr_pool = self._client_pool
            _dr_llm_call = lambda prompt: _build_llm_call_dr(_dr_pool.current_client)(prompt)
            configure_decision_recall(_DecisionAgentPaths(cfg.project_root), llm_call=_dr_llm_call)

        # [SYS-SUMMARY] 防止多个摘要/记忆生成任务并发运行（互斥，非阻塞获取）
        self._summary_lock = threading.Lock()
        # [compact_mechanism_improvement_plan P2-A] 压缩质量事后自检可能运行在
        # 后台线程里（audit_async=True 时），保护对 self._history / raw history
        # 的并发追加（追加一条 compact_supplement 条目）。
        self._compact_audit_lock = threading.Lock()

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

