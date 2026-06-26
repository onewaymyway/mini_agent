"""
api/session_pool.py — 每个 Session 一个独立 Agent 实例

核心设计：
  - 每个 (user_id, session_id) 对对应一个 SessionEntry
  - SessionEntry 持有独立的 Agent 实例和 AgentBridge（独立 SSE 流、独立输入队列）
  - AgentRunner 线程随 SessionEntry 创建而启动，随 GC 时停止
  - Self（主自我）的 bridge 和 runner 独立于 pool，是 daemon 的「骨干」

稳定性设计：
  - SessionAgent 崩溃 → _monitor_loop 检测 → 写错误日志 → 从 pool 移除
    → 通知 SelfMessageBus → 不影响其他 session 和 Self
  - ResourceArbiter 控制并发 session 数和每个 session 的 token 预算
  - idle_timeout 后自动 suspend（写文件 + 从内存移除）
"""

from __future__ import annotations

import copy
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from mini_agent.api.bridge import AgentBridge
    from mini_agent.api.user_store import UserContext


# ── 默认参数 ──────────────────────────────────────────────────────────────────

DEFAULT_IDLE_TIMEOUT = 30 * 60    # 30 分钟无活动则 suspend
DEFAULT_MAX_SESSIONS = 20         # 同时活跃的 session 上限


# ── SessionEntry ──────────────────────────────────────────────────────────────

@dataclass
class SessionEntry:
    """
    一个活跃 session 的完整运行时状态。
    持有独立 Agent + AgentBridge + AgentRunner 线程。
    """
    session_id:   str
    user_id:      str
    role:         str
    agent:        Any               # mini_agent.agent.Agent
    bridge:       Any               # AgentBridge（独立实例）
    runner:       Any               # AgentRunner（独立线程）
    created_at:   float = field(default_factory=time.time)
    last_active:  float = field(default_factory=time.time)
    is_suspended: bool  = False

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
    # "peer_message"     — SessionAgent 横向通信

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


import queue as _queue

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
        pool = SessionAgentPool(self_cfg, self_paths, bus)
        pool.start_monitor()   # 启动健康检查后台线程

        entry = pool.get_or_create(user_ctx, session_id, profile_manager)
        entry.bridge.input_queue.enqueue(message)
    """

    def __init__(
        self,
        self_cfg:    Any,           # AppConfig
        self_paths:  Any,           # AgentPaths
        bus:         SelfMessageBus,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        max_sessions: int   = DEFAULT_MAX_SESSIONS,
    ) -> None:
        self._cfg          = self_cfg
        self._paths        = self_paths
        self._bus          = bus
        self._idle_timeout = idle_timeout
        self._max_sessions = max_sessions

        self._pool: dict[str, SessionEntry] = {}   # session_id → entry
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None

        # 注册 pool 自身到 bus
        bus.register("pool")

    # ── 核心 API ─────────────────────────────────────────────────────────────

    def get_or_create(
        self,
        user_ctx:        "UserContext",
        session_id:      str,
        profile_manager: Any,          # UserProfileManager
    ) -> SessionEntry:
        """
        获取已有 SessionEntry 或创建新的。
        若 session 处于 suspended 状态，先恢复（重建 Agent + Runner）。
        """
        with self._lock:
            entry = self._pool.get(session_id)
            if entry is not None:
                if entry.is_alive:
                    entry.touch()
                    return entry
                # entry 存在但 runner 已死，重建
                self._pool.pop(session_id, None)

            # 检查并发上限
            active = sum(1 for e in self._pool.values() if e.is_alive)
            if active >= self._max_sessions:
                raise RuntimeError(
                    f"Max concurrent sessions ({self._max_sessions}) reached. "
                    "Try again later."
                )

            entry = self._create_entry(user_ctx, session_id, profile_manager)
            self._pool[session_id] = entry
            return entry

    def get(self, session_id: str) -> Optional[SessionEntry]:
        with self._lock:
            return self._pool.get(session_id)

    def find_by_turn(self, turn_id: str) -> Optional[SessionEntry]:
        """根据 turn_id 找到所属 SessionEntry（用于 SSE 路由）。"""
        with self._lock:
            for entry in self._pool.values():
                turn = entry.bridge.input_queue.get_turn(turn_id)
                if turn is not None:
                    return entry
        return None

    def list_entries(self) -> list[SessionEntry]:
        with self._lock:
            return list(self._pool.values())

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._pool.values() if e.is_alive)

    def suspend(self, session_id: str) -> bool:
        """挂起 SessionEntry：保存状态 + 停止 Runner + 从内存移除。"""
        with self._lock:
            entry = self._pool.pop(session_id, None)
        if not entry:
            return False
        self._do_suspend(entry)
        return True

    # ── 内部：创建 ───────────────────────────────────────────────────────────

    def _create_entry(
        self,
        user_ctx:        "UserContext",
        session_id:      str,
        profile_manager: Any,
    ) -> SessionEntry:
        from mini_agent.agent import Agent
        from mini_agent.api.bridge import AgentBridge, init_bridge
        from mini_agent.api.server import AgentRunner

        # ── 派生 cfg（深拷贝，避免各 session 互相污染）────────────────────
        session_cfg = copy.deepcopy(self._cfg)

        # 注入用户上下文到 system prompt
        user_system_ctx = profile_manager.build_system_context(
            user_ctx.user_id, user_ctx.role
        )
        existing = getattr(session_cfg, "extra_system", "") or ""
        session_cfg.extra_system = (existing + "\n\n" + user_system_ctx).strip()

        # ── 构建 Agent ────────────────────────────────────────────────────
        agent = Agent(cfg=session_cfg)

        # 尝试恢复已有 session 历史
        try:
            mgr = agent.session_manager
            if mgr:
                existing_session = mgr.load(session_id)
                if existing_session:
                    agent.load_session(session_id)
        except Exception:
            pass

        # ── 独立 Bridge（独立 RingBuffer + InputQueue + SSE 流）────────────
        bridge = init_bridge(ring_maxlen=500)
        bridge.agent = agent

        # ── 独立 AgentRunner 线程 ────────────────────────────────────────
        runner = AgentRunner(bridge)
        runner.name = f"session-runner-{session_id[:8]}"
        runner.start()

        # 注册到 SelfMessageBus
        bus_id = f"session:{session_id}"
        self._bus.register(bus_id)

        entry = SessionEntry(
            session_id=session_id,
            user_id=user_ctx.user_id,
            role=user_ctx.role,
            agent=agent,
            bridge=bridge,
            runner=runner,
        )
        return entry

    # ── 内部：挂起/崩溃处理 ──────────────────────────────────────────────────

    def _do_suspend(self, entry: SessionEntry) -> None:
        """保存 session 状态，停止 Runner，从 bus 注销。"""
        # 保存 session
        try:
            if entry.agent and entry.agent.session_manager:
                entry.agent.save_session()
        except Exception:
            pass

        # 停止 Runner
        try:
            if entry.runner and entry.runner.is_alive():
                entry.runner.stop()
                entry.runner.join(timeout=5)
        except Exception:
            pass

        # 从 bus 注销
        self._bus.unregister(f"session:{entry.session_id}")

    # ── 健康监控 ─────────────────────────────────────────────────────────────

    def start_monitor(self) -> None:
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="session-pool-monitor"
        )
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        import logging
        log = logging.getLogger("session_pool.monitor")

        while True:
            time.sleep(15)
            try:
                self._check_health(log)
                self._gc_idle(log)
            except Exception:
                pass

    def _check_health(self, log) -> None:
        """检测崩溃的 SessionAgent，通知 Self。"""
        with self._lock:
            dead = [
                entry for entry in self._pool.values()
                if not entry.is_suspended and not entry.runner.is_alive()
            ]

        for entry in dead:
            log.error(
                f"[SessionPool] session={entry.session_id} "
                f"user={entry.user_id} runner died, removing."
            )
            # 紧急保存
            try:
                entry.agent.save_session()
            except Exception:
                pass

            with self._lock:
                self._pool.pop(entry.session_id, None)
            self._bus.unregister(f"session:{entry.session_id}")

            # 通知 Self
            self._bus.send(SelfMessage(
                from_id="pool",
                to_id="self",
                msg_type="session_crashed",
                payload={
                    "session_id": entry.session_id,
                    "user_id":    entry.user_id,
                    "role":       entry.role,
                },
            ))

    def _gc_idle(self, log) -> None:
        """清理长时间空闲的 SessionEntry（suspend）。"""
        with self._lock:
            idle = [
                entry for entry in self._pool.values()
                if entry.idle_seconds > self._idle_timeout and entry.is_alive
            ]

        for entry in idle:
            log.info(
                f"[SessionPool] Suspending idle session={entry.session_id} "
                f"(idle {entry.idle_seconds:.0f}s)"
            )
            with self._lock:
                self._pool.pop(entry.session_id, None)
            self._do_suspend(entry)

    def stop_all(self) -> None:
        """daemon 关闭时保存并停止所有 SessionAgent。"""
        with self._lock:
            entries = list(self._pool.values())
            self._pool.clear()
        for entry in entries:
            self._do_suspend(entry)
