"""
api/bridge.py — Agent 核心与 HTTP 层之间的解耦桥梁

组件：
  RingBuffer          — 环形事件缓冲区，支持迟接入客户端回放历史
  OutputBroadcaster   — 将事件写入 RingBuffer 并扇出给所有 SSE 订阅者
  InputQueue          — 命令队列，HTTP 端 enqueue，AgentRunner 消费
  PermissionGate      — 工具调用权限审批，支持终端 & HTTP 双路
  AgentBridge         — 以上组件的统一入口，单例

设计原则：
  - Agent 核心（agent.py）无需感知 HTTP 存在
  - 所有通信都是线程安全的
  - SSE 客户端断线重连可通过 Last-Event-ID 续接
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from typing import Any, AsyncIterator, Optional

from .models import AgentEvent, EventType, TurnInfo


# ── RingBuffer ────────────────────────────────────────────────────────────────

class RingBuffer:
    """
    线程安全的环形事件缓冲区。
    写入端：任意线程调用 push()
    读取端：SSE handler 调用 events_since() 做历史回放
    """

    def __init__(self, maxlen: int = 2000) -> None:
        self._buf: deque[AgentEvent] = deque(maxlen=maxlen)
        self._counter: int = 0
        self._lock = threading.Lock()

    def push(self, event: AgentEvent) -> AgentEvent:
        """赋予自增 id 并写入缓冲区，返回带 id 的事件。"""
        with self._lock:
            self._counter += 1
            event = event.model_copy(update={"id": self._counter})
            self._buf.append(event)
        return event

    def events_since(self, since_id: int = 0) -> list[AgentEvent]:
        """返回 id > since_id 的所有事件（用于回放历史）。"""
        with self._lock:
            return [e for e in self._buf if e.id > since_id]

    def events_since_ts(self, since_ts: float) -> list[AgentEvent]:
        """返回 ts >= since_ts 的所有事件。"""
        with self._lock:
            return [e for e in self._buf if e.ts >= since_ts]

    def all_events(self) -> list[AgentEvent]:
        with self._lock:
            return list(self._buf)

    @property
    def latest_id(self) -> int:
        with self._lock:
            return self._counter


# ── OutputBroadcaster ─────────────────────────────────────────────────────────

class OutputBroadcaster:
    """
    事件广播器：
    1. 写入 RingBuffer（持久化，支持回放）
    2. 向所有在线 SSE 订阅者扇出事件

    线程安全：push() 可从任意线程调用；
    订阅/取消订阅需在 asyncio 事件循环中进行。
    """

    def __init__(self, ring: RingBuffer) -> None:
        self._ring = ring
        # asyncio.Queue per subscriber  {sub_id -> asyncio.Queue}
        self._subs: dict[str, asyncio.Queue] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定 asyncio 事件循环（server 启动时调用）。"""
        self._loop = loop

    def push(self, event: AgentEvent) -> AgentEvent:
        """
        写入 RingBuffer 并广播给所有 SSE 订阅者。
        可从任意线程调用（内部用 call_soon_threadsafe 跨越线程边界）。
        """
        event = self._ring.push(event)
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._broadcast_sync, event)
        return event

    def _broadcast_sync(self, event: AgentEvent) -> None:
        """在 asyncio 线程中运行，把事件塞进每个订阅队列。"""
        with self._lock:
            subs = list(self._subs.items())
        dead = []
        for sub_id, q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(sub_id)
        for sub_id in dead:
            self._remove_sub(sub_id)

    def subscribe(self, maxsize: int = 512) -> tuple[str, asyncio.Queue]:
        """注册一个新订阅者，返回 (sub_id, queue)。"""
        sub_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._subs[sub_id] = q
        return sub_id, q

    def unsubscribe(self, sub_id: str) -> None:
        self._remove_sub(sub_id)

    def _remove_sub(self, sub_id: str) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


# ── InputQueue ────────────────────────────────────────────────────────────────

class _TurnCommand:
    # Stage 9 §7.1: 新增 initiator 字段，区分"谁发起的"
    __slots__ = ("turn_id", "message", "submitted_at", "initiator", "meta")

    def __init__(
        self,
        turn_id: str,
        message: str,
        initiator: str = "user",
        meta: Optional[dict] = None,
    ) -> None:
        self.turn_id = turn_id
        self.message = message
        self.submitted_at = time.time()
        self.initiator = initiator          # "user" | "scheduled" | "autonomous"
        self.meta: dict = meta or {}


class InputQueue:
    """
    命令队列：HTTP 端 enqueue()，AgentRunner 阻塞 dequeue()。
    同时维护 TurnInfo 字典，记录每轮状态。
    """

    def __init__(self) -> None:
        self._q: deque[_TurnCommand] = deque()
        self._cv = threading.Condition()
        self._turns: dict[str, TurnInfo] = {}
        self._lock = threading.Lock()

    def enqueue(
        self,
        message: str,
        turn_id: Optional[str] = None,
        initiator: str = "user",
        meta: Optional[dict] = None,
    ) -> str:
        """
        投入一条命令，返回 turn_id。
        Stage 9 §7.1: 新增 initiator 参数（"user"|"scheduled"|"autonomous"），
        默认 "user"，现有所有调用点无需修改（向后兼容）。
        """
        tid = turn_id or str(uuid.uuid4())
        cmd = _TurnCommand(tid, message, initiator=initiator, meta=meta or {})
        info = TurnInfo(
            turn_id=tid,
            input=message,
            state="queued",
            started_at=time.time(),
            initiator=initiator,
        )
        with self._lock:
            self._turns[tid] = info
        with self._cv:
            self._q.append(cmd)
            self._cv.notify_all()
        return tid

    def dequeue(self, timeout: float = 1.0) -> Optional[_TurnCommand]:
        """阻塞等待下一条命令，timeout 后返回 None。"""
        with self._cv:
            if not self._q:
                self._cv.wait(timeout=timeout)
            if self._q:
                cmd = self._q.popleft()
                self._update_turn(cmd.turn_id, state="running",
                                  started_at=time.time())
                return cmd
        return None

    def mark_done(self, turn_id: str) -> None:
        self._update_turn(turn_id, state="done", ended_at=time.time())

    def mark_error(self, turn_id: str) -> None:
        self._update_turn(turn_id, state="error", ended_at=time.time())

    def mark_interrupted(self, turn_id: str) -> None:
        self._update_turn(turn_id, state="interrupted", ended_at=time.time())

    def _update_turn(self, turn_id: str, **kwargs: Any) -> None:
        with self._lock:
            if turn_id in self._turns:
                info = self._turns[turn_id]
                for k, v in kwargs.items():
                    setattr(info, k, v)

    def get_turn(self, turn_id: str) -> Optional[TurnInfo]:
        with self._lock:
            return self._turns.get(turn_id)

    def list_turns(self) -> list[TurnInfo]:
        with self._lock:
            return list(self._turns.values())

    @property
    def depth(self) -> int:
        return len(self._q)


# ── PermissionGate ────────────────────────────────────────────────────────────

class _PendingPermission:
    """一次待审批的工具调用。"""
    __slots__ = ("req_id", "tool_name", "tool_input", "turn_id",
                 "event", "approved", "edited_input")

    def __init__(self, req_id: str, tool_name: str,
                 tool_input: dict, turn_id: str) -> None:
        self.req_id      = req_id
        self.tool_name   = tool_name
        self.tool_input  = tool_input
        self.turn_id     = turn_id
        self.event       = threading.Event()
        self.approved    = False
        self.edited_input: Optional[dict] = None


class HttpPermissionGate:
    """
    工具调用权限审批的 HTTP 侧。
    当 HTTP 服务启动时，PermissionGuard 会注册此对象作为回调。
    """

    def __init__(self, broadcaster: OutputBroadcaster) -> None:
        self._broadcaster = broadcaster
        self._pending: dict[str, _PendingPermission] = {}
        self._lock = threading.Lock()
        self._timeout = 120.0   # 2 分钟无响应视为拒绝
        self._bridge_state_setter = None  # 可选回调：broadcast_done 后更新 bridge 状态
        # 可选回调：返回当前激活的 session_id，由 AgentBridge 注入，
        # 用于给这里推送的 permission_req/permission_done 事件打上
        # session_id 标签，让 /v1/stream 能按 session 过滤。
        self._session_id_getter = None

    def _sid(self) -> str:
        try:
            return self._session_id_getter() if self._session_id_getter else ""
        except Exception:
            return ""

    def request(
        self,
        tool_name: str,
        tool_input: dict,
        turn_id: str = "",
    ) -> tuple[bool, Optional[dict]]:
        """
        阻塞当前线程，通过 SSE 推送权限请求给 HTTP 客户端，
        等待回调 approve() / deny() 后返回 (approved, edited_input)。
        """
        req_id = str(uuid.uuid4())
        pending = _PendingPermission(req_id, tool_name, tool_input, turn_id)

        with self._lock:
            self._pending[req_id] = pending

        # 广播权限请求事件
        self._broadcaster.push(AgentEvent(
            type=EventType.PERMISSION_REQ,
            turn_id=turn_id,
            session_id=self._sid(),
            data={
                "req_id":     req_id,
                "tool_name":  tool_name,
                "tool_input": tool_input,
            },
        ))

        # 阻塞等待 HTTP 客户端回应
        responded = pending.event.wait(timeout=self._timeout)
        with self._lock:
            self._pending.pop(req_id, None)

        if not responded:
            # 超时 → 拒绝
            self._broadcaster.push(AgentEvent(
                type=EventType.PERMISSION_DONE,
                turn_id=turn_id,
                session_id=self._sid(),
                data={"req_id": req_id, "approved": False, "reason": "timeout"},
            ))
            return False, None

        self._broadcaster.push(AgentEvent(
            type=EventType.PERMISSION_DONE,
            turn_id=turn_id,
            session_id=self._sid(),
            data={
                "req_id":   req_id,
                "approved": pending.approved,
                "reason":   "user",
            },
        ))
        return pending.approved, pending.edited_input

    def register_pending(
        self,
        req_id: str,
        tool_name: str,
        tool_input: dict,
        turn_id: str = "",
    ) -> Any:
        """
        注册一个待审批项并广播 SSE 事件，返回内部 pending 对象（有 .event 属性）。
        用于双路审批：CLI 与 HTTP 端同时可以响应，由调用方管理 event.wait()。
        """
        pending = _PendingPermission(req_id, tool_name, tool_input, turn_id)
        with self._lock:
            self._pending[req_id] = pending

        self._broadcaster.push(AgentEvent(
            type=EventType.PERMISSION_REQ,
            turn_id=turn_id,
            session_id=self._sid(),
            data={
                "req_id":     req_id,
                "tool_name":  tool_name,
                "tool_input": tool_input,
            },
        ))
        return pending

    def cancel_pending(self, req_id: str) -> None:
        """取消一个待审批项（CLI 端已先决定时调用，唤醒 HTTP 监听线程）。"""
        with self._lock:
            pending = self._pending.pop(req_id, None)
        if pending is not None:
            pending.event.set()   # 让任何正在 wait 的线程退出

    def broadcast_done(
        self,
        req_id: str,
        approved: bool,
        reason: str,
        turn_id: str = "",
    ) -> None:
        """广播权限审批结果事件给所有 SSE 客户端，并将 bridge 状态更新为 running。
        幂等：同一 req_id 只广播一次，防止 CLI 路径和 HTTP 路径双重触发。
        """
        with self._lock:
            if not hasattr(self, "_broadcast_done_ids"):
                self._broadcast_done_ids: set = set()
            if req_id in self._broadcast_done_ids:
                return
            self._broadcast_done_ids.add(req_id)

        self._broadcaster.push(AgentEvent(
            type=EventType.PERMISSION_DONE,
            turn_id=turn_id,
            session_id=self._sid(),
            data={"req_id": req_id, "approved": approved, "reason": reason},
        ))
        # 修复：权限决定后立即把 bridge 状态从 waiting_permission 改回 running，
        # 这样 /v1/status 轮询就能返回正确状态，Web 端权限面板才会消失
        if self._bridge_state_setter is not None:
            self._bridge_state_setter("running")

    def respond(
        self,
        req_id: str,
        approved: bool,
        edited_input: Optional[dict] = None,
    ) -> bool:
        """HTTP 端调用，唤醒阻塞的 request()，并从 pending 列表移除。"""
        with self._lock:
            pending = self._pending.pop(req_id, None)  # 修复：立即从pending移除，list_pending不再返回已处理条目
        if pending is None:
            return False
        pending.approved     = approved
        pending.edited_input = edited_input
        pending.event.set()
        return True

    def list_pending(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "req_id":     p.req_id,
                    "tool_name":  p.tool_name,
                    "tool_input": p.tool_input,
                    "turn_id":    p.turn_id,
                }
                for p in self._pending.values()
            ]


# ── AgentBridge（单例入口）────────────────────────────────────────────────────

class AgentBridge:
    """
    HTTP 层与 Agent 核心之间的统一桥梁。
    在 app.py 中初始化，注入到 FastAPI app.state。
    """

    def __init__(self, ring_maxlen: int = 2000) -> None:
        self.ring        = RingBuffer(maxlen=ring_maxlen)
        self.broadcaster = OutputBroadcaster(self.ring)
        self.input_queue = InputQueue()
        self.permission_gate = HttpPermissionGate(self.broadcaster)

        # 注入状态更新回调：权限审批完成时 gate 可以直接更新 bridge 状态
        self.permission_gate._bridge_state_setter = self._set_state_from_gate
        # 注入 session_id 获取回调：权限相关事件也要打上当前 session 标签，
        # 否则 /v1/stream?session_id=xxx 按 session 过滤时会漏掉审批事件。
        self.permission_gate._session_id_getter = self._current_session_id

        # 当前运行状态
        self._state:        str = "idle"   # "idle" | "running" | "waiting_permission"
        self._current_turn: Optional[str] = None
        self._interrupt_flag = threading.Event()
        self._state_lock = threading.Lock()

        # 注入后由外部赋值
        self.agent: Any = None   # mini_agent.agent.Agent

    def _current_session_id(self) -> str:
        """当前 agent 激活的 session_id（单用户模式下唯一有意义的来源）。
        取不到时返回空字符串，此时事件不带 session 标签，/v1/stream 的
        session 过滤会把它当成"不属于任何具体 session 的系统级事件"，
        照样透传给所有订阅者，不会因为取不到 session_id 就丢事件。
        """
        try:
            return getattr(self.agent, "session_id", "") or ""
        except Exception:
            return ""

    def _set_state_from_gate(self, new_state: str) -> None:
        """由 permission_gate 在权限决定后回调，将状态从 waiting_permission 改回 running。"""
        with self._state_lock:
            if self._state == "waiting_permission":
                self._state = new_state

    # ── 状态管理 ──────────────────────────────────────────────────────────

    def set_state(self, state: str, turn_id: Optional[str] = None) -> None:
        with self._state_lock:
            self._state = state
            if turn_id is not None:
                self._current_turn = turn_id

    def get_state(self) -> dict:
        with self._state_lock:
            return {
                "state":    self._state,
                "turn_id":  self._current_turn,
                "queue_depth": self.input_queue.depth,
                "subscribers": self.broadcaster.subscriber_count,
            }

    # ── 中断 ──────────────────────────────────────────────────────────────

    def request_interrupt(self) -> None:
        self._interrupt_flag.set()

    def consume_interrupt(self) -> bool:
        """检查并消费中断标志（AgentRunner 每轮开始时检查）。"""
        if self._interrupt_flag.is_set():
            self._interrupt_flag.clear()
            return True
        return False

    # ── 便捷推送方法 ──────────────────────────────────────────────────────

    def emit(self, event: AgentEvent) -> AgentEvent:
        # 兜底：调用方直接构造 AgentEvent 时如果没显式填 session_id，
        # 在这里自动补上当前激活 session，避免漏打标签导致 /v1/stream
        # 按 session 过滤时把这条事件过滤掉。
        if not event.session_id:
            event = event.model_copy(update={"session_id": self._current_session_id()})
        return self.broadcaster.push(event)

    def emit_token(self, token: str, turn_id: str = "") -> None:
        self.broadcaster.push(AgentEvent(
            type=EventType.TOKEN,
            turn_id=turn_id,
            session_id=self._current_session_id(),
            data={"text": token},
        ))

    def emit_tool_call(self, name: str, inp: dict, turn_id: str = "") -> None:
        self.broadcaster.push(AgentEvent(
            type=EventType.TOOL_CALL,
            turn_id=turn_id,
            session_id=self._current_session_id(),
            data={"tool_name": name, "tool_input": inp},
        ))

    def emit_tool_result(self, name: str, result: str, turn_id: str = "") -> None:
        self.broadcaster.push(AgentEvent(
            type=EventType.TOOL_RESULT,
            turn_id=turn_id,
            session_id=self._current_session_id(),
            data={"tool_name": name, "result": result},
        ))

    def emit_turn_start(self, turn_id: str, message: str, user_id: str = "") -> None:
        self.broadcaster.push(AgentEvent(
            type=EventType.TURN_START,
            turn_id=turn_id,
            user_id=user_id,
            session_id=self._current_session_id(),
            data={"message": message},
        ))

    def emit_turn_done(
        self, turn_id: str, text: str = "", meta: Optional[dict] = None, user_id: str = ""
    ) -> None:
        data = {"text": text}
        if meta:
            data.update(meta)
        self.broadcaster.push(AgentEvent(
            type=EventType.TURN_DONE,
            turn_id=turn_id,
            user_id=user_id,
            session_id=self._current_session_id(),
            data=data,
        ))

    def emit_error(self, msg: str, turn_id: str = "", user_id: str = "") -> None:
        self.broadcaster.push(AgentEvent(
            type=EventType.ERROR,
            turn_id=turn_id,
            user_id=user_id,
            session_id=self._current_session_id(),
            data={"message": msg},
        ))

    def emit_info(self, msg: str) -> None:
        self.broadcaster.push(AgentEvent(
            type=EventType.INFO,
            session_id=self._current_session_id(),
            data={"message": msg},
        ))

    def emit_fs_change(self, action: str, path: str) -> None:
        self.broadcaster.push(AgentEvent(
            type=EventType.FS_CHANGE,
            session_id=self._current_session_id(),
            data={"action": action, "path": path},
        ))

    def emit_session_switched(self, session_id: str, title: str = "") -> None:
        # 注意：这条事件本身要打上"切换到的目标 session_id"，而不是
        # self._current_session_id()（此时 agent.session_id 通常已经等于
        # session_id 了，二者一致；显式传参更清楚地表达意图，也不依赖
        # 调用时机）。这样客户端按 session_id 过滤订阅时，session_switched
        # 事件会正确出现在"新 session"的流里，而不是旧 session 的流里。
        self.broadcaster.push(AgentEvent(
            type=EventType.SESSION_SWITCHED,
            session_id=session_id,
            data={"session_id": session_id, "title": title},
        ))

    def emit_objective_progress(
        self,
        execution_id: str,
        objective_id: str,
        title: str,
        status: str,
        progress: str,
        current_step: str = "",
    ) -> None:
        """推送 Objective 执行进度事件（daemon 自主执行时）。"""
        self.broadcaster.push(AgentEvent(
            type=EventType.OBJECTIVE_PROGRESS,
            session_id=self._current_session_id(),
            data={
                "execution_id": execution_id,
                "objective_id": objective_id,
                "title": title,
                "status": status,
                "progress": progress,
                "current_step": current_step,
            },
        ))


# ── 模块级单例 ────────────────────────────────────────────────────────────────

_bridge_instance: Optional[AgentBridge] = None


def get_bridge() -> AgentBridge:
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = AgentBridge()
    return _bridge_instance


def init_bridge(ring_maxlen: int = 2000) -> AgentBridge:
    global _bridge_instance
    _bridge_instance = AgentBridge(ring_maxlen=ring_maxlen)
    return _bridge_instance