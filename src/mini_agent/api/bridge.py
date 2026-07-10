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

class _Subscriber:
    """单个 SSE 订阅者的状态：队列 + 该订阅者关心的过滤范围。"""
    __slots__ = ("queue", "turn_id_filter", "session_id_filter")

    def __init__(
        self,
        queue: asyncio.Queue,
        turn_id_filter: Optional[str] = None,
        session_id_filter: Optional[str] = None,
    ) -> None:
        self.queue = queue
        self.turn_id_filter = turn_id_filter
        self.session_id_filter = session_id_filter


class OutputBroadcaster:
    """
    事件广播器：
    1. 写入 RingBuffer（持久化，支持回放）
    2. 向所有在线 SSE 订阅者扇出事件

    线程安全：push() 可从任意线程调用；
    订阅/取消订阅需在 asyncio 事件循环中进行。

    ★ 诊断修复（daemon connected 模式偶发"客户端信息显示不全"）★
    之前的实现里，_broadcast_sync() 把**每一个**事件无差别地塞进**所有**
    订阅者的队列，`/v1/stream/{turn_id}` 这种只关心单个 turn 的订阅者
    也不例外——过滤（_match()）是在 routes.py 的 SSE 生成器里、事件已经
    从队列里取出来之后才做的。

    问题：一个订阅者的队列（默认 maxsize=512）因此实际装的是"全局所有
    session、所有 turn"的事件，而不是它真正关心的那一小部分。当同一
    daemon 下有并发 SubAgent 任务在跑（tool_call/tool_result/info 等
    事件频率很高，参见 orchestrator/task_manager.py），这些跟当前 turn
    完全无关的事件会迅速把一个本该很轻量的单 turn 订阅队列填满。队列
    一旦满了，_broadcast_sync() 的处理方式是**整个丢弃这个订阅者**
    （从 _subs 里移除）——而不是仅仅丢掉不相关的那部分事件——于是这个
    正在显示中的 turn，其后续 token 事件全部收不到广播，表现为客户端
    "内容显示到一半就不完整/后续被截断"。而 daemon 本地终端走的是另一条
    完全不同的路径（不经过这个订阅队列），所以只有"客户端"一侧复现，
    这与最初的问题描述完全吻合。

    修复：
      1. subscribe() 增加 turn_id_filter / session_id_filter 参数，
         在**推送时**就按订阅者关心的范围过滤，不相关的事件根本不会
         进入该订阅者的队列——从根源上避免"无关事件把队列挤爆"。
      2. 队列仍然满了（真正的极端积压）时，不再直接销毁订阅者、让它
         从此再也收不到任何事件：改为丢弃队首（最旧）的一条事件腾出
         空间再重试——保证"最新事件优先送达"，牺牲的是最旧的、大概率
         已经过时的积压事件，而不是让整条流从此死掉。
      3. 每次丢弃都做一次计数 + 告警日志，方便以后再复现时能直接从
         日志里定位"是不是队列积压导致的丢事件"，而不必再靠肉眼比对
         客户端输出去猜。
    """

    def __init__(self, ring: RingBuffer) -> None:
        self._ring = ring
        # asyncio.Queue per subscriber  {sub_id -> _Subscriber}
        self._subs: dict[str, "_Subscriber"] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()
        # 诊断计数器：sub_id -> 因队列积压丢弃的事件数（供 /v1/status 等
        # 诊断端点或日志排查使用，不影响功能）。
        self.dropped_event_counts: dict[str, int] = {}

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

    @staticmethod
    def _matches(event: AgentEvent, turn_id_filter: Optional[str],
                 session_id_filter: Optional[str]) -> bool:
        """
        判断一个事件是否落在某个订阅者关心的范围内。
        与 routes.py 里原来的 _match() 语义保持一致（session_switched
        始终放行，见该函数原有注释），只是把它下沉到推送侧执行。
        """
        if turn_id_filter and event.turn_id and event.turn_id != turn_id_filter:
            return False
        if (
            session_id_filter
            and event.session_id
            and event.session_id != session_id_filter
            and event.type != EventType.SESSION_SWITCHED
        ):
            return False
        return True

    def _broadcast_sync(self, event: AgentEvent) -> None:
        """在 asyncio 线程中运行，把事件塞进每个订阅队列（按各自的过滤范围）。"""
        with self._lock:
            subs = list(self._subs.items())
        for sub_id, sub in subs:
            if not self._matches(event, sub.turn_id_filter, sub.session_id_filter):
                continue
            self._put_with_backpressure(sub_id, sub.queue, event)

    def _put_with_backpressure(self, sub_id: str, q: asyncio.Queue, event: AgentEvent) -> None:
        """
        尝试把事件放进订阅队列；满了就丢弃队首最旧的一条腾出空间再重试，
        而不是销毁整个订阅者——保证这个订阅者至少还能继续收到"更新的"
        事件，不会因为一次积压就彻底失联。
        """
        try:
            q.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        try:
            q.get_nowait()  # 丢弃最旧的一条
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # 极端情况：并发写入把刚腾出的位置又占满了，放弃这一条
        with self._lock:
            self.dropped_event_counts[sub_id] = self.dropped_event_counts.get(sub_id, 0) + 1
        try:
            import logging
            logging.getLogger("mini_agent.api.bridge").warning(
                "SSE subscriber %s queue overflow, dropped oldest event "
                "(turn_id=%s, total_dropped=%d) — 若客户端反馈内容显示不全，"
                "先查这里的丢弃计数是否持续增长",
                sub_id, event.turn_id, self.dropped_event_counts[sub_id],
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.bridge')
            pass

    def subscribe(
        self,
        maxsize: int = 512,
        turn_id_filter: Optional[str] = None,
        session_id_filter: Optional[str] = None,
    ) -> tuple[str, asyncio.Queue]:
        """
        注册一个新订阅者，返回 (sub_id, queue)。

        turn_id_filter / session_id_filter：若提供，推送时就只把匹配的
        事件放进这个订阅者的队列（见类文档的"诊断修复"说明）。不提供则
        保持旧行为——接收全部事件（例如 /v1/stream 全局订阅、观察者线程）。
        """
        sub_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._subs[sub_id] = _Subscriber(
                queue=q, turn_id_filter=turn_id_filter, session_id_filter=session_id_filter,
            )
        return sub_id, q

    def unsubscribe(self, sub_id: str) -> None:
        self._remove_sub(sub_id)

    def _remove_sub(self, sub_id: str) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)
            self.dropped_event_counts.pop(sub_id, None)

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


# ── InteractionGate（通用交互式提问，daemon connected 适配）────────────────────

class _PendingInteraction:
    """一次待回答的通用交互式提问。

    kind: "ask_user" | "ask_user_confirm" | "ask_user_choice" |
          "goal_negotiation" | "repl_prompt"
    data: 展示给用户看的内容（question/hint/options/choices/prompt_text 等，
          由调用方自行填充，客户端按 kind 渲染）。
    answer: 回答结果，统一放在这个 dict 里，字段含义随 kind 变化：
          ask_user            -> {"answer": str}
          ask_user_confirm    -> {"confirmed": bool}
          ask_user_choice     -> {"choice_index": int} 或 {"answer": str}
          goal_negotiation    -> {"answer": str}
          repl_prompt         -> {"answer": str}
    """
    __slots__ = ("req_id", "kind", "data", "turn_id", "event", "answer")

    def __init__(self, req_id: str, kind: str, data: dict, turn_id: str) -> None:
        self.req_id  = req_id
        self.kind    = kind
        self.data    = data
        self.turn_id = turn_id
        self.event   = threading.Event()
        self.answer: dict = {}


class HttpInteractionGate:
    """
    通用交互式提问的 HTTP 侧。用途与 HttpPermissionGate 完全对称：
    ask_user 系列工具、/goal 协商子对话、以及任意 slash 命令内部对
    term.prompt_user()/term.confirm() 的调用，都通过这里把"需要用户
    二次输入"的请求同时广播给 CLI（如果有本地终端）和 HTTP 端
    （daemon connected 的远程客户端），谁先回答就用谁的。
    """

    def __init__(self, broadcaster: OutputBroadcaster) -> None:
        self._broadcaster = broadcaster
        self._pending: dict[str, _PendingInteraction] = {}
        self._lock = threading.Lock()
        self._timeout = 300.0  # 5 分钟无响应视为放弃（比权限审批更久，因为可能涉及长文本思考）
        self._bridge_state_setter = None
        self._session_id_getter = None

    def _sid(self) -> str:
        try:
            return self._session_id_getter() if self._session_id_getter else ""
        except Exception:
            return ""

    def register_pending(
        self,
        req_id: str,
        kind: str,
        data: dict,
        turn_id: str = "",
    ) -> _PendingInteraction:
        """注册一个待回答项并广播 SSE 事件，返回 pending 对象（有 .event/.answer 属性）。"""
        pending = _PendingInteraction(req_id, kind, data, turn_id)
        with self._lock:
            self._pending[req_id] = pending

        self._broadcaster.push(AgentEvent(
            type=EventType.INTERACTION_REQ,
            turn_id=turn_id,
            session_id=self._sid(),
            data={"req_id": req_id, "kind": kind, **data},
        ))
        return pending

    def cancel_pending(self, req_id: str) -> None:
        """取消一个待回答项（一端已先决定时调用，唤醒另一端的等待）。"""
        with self._lock:
            pending = self._pending.pop(req_id, None)
        if pending is not None:
            pending.event.set()

    def broadcast_done(self, req_id: str, answer: dict, reason: str, turn_id: str = "") -> None:
        """广播回答结果给所有 SSE 客户端。幂等：同一 req_id 只广播一次。"""
        with self._lock:
            if not hasattr(self, "_broadcast_done_ids"):
                self._broadcast_done_ids: set = set()
            if req_id in self._broadcast_done_ids:
                return
            self._broadcast_done_ids.add(req_id)

        self._broadcaster.push(AgentEvent(
            type=EventType.INTERACTION_DONE,
            turn_id=turn_id,
            session_id=self._sid(),
            data={"req_id": req_id, "reason": reason, **answer},
        ))
        if self._bridge_state_setter is not None:
            self._bridge_state_setter("running")

    def respond(self, req_id: str, answer: dict) -> bool:
        """HTTP 端调用，唤醒阻塞的等待方，并从 pending 列表移除。"""
        with self._lock:
            pending = self._pending.pop(req_id, None)
        if pending is None:
            return False
        pending.answer = answer
        pending.event.set()
        return True

    def list_pending(self) -> list[dict]:
        with self._lock:
            return [
                {"req_id": p.req_id, "kind": p.kind, "data": p.data, "turn_id": p.turn_id}
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
        self.interaction_gate = HttpInteractionGate(self.broadcaster)

        # 注入状态更新回调：权限审批完成时 gate 可以直接更新 bridge 状态
        self.permission_gate._bridge_state_setter = self._set_state_from_gate
        self.interaction_gate._bridge_state_setter = self._set_state_from_gate
        # 注入 session_id 获取回调：权限/交互相关事件也要打上当前 session 标签，
        # 否则 /v1/stream?session_id=xxx 按 session 过滤时会漏掉这些事件。
        self.permission_gate._session_id_getter = self._current_session_id
        self.interaction_gate._session_id_getter = self._current_session_id

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

    def emit_agent_prefix(self, agent_name: str, turn_id: str = "") -> None:
        # [SYS-AGENT-PREFIX] 见 models.py::EventType.AGENT_PREFIX 的说明。
        self.broadcaster.push(AgentEvent(
            type=EventType.AGENT_PREFIX,
            turn_id=turn_id,
            session_id=self._current_session_id(),
            data={"agent_name": agent_name},
        ))

    def emit_reasoning(
        self, turn_id: str = "", text: Optional[str] = None, marker: Optional[str] = None,
    ) -> None:
        """
        推送思维链（CoT）相关事件——之前完全没有对应的 emit，导致
        print_reasoning()/print_reasoning_header()/print_reasoning_footer()
        产生的"── Reasoning ──"整块内容在 daemon 本地终端可见、connected
        客户端却完全看不到（连头尾分隔线都没有），是本次排查里发现的另一
        处"两端显示不一样"。

        text   非空：一个流式 reasoning token（对应 print_reasoning(token)）。
        marker 非空："start" 或 "end"，对应 print_reasoning_header()/
               print_reasoning_footer()，让客户端能画出同样的分隔线，
               而不只是把 reasoning 文本和正文混在一起。
        """
        data: dict = {}
        if text is not None:
            data["text"] = text
        if marker is not None:
            data["marker"] = marker
        self.broadcaster.push(AgentEvent(
            type=EventType.REASONING,
            turn_id=turn_id,
            session_id=self._current_session_id(),
            data=data,
        ))

    def emit_skill_loaded(self, name: str, turn_id: str = "") -> None:
        """
        推送 skill 激活通知（对应 print_skill_loaded()）。同样是之前
        完全没有转发的一类事件——daemon 本地终端能看到"📚 Skill loaded: xxx"，
        connected 客户端看不到，容易让人误以为两端行为不一致（其实只是
        没转发，agent 侧逻辑本身没有差异）。
        """
        self.broadcaster.push(AgentEvent(
            type=EventType.SKILL_LOADED,
            turn_id=turn_id,
            session_id=self._current_session_id(),
            data={"skill_name": name},
        ))

    def emit_tool_call(self, name: str, inp: dict, turn_id: str = "", verbose: bool = False) -> None:
        self.broadcaster.push(AgentEvent(
            type=EventType.TOOL_CALL,
            turn_id=turn_id,
            session_id=self._current_session_id(),
            # verbose：透传本地终端当时是否处于"展示完整工具入参 JSON"模式
            # （tool_executor.py 调用 print_tool_call() 时传的
            # verbose=self.cfg.verbose）。之前这里没有转发这个字段，导致
            # daemon 本地终端能看到 tool_input 的 JSON 代码块（.agent 目录
            # 排查等场景很依赖这个），而 connected 客户端 _render_sse_event()
            # 根本无从判断要不要展示同样的内容——表现为"同一次 read_file
            # 调用，daemon 端能看到 path/start_line/end_line 参数，客户端
            # 只有一行摘要"。加上这个字段，客户端才能做出和本地一致的判断。
            data={"tool_name": name, "tool_input": inp, "verbose": verbose},
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

    def emit_command_output(self, line: str, turn_id: str = "") -> None:
        """推送 slash 命令（/evolve /skills /stats 等）执行期间产生的一行输出。

        见 models.py::EventType.COMMAND_OUTPUT 的说明：run_captured() 期间
        逐行实时转发，取代"只在结束时把整段捕获文本塞进 turn_done.text"
        的旧行为——旧行为在 connected 客户端上要么完全看不到（历史 bug），
        要么和 info/warning 等类型化事件重复显示一遍。
        """
        self.broadcaster.push(AgentEvent(
            type=EventType.COMMAND_OUTPUT,
            turn_id=turn_id,
            session_id=self._current_session_id(),
            data={"line": line},
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