"""
api/session_pool.py — daemon 多用户架构 Phase 3：每个 Session 一个独立 Agent 实例

核心设计：
  - 每个 (user_id, session_id) 对应一个 SessionEntry
  - SessionEntry 持有独立的 Agent 实例和 AgentBridge（独立 SSE 流、独立输入队列）
  - AgentRunner 线程随 SessionEntry 创建而启动，随 GC/崩溃而停止
  - Self（主自我）不在这个 pool 里管理——Self 复用 HttpServer 已有的
    self._bridge / self._runner，SessionAgentPool 只管"用户对话用的"
    SessionAgent，不管 Self 自己。

稳定性设计：
  - SessionAgent 崩溃 → on_crash 回调（实时）/ _monitor_loop（兜底巡检）
    → 写日志 → 从 pool 移除 → 通知 SelfMessageBus → 不影响其他 session 和 Self
  - idle_timeout 后自动 suspend（保存 + 从内存移除，session 文件仍在磁盘上，
    下次同一个 session_id 再被请求时会重新加载历史）
  - 并发上限 max_sessions，超过时拒绝新建（不影响已有 session）

与设计文档草稿（0.3 / 3.1 节）的关键差异，已在此实现中修正：
  1. **线程模型**：草稿里 `Agent(cfg=session_cfg)` 在调用者线程（HTTP 请求线程）
     构造，`AgentRunner(bridge); runner.start()` 又在调用者线程里再起一个新线程。
     这样 Agent.__init__() 注册的 thread-local provider（project_root/session_id/
     active_skills，见 tools/evolution.py、tools/workdir_knowledge.py、
     tools/orchestration.py）写入的是"调用者线程"，但实际跑 run_turn() 的是
     "AgentRunner 自己的线程"——两者不是同一个，工具读到的永远是 None/空。
     本实现改成：把 Agent() 的构造逻辑包成一个 `agent_factory` 闭包，传给
     AgentRunner（api/server.py 已在 Phase 3 改造里支持这个参数）。
     AgentRunner.run() 的第一件事就是在它自己的线程上调用 agent_factory()，
     构造和运行从此真正在同一条线程上发生。
  2. **`extra_system` 字段不存在**：草稿写的是 `session_cfg.extra_system`，
     `AppConfig` 上根本没有这个字段（死代码，赋值了但没人读）。已改为
     `session_cfg.system_extra`（Phase 2 已经验证过的、真正接入 system prompt
     组装链路的字段）。
  3. **per-user session 目录**：草稿没有处理"session 存哪"的问题，会导致所有
     用户的 session 还是写进同一个全局 `.agent/sessions/`，没有真正的用户级
     隔离。本实现给每个非 owner 用户分配独立的 `session.dir`：
     `<project_root>/.agent/users/<user_id>/sessions/`（owner 的 session 仍然
     走原来的全局目录，保持向后兼容）。这比给 SessionMeta 加 user_id 字段再
     过滤的方案成本更低、隔离性更强（物理上分开，不依赖过滤逻辑写对）。
  4. **MCP 工具注册的并发安全**：tools/__init__.py::ToolRegistry.register() 是
     纯 dict 操作，没有锁。MCPManager.register_all() 把工具注册进**全局共享**的
     _default_registry。如果两个 SessionEntry 几乎同时创建，各自的
     Agent.__init__() 都会调用 register_all()，存在并发写同一个 dict、或重复
     注册同名工具抛 ValueError 的风险。本实现引入一把全局
     `_agent_construction_lock`，`agent_factory()` 内部构造 Agent 时持有这把
     锁——只序列化"构造"这个短暂阶段，不影响 run_turn()（真正耗时的 LLM 调用）
     的并发度。
  5. **SkillLoader 不能跨 session 共享**：SkillLoader 内部的 `_active`（已激活
     skill 列表）是实例级可变状态。如果多个 SessionEntry 共享同一个
     SkillLoader 实例，激活/失活会互相串。本实现给每个 SessionEntry 构造
     独立的 SkillLoader（重新跑一次 _discover() 扫描——这个扫描成本是真实
     存在的已知取舍，记录在设计文档里，不在这里假装它不存在）。
"""

from __future__ import annotations

import copy
import logging
import queue as _queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.api.server import AgentRunner as AgentRunnerType
    from mini_agent.api.user_store import UserContext, RoleProfileManager
    from mini_agent.config.models import AppConfig


log = logging.getLogger("mini_agent.session_pool")


# ── 默认参数 ──────────────────────────────────────────────────────────────────

DEFAULT_IDLE_TIMEOUT  = 30 * 60   # 30 分钟无活动则 suspend
DEFAULT_MAX_SESSIONS  = 20        # 同时活跃的 session 上限
AGENT_READY_TIMEOUT   = 30.0      # 等待新 Agent 构造完成的超时（构造本身很轻，
                                   # 这个超时主要是防御性的，正常情况几百毫秒内完成）

# 见模块 docstring 第 4 点：序列化 Agent() 构造阶段，避免并发构造时
# 共享的全局 ToolRegistry（MCP 工具注册等）出现竞态。
_agent_construction_lock = threading.Lock()


# ── SessionEntry ──────────────────────────────────────────────────────────────

@dataclass
class SessionEntry:
    """
    一个活跃 session 的完整运行时状态。
    持有独立 Agent + AgentBridge + AgentRunner 线程。

    注意：构造时 `bridge.agent` 可能暂时是 None——AgentRunner 用 agent_factory
    模式时，Agent 真正的构造发生在 runner 线程里（见模块 docstring 第 1 点），
    SessionAgentPool.get_or_create() 会等 runner.ready_event 之后才返回，
    所以从 get_or_create() 拿到的 SessionEntry，agent 字段保证已经填好。
    """
    session_id:   str
    user_id:      str
    role:         str
    bridge:       Any                # AgentBridge（独立实例）
    runner:       "AgentRunnerType"  # AgentRunner（独立线程，agent_factory 模式）
    created_at:   float = field(default_factory=time.time)
    last_active:  float = field(default_factory=time.time)
    is_suspended: bool  = False

    @property
    def agent(self) -> Any:
        """便捷访问 bridge.agent（construction 完成后两者始终是同一个对象）。"""
        return self.bridge.agent

    def touch(self) -> None:
        self.last_active = time.time()

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_active

    @property
    def is_alive(self) -> bool:
        return (
            not self.is_suspended
            and self.runner is not None
            and self.runner.is_alive()
        )


# ── SelfMessageBus ────────────────────────────────────────────────────────────
#
# Phase 3 阶段先把发送端做好（session_crashed 等），Phase 4 把消费端接上——
# Self 现在会在 api/server.py::AgentRunner._main_loop() 的每个 idle 周期
# drain 并处理 to_id="self" 的消息（见 AgentRunner._drain_self_messages()）。

class SelfMessage:
    """Self 与 SessionAgent 之间的内部消息。"""
    __slots__ = ("msg_id", "from_id", "to_id", "msg_type", "payload", "ts")

    # msg_type 枚举：
    # "session_summary"  — Session 上报轮次摘要
    # "profile_update"   — Session 上报用户画像增量
    # "approval_req"     — Session 请求权限审批
    # "approval_resp"    — Self 回应审批
    # "context_inject"   — Self 向 Session 注入上下文
    # "session_crashed"  — Pool 通知 Self 某 Session 崩溃
    # "peer_message"      — SessionAgent 横向通信

    def __init__(
        self,
        from_id:  str,
        to_id:    str,
        msg_type: str,
        payload:  dict,
    ) -> None:
        import secrets as _s
        self.msg_id   = _s.token_hex(4)
        self.from_id  = from_id
        self.to_id    = to_id
        self.msg_type = msg_type
        self.payload  = payload
        self.ts       = time.time()


class SelfMessageBus:
    """
    内存消息总线（无持久化）。
    Self 和所有 SessionAgent 共享同一 bus 实例。

    实体 ID 规范：
      "self"               — 主自我（AutonomousLoop）
      "session:<sid>"      — 某个 SessionAgent
      "pool"               — SessionAgentPool（管理消息）
    """

    def __init__(self, maxsize: int = 200) -> None:
        self._queues: dict[str, _queue.Queue] = {}
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def register(self, entity_id: str) -> None:
        with self._lock:
            if entity_id not in self._queues:
                self._queues[entity_id] = _queue.Queue(maxsize=self._maxsize)

    def unregister(self, entity_id: str) -> None:
        with self._lock:
            self._queues.pop(entity_id, None)

    def send(self, msg: SelfMessage) -> bool:
        with self._lock:
            q = self._queues.get(msg.to_id)
        if not q:
            return False
        try:
            q.put_nowait(msg)
            return True
        except _queue.Full:
            return False

    def receive(self, entity_id: str, timeout: float = 0) -> Optional[SelfMessage]:
        with self._lock:
            q = self._queues.get(entity_id)
        if not q:
            return None
        try:
            return q.get(timeout=timeout) if timeout > 0 else q.get_nowait()
        except (_queue.Empty, _queue.Full):
            return None

    def broadcast_to_sessions(self, msg: SelfMessage) -> int:
        """Self 向所有活跃 SessionAgent 广播，返回成功发送数。"""
        with self._lock:
            targets = [k for k in self._queues if k.startswith("session:")]
        sent = 0
        for t in targets:
            m = SelfMessage(
                from_id=msg.from_id,
                to_id=t,
                msg_type=msg.msg_type,
                payload=dict(msg.payload),
            )
            if self.send(m):
                sent += 1
        return sent

    def drain_all(self, entity_id: str) -> list[SelfMessage]:
        """取出该实体队列中所有待处理消息。"""
        msgs = []
        while True:
            m = self.receive(entity_id, timeout=0)
            if m is None:
                break
            msgs.append(m)
        return msgs


# ── SessionAgentPool ──────────────────────────────────────────────────────────

class SessionAgentPool:
    """
    管理所有活跃 SessionAgent 实例。

    使用方式：
        pool = SessionAgentPool(base_cfg, role_profile_mgr, bus, skill_dirs)
        pool.start_monitor()   # 启动健康检查后台线程

        entry = pool.get_or_create(user_ctx, session_id)
        entry.bridge.input_queue.enqueue(message, meta={...})
    """

    def __init__(
        self,
        base_cfg:         "AppConfig",
        role_profile_mgr: Optional["RoleProfileManager"],
        bus:              SelfMessageBus,
        skill_dirs:       Optional[list] = None,
        idle_timeout:     float = DEFAULT_IDLE_TIMEOUT,
        max_sessions:     int   = DEFAULT_MAX_SESSIONS,
    ) -> None:
        """
        Args:
            base_cfg: daemon 启动时的基础配置（来自 CLI/配置文件），每个 session
                深拷贝一份再各自定制（session.dir、system_extra 注入点等）。
                这是"配置模板"，不是"Self 在用的那份 cfg 对象"——两者不共享
                引用，互相修改不会串。
            role_profile_mgr: Phase 2 的 RoleProfileManager，用于把角色画像
                拼进每个 SessionAgent 的 system_extra（construction 时一次性
                注入，不像 Phase 1/2 共享 Agent 模型下需要每个 turn 都重新
                拼接——per-session Agent 的 cfg 从创建到销毁都只服务这一个
                用户，system_extra 在构造时定好就不会再变）。
            skill_dirs: 构造每个 SessionAgent 自己的 SkillLoader 用的目录列表
                （复用 HttpServer 启动时已经算好的那份，不重新从 cfg 推导）。
        """
        self._base_cfg          = base_cfg
        self._role_profile_mgr  = role_profile_mgr
        self._bus               = bus
        self._skill_dirs         = skill_dirs or []
        self._idle_timeout       = idle_timeout
        self._max_sessions       = max_sessions

        self._pool: dict[str, SessionEntry] = {}   # session_id → entry
        self._lock = threading.Lock()
        # [FIX] 见 api/server.py::create_app().lifespan 里的详细说明：
        # 每个新建的 per-session bridge 都必须调用一次
        # bridge.broadcaster.set_loop(...)，否则该 session 的所有 SSE
        # 事件都发不出去（OutputBroadcaster.publish() 在 _loop 为 None 时
        # 直接跳过 call_soon_threadsafe）。这里先存一份 loop 引用，供
        # _create_entry() 在构造新 bridge 时使用；只有在 uvicorn 的 async
        # 上下文里才能拿到真正的事件循环，所以由外部（create_app 的
        # lifespan）在启动时调用 set_loop() 注入进来，不在 __init__ 里直接
        # asyncio.get_event_loop()（此时很可能还没有运行中的循环）。
        self._loop: Optional["asyncio.AbstractEventLoop"] = None
        # 每个 session_id 一把构造锁，只序列化"同一个 session_id"的并发创建请求，
        # 不同 session_id 之间互不阻塞（见 get_or_create 的详细说明：这是为了
        # 修复一个真实存在过的死锁——构造失败回调需要拿 self._lock，
        # 如果调用方在等待构造完成期间一直握着 self._lock 就会互相等待）。
        self._construction_locks: dict[str, threading.Lock] = {}
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()

        # 注册 pool 自身到 bus
        bus.register("pool")

    # ── 核心 API ─────────────────────────────────────────────────────────────

    def get_or_create(
        self,
        user_ctx:   "UserContext",
        session_id: str,
    ) -> SessionEntry:
        """
        获取已有 SessionEntry 或创建新的。
        若 session 处于 suspended/已崩溃状态，重建（新的 Agent + Runner）。

        注意：Agent 的真正构造发生在新起的 AgentRunner 线程内部（见模块
        docstring 第 1 点），本方法会阻塞等待那条线程完成构造
        （AGENT_READY_TIMEOUT 超时保护），但不会等到第一次 run_turn() ——
        那是后续异步发生的事，不卡这次 HTTP 请求。

        关键点（这是实测中发现并修复的一个死锁）：等待构造完成的这段时间
        **不持有 self._lock**。最初的实现里，整个方法体（包括等待
        runner.ready_event 的那一段阻塞）都包在 `with self._lock:` 里，
        而 AgentRunner 构造失败时的 on_crash 回调（运行在 runner 自己的
        线程上）需要 `with self._lock:` 才能把这个 session 从 pool 里摘掉——
        调用方线程握着锁等 ready_event，runner 线程等同一把锁才能让
        ready_event 解除阻塞，互相等对方，死锁。
        现在改成：用一把"每个 session_id 一把"的构造锁（_construction_locks）
        只序列化"同一个 session_id 的并发请求"，不同 session_id 之间完全不
        互相阻塞；self._lock 只在简短的字典读写时短暂持有，从不跨越
        "等待另一个线程完成某件事"这种阻塞操作。
        """
        with self._lock:
            entry = self._pool.get(session_id)
            if entry is not None and entry.is_alive:
                entry.touch()
                return entry
            # entry 不存在，或者存在但 runner 已死（需要重建）：
            # 拿到（或创建）这个 session_id 专属的构造锁，离开 self._lock 的保护。
            con_lock = self._construction_locks.setdefault(session_id, threading.Lock())

        # 注意：从这里开始不再持有 self._lock。多个线程对同一个 session_id
        # 并发调用 get_or_create() 时，会在下面 con_lock.acquire() 这里排队，
        # 但不会阻塞其他 session_id 的创建/获取。
        with con_lock:
            # 双重检查：可能在等锁的这段时间，另一个线程已经把这个 session 建好了
            with self._lock:
                entry = self._pool.get(session_id)
                if entry is not None and entry.is_alive:
                    entry.touch()
                    return entry

                active = sum(1 for e in self._pool.values() if e.is_alive)
                if active >= self._max_sessions:
                    raise RuntimeError(
                        f"Max concurrent sessions ({self._max_sessions}) reached. "
                        "Try again later."
                    )

            # 真正的构造过程（可能阻塞数百毫秒，等 AgentRunner 线程把 Agent 建好）
            # 完全在锁外面进行，不影响其他 session 的并发创建/访问。
            entry = self._create_entry(user_ctx, session_id)

            with self._lock:
                self._pool[session_id] = entry
            return entry

    def set_loop(self, loop: "asyncio.AbstractEventLoop") -> None:
        """由 create_app() 的 lifespan 在启动时注入运行中的事件循环。

        必须在真正拿到新 bridge 之前就调用（daemon 启动早期），这样
        _create_entry() 里对每个新 session 构造的 bridge 才能在构造时
        立刻绑好 loop——否则该 session 早期发出的事件（包括第一轮回复）
        会在 bridge.broadcaster._loop 还是 None 的窗口期内被静默丢弃。
        """
        with self._lock:
            self._loop = loop

    def get(self, session_id: str) -> Optional[SessionEntry]:
        with self._lock:
            return self._pool.get(session_id)

    def find_by_turn(self, turn_id: str) -> Optional[SessionEntry]:
        """根据 turn_id 找到所属 SessionEntry（用于 SSE 路由 / 权限校验）。"""
        with self._lock:
            entries = list(self._pool.values())
        for entry in entries:
            turn = entry.bridge.input_queue.get_turn(turn_id)
            if turn is not None:
                return entry
        return None

    def find_by_permission_req(self, req_id: str) -> Optional[SessionEntry]:
        """
        根据权限请求 req_id 找到所属 SessionEntry。

        和 find_by_turn 同样的理由（见 routes.py::stream_turn 的修复说明）：
        /v1/permissions/{req_id} 不能简单依赖"该用户最近活跃的 session"，
        必须真正定位这个 req_id 是哪个 SessionEntry 的 permission_gate 发出的。
        """
        with self._lock:
            entries = list(self._pool.values())
        for entry in entries:
            gate = getattr(entry.bridge, "permission_gate", None)
            if gate is None:
                continue
            with gate._lock:
                if req_id in gate._pending:
                    return entry
        return None

    def find_by_interaction_req(self, req_id: str) -> Optional[SessionEntry]:
        """
        根据通用交互请求 req_id 找到所属 SessionEntry（ask_user 系列工具 /
        /goal 协商 / 任意 slash 命令内的 prompt_user() 调用）。
        与 find_by_permission_req 同样的理由：不能简单依赖"该用户最近活跃
        的 session"，必须真正定位这个 req_id 是哪个 SessionEntry 的
        interaction_gate 发出的。
        """
        with self._lock:
            entries = list(self._pool.values())
        for entry in entries:
            gate = getattr(entry.bridge, "interaction_gate", None)
            if gate is None:
                continue
            with gate._lock:
                if req_id in gate._pending:
                    return entry
        return None

    def list_entries(self, user_id: Optional[str] = None) -> list[SessionEntry]:
        with self._lock:
            entries = list(self._pool.values())
        if user_id is not None:
            entries = [e for e in entries if e.user_id == user_id]
        return entries

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._pool.values() if e.is_alive)

    def _remove_from_pool(self, session_id: str) -> Optional[SessionEntry]:
        """
        统一的"从 pool 摘除"操作：弹出 entry 的同时清理对应的构造锁，
        避免 _construction_locks 随 session churn 无限增长（守护进程长期运行，
        如果每个用过一次就废弃的 session_id 都永久占着一把 Lock 对象，
        内存会缓慢泄漏）。所有"移除 session"的地方（suspend / on_crash /
        健康巡检 / idle 清理）都应该走这个方法，不要再单独写
        `self._pool.pop(...)`。
        """
        with self._lock:
            entry = self._pool.pop(session_id, None)
            self._construction_locks.pop(session_id, None)
        return entry

    def suspend(self, session_id: str) -> bool:
        """挂起 SessionEntry：保存状态 + 停止 Runner + 从内存移除。"""
        entry = self._remove_from_pool(session_id)
        if not entry:
            return False
        self._do_suspend(entry)
        return True

    # ── 内部：创建 ───────────────────────────────────────────────────────────

    def _build_session_cfg(self, user_ctx: "UserContext") -> "AppConfig":
        """
        派生该用户专属的 cfg（深拷贝，避免各 session 互相污染）。
        见模块 docstring 第 2/3 点：system_extra（不是 extra_system）+
        per-user session 目录（owner 例外，沿用全局目录）。
        """
        session_cfg = copy.deepcopy(self._base_cfg)

        if self._role_profile_mgr is not None:
            user_system_ctx = self._role_profile_mgr.build_system_context(
                user_ctx.user_id, user_ctx.role
            )
            existing = getattr(session_cfg, "system_extra", "") or ""
            session_cfg.system_extra = (existing + "\n\n" + user_system_ctx).strip()

        if user_ctx.user_id != "owner":
            # owner 沿用 session_cfg.session.dir 的默认值（None →
            # SessionManager 内部推导为 <project_root>/.agent/sessions/），
            # 保持向后兼容：开启多用户模式前 owner 已有的历史 session 不受影响。
            session_cfg.session.dir = (
                session_cfg.project_root / ".agent" / "users"
                / user_ctx.user_id / "sessions"
            )

        return session_cfg

    def _make_agent_factory(
        self, user_ctx: "UserContext", session_id: str, session_cfg: "AppConfig"
    ) -> Callable[[], Any]:
        """
        返回一个无参闭包，AgentRunner.run() 会在它自己的线程上调用这个闭包来
        构造 Agent（见模块 docstring 第 1 点）。闭包内部做的事：
          1. 在 _agent_construction_lock 保护下构造 Agent（避免 MCP 工具
             注册等共享全局状态的并发竞态，见模块 docstring 第 4 点）
          2. 尝试恢复已有 session 历史（如果 session_id 对应的历史已存在）
        """
        def _factory():
            from mini_agent.agent import Agent
            from mini_agent.skills import SkillLoader

            # 每个 SessionAgent 独立的 SkillLoader（见模块 docstring 第 5 点：
            # _active 列表是实例级可变状态，不能跨 session 共享）。
            skill_loader = None
            if self._skill_dirs:
                try:
                    skill_loader = SkillLoader(
                        self._skill_dirs,
                        per_skill_tokens=getattr(session_cfg, "skill_compact_per_skill", 5_000),
                        total_budget=getattr(session_cfg, "skill_compact_budget", 25_000),
                    )
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.api.session_pool.SessionAgentPool._make_agent_factory._factory')
                    skill_loader = None  # skill 目录有问题不应该阻断整个 session 创建

            with _agent_construction_lock:
                agent = Agent(cfg=session_cfg, skill_loader=skill_loader)

            # [具身改进 B4] 余裕感知层：session 构造完成后，一次性分析
            # open_threads / capability_map / lesson memory，把结果追加到
            # agent.cfg.system_extra（ContextBuilder 每轮读取的同一对象，
            # 立即对下一轮 system prompt 生效）。只读分析，失败不阻断
            # session 创建。
            self._inject_affordance_map(agent, session_cfg)

            # 尝试恢复已有 session 历史（同一个 session_id 之前已经存在）
            try:
                mgr = agent.session_manager
                if mgr is not None:
                    existing_session = mgr.load(session_id)
                    if existing_session is not None:
                        agent.load_session(session_id)
                    elif agent._session is not None and agent._session.id != session_id:
                        # [FIX] session_id 错位 bug：Agent() 构造时内部调用
                        # SessionManager.new_session() 会随机生成一个全新的
                        # 8 位 session id（不接受指定 id），跟这里 pool 用作
                        # 路由 key 的 session_id（例如 /v1/sessions/new 分配
                        # 的 12 位 id，或客户端记住、后续每次请求都带着的
                        # session_id）完全对不上。
                        #
                        # 后果：pool._pool 字典按 session_id 路由没问题（靠
                        # 的是外层 dict key，不依赖 agent 内部 id），但
                        # agent 自己保存历史时用的是这个不相关的随机 id，
                        # 客户端下次拿着它记住的 session_id 来 resume /
                        # 查询历史时，磁盘上根本没有这个目录——历史查不到、
                        # session 列表里显示的 id 和客户端 UI 上的 id 对不
                        # 上，正是"多 session 场景下第二个客户端好像没收到
                        # 回复"这类问题背后的根因之一。
                        #
                        # 这里是全新 session（尚未 save() 过、file_path 为
                        # 空，不存在覆盖已有磁盘文件的风险），可以安全地把
                        # Agent 内部 session.id 直接改写成 pool 要求的
                        # session_id，并重新绑定 TaskManager / debug logger /
                        # raw_history 等依赖 session_id 的周边组件（复用
                        # load_session/new_session 同款的 _bind_session_extras()）。
                        agent._session.id = session_id
                        agent._bind_session_extras()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.session_pool.SessionAgentPool._make_agent_factory._factory')
                pass  # 恢复失败不阻断创建，agent 会用一个全新的 session

            return agent

        return _factory

    @staticmethod
    def _inject_affordance_map(agent: Any, session_cfg: "AppConfig") -> None:
        """
        [具身改进 B4，本地/daemon 路径统一，见 next_doc/priority_improvements_implementation_plan.md 方案一]
        构建一次 AffordanceMap，拼进 agent 正在使用的 system_extra。

        实际实现已提升为 perception/affordance_analyzer.py::inject_affordance_map()，
        daemon 多用户路径（本方法）与本地单 Agent 路径（cli/app.py）共用同一份
        逻辑，避免两条路径各自维护一份、逐渐分叉。本方法保留只是为了不改动
        既有调用点（self._inject_affordance_map(agent, session_cfg)）。
        """
        from mini_agent.perception.affordance_analyzer import inject_affordance_map
        inject_affordance_map(agent, session_cfg, log=log)

    def _create_entry(
        self,
        user_ctx:   "UserContext",
        session_id: str,
    ) -> SessionEntry:
        from mini_agent.api.bridge import AgentBridge
        from mini_agent.api.server import AgentRunner

        session_cfg = self._build_session_cfg(user_ctx)
        agent_factory = self._make_agent_factory(user_ctx, session_id, session_cfg)

        # ── 独立 Bridge（独立 RingBuffer + InputQueue + SSE 流）────────────
        # [BUGFIX] 之前这里调用 init_bridge()，会顺手把 bridge.py 里那个
        # 进程级全局单例 _bridge_instance 也换成这个新 session 的 bridge，
        # 导致任何"当前正在等待用户回答"的 ask_user/ask_user_confirm 交互
        # （通过 interaction.py::get_bridge() 取"当前 bridge"来登记）会被
        # 后来创建的任意 session 悄悄劫走，答复时报 404 "not found or
        # already handled"（详见 bridge.py::get_bridge() 处的完整说明）。
        # 现在直接构造 AgentBridge，不触碰那个全局单例；这条 session 自己的
        # 线程会在 AgentRunner.run() 里通过 set_thread_bridge() 把这个 bridge
        # 绑定到"这条线程"的 thread-local，同样能保证线程内 get_bridge()
        # 拿到正确的 bridge，但不会影响其它线程。
        bridge = AgentBridge(ring_maxlen=500)
        # 注意：此刻 bridge.agent 还是 None——AgentRunner.run() 会在它自己的
        # 线程里调用 agent_factory() 之后才赋值（见 api/server.py AgentRunner.run）。

        # [FIX] 必须立刻把事件循环绑定给这个新 bridge 的 broadcaster，
        # 否则它后续 emit 的所有事件（token / turn_done / ...）都会因为
        # OutputBroadcaster.publish() 里 `self._loop` 为 None 而被直接
        # 跳过，不会送进任何 SSE 订阅者的队列——这正是"daemon 多客户端场景下，
        # 新建 session 的那个客户端发消息后，daemon 自己的终端能看到
        # AgentRunner 处理完、打印出了回复，但发消息的客户端本身通过
        # /v1/stream/{turn_id} 什么都收不到"的根因。self._loop 由
        # create_app() 的 lifespan 在服务启动时通过 set_loop() 注入
        # （见该处注释），正常情况下这里不会是 None；万一意外是 None
        # （例如极端情况下 session 在 lifespan 跑之前就被创建），也不阻断
        # session 创建，只是退化为"这个 session 的 SSE 事件发不出去"，
        # 不影响 daemon 本地终端和下一次正常请求。
        with self._lock:
            loop = self._loop
        if loop is not None:
            bridge.broadcaster.set_loop(loop)

        bus_id = f"session:{session_id}"

        def _on_crash(exc: BaseException) -> None:
            """AgentRunner 线程整体崩溃（不是单个 turn 出错）时的回调。"""
            log.error(
                "[SessionPool] session=%s user=%s runner crashed: %s",
                session_id, user_ctx.user_id, exc,
            )
            self._remove_from_pool(session_id)
            self._bus.unregister(bus_id)
            self._bus.send(SelfMessage(
                from_id="pool", to_id="self", msg_type="session_crashed",
                payload={
                    "session_id": session_id,
                    "user_id":    user_ctx.user_id,
                    "role":       user_ctx.role,
                    "error":      f"{type(exc).__name__}: {exc}",
                },
            ))

        runner = AgentRunner(
            bridge,
            role_profile_mgr=None,  # Phase 3：system_extra 已在构造时一次性注入好，
                                     # 不需要 AgentRunner 再按 turn 重新拼接
                                     # （那是 Phase 1/2 共享 Agent 模型下的临时做法）。
            agent_factory=agent_factory,
            on_crash=_on_crash,
        )
        runner.name = f"session-runner-{session_id[:8]}"
        runner.start()

        if not runner.ready_event.wait(timeout=AGENT_READY_TIMEOUT):
            # 构造严重超时（不应该发生——Agent() 构造本身很轻，真正慢的是
            # run_turn() 里的 LLM 调用，那是构造完成之后才会发生的事）。
            runner.stop()
            raise RuntimeError(
                f"Timed out waiting for session {session_id!r} agent to initialize "
                f"(>{AGENT_READY_TIMEOUT}s)."
            )
        if runner.init_error is not None:
            raise RuntimeError(
                f"Failed to initialize agent for session {session_id!r}: "
                f"{type(runner.init_error).__name__}: {runner.init_error}"
            ) from runner.init_error

        self._bus.register(bus_id)

        entry = SessionEntry(
            session_id=session_id,
            user_id=user_ctx.user_id,
            role=user_ctx.role,
            bridge=bridge,
            runner=runner,
        )
        return entry

    # ── 内部：挂起/崩溃处理 ──────────────────────────────────────────────────

    def _do_suspend(self, entry: SessionEntry) -> None:
        """保存 session 状态，停止 Runner，从 bus 注销。"""
        # 保存 session
        summary_payload = None
        try:
            if entry.agent is not None and entry.agent.session_manager:
                entry.agent.save_session()
                meta = entry.agent.session_meta
                if meta is not None:
                    summary_payload = {
                        "session_id": entry.session_id,
                        "user_id":    entry.user_id,
                        "role":       entry.role,
                        "title":      meta.title or "",
                        "summary":    meta.summary or "",
                        "turns":      meta.turns,
                        "duration_seconds": time.time() - entry.created_at,
                    }
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.session_pool')
            pass

        # 停止 Runner（先发 stop 信号，再 join 等它真正退出）
        try:
            if entry.runner and entry.runner.is_alive():
                entry.runner.stop()
                entry.runner.join(timeout=5)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.session_pool')
            pass

        # daemon 多用户架构 Phase 4：向 Self 上报这次对话的摘要。
        # 放在 runner 停止*之后*发送（而不是在保存 session 那一步就立刻发），
        # 是为了确保即使 Self 立刻在下个 tick 周期就去查 SessionAgentPool 的状态，
        # 看到的也是"这个 session 已经完全停掉"的一致状态，不会有"摘要已经发了，
        # 但 runner 还在跑"这种过渡期的歧义。
        if summary_payload is not None:
            self._bus.send(SelfMessage(
                from_id=f"session:{entry.session_id}", to_id="self",
                msg_type="session_summary", payload=summary_payload,
            ))

        # 从 bus 注销
        self._bus.unregister(f"session:{entry.session_id}")

    # ── 健康监控 ─────────────────────────────────────────────────────────────

    def start_monitor(self) -> None:
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="session-pool-monitor"
        )
        self._monitor_thread.start()

    def stop_monitor(self) -> None:
        self._stop_monitor.set()

    def _monitor_loop(self) -> None:
        while not self._stop_monitor.is_set():
            self._stop_monitor.wait(15)
            if self._stop_monitor.is_set():
                break
            try:
                self._check_health()
                self._gc_idle()
            except Exception:
                log.exception("[SessionPool] monitor loop iteration failed")

    def _check_health(self) -> None:
        """
        检测崩溃的 SessionAgent。

        注意：大多数崩溃场景已经由 AgentRunner 的 on_crash 回调实时处理
        （见 _create_entry 里的 _on_crash），这里只是兜底——万一 runner 线程
        因为某种没有正确走 on_crash 路径的方式死掉，定期巡检仍然能发现并清理。
        """
        with self._lock:
            dead = [
                entry for entry in self._pool.values()
                if not entry.is_suspended and not entry.runner.is_alive()
            ]

        for entry in dead:
            log.error(
                "[SessionPool] session=%s user=%s runner died (caught by monitor, "
                "not on_crash callback — investigate if this happens often).",
                entry.session_id, entry.user_id,
            )
            try:
                if entry.agent is not None:
                    entry.agent.save_session()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.session_pool')
                pass

            with self._lock:
                self._pool.pop(entry.session_id, None)
                self._construction_locks.pop(entry.session_id, None)
            self._bus.unregister(f"session:{entry.session_id}")

            self._bus.send(SelfMessage(
                from_id="pool", to_id="self", msg_type="session_crashed",
                payload={
                    "session_id": entry.session_id,
                    "user_id":    entry.user_id,
                    "role":       entry.role,
                },
            ))

    def _gc_idle(self) -> None:
        """清理长时间空闲的 SessionEntry（suspend）。"""
        with self._lock:
            idle = [
                entry for entry in self._pool.values()
                if entry.idle_seconds > self._idle_timeout and entry.is_alive
            ]

        for entry in idle:
            log.info(
                "[SessionPool] Suspending idle session=%s (idle %.0fs)",
                entry.session_id, entry.idle_seconds,
            )
            self._remove_from_pool(entry.session_id)
            self._do_suspend(entry)

    def stop_all(self) -> None:
        """daemon 关闭时保存并停止所有 SessionAgent。"""
        self.stop_monitor()
        with self._lock:
            entries = list(self._pool.values())
            self._pool.clear()
        for entry in entries:
            self._do_suspend(entry)
