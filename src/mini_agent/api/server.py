"""
api/server.py — FastAPI app 工厂 + AgentRunner 后台线程

AgentRunner：
  独立线程，循环从 InputQueue 取命令 → 调用 agent.run_turn()
  → 通过 OutputBroadcaster 广播事件（token / tool_call / 等）

FastAPI app：
  挂载 AuthMiddleware + routes
  启动时绑定 asyncio 事件循环给 OutputBroadcaster
"""

from __future__ import annotations

import asyncio
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

# ── 本地原生打印抑制开关 ──────────────────────────────────────────────────────
# [daemon 前台 attach-console 专用] 默认 False：_install_output_hook() 打的
# 补丁在广播事件给 SSE 的同时，也会照常调用 renderer.py 原本的
# print_xxx()，让 daemon 进程自己的终端能看到实时输出——这对"detach 后台
# 进程，输出只写到 daemon.log"和"没有额外 attach 机制"的场景是必须的。
#
# 但如果 daemon 前台进程另外起了一个"attach 自己"的 connected 客户端
# （cli/daemon.py::run_connected_repl，通过 loopback HTTP 订阅 SSE 并渲染），
# 两边都会打印同一份内容——一份来自这里的原生 print_xxx()，一份来自
# run_connected_repl 的 observer 渲染——表现为终端上每条消息都出现两次。
# 更麻烦的是，两边还会对同一个 permission_req/interaction_req 抢着做本地
# 交互式应答，导致输入错位、"看似回答了又被判定已被其他端处理"这类怪状态。
#
# 开启此开关后，这里的 hook 只广播、不再自己 print，daemon 前台的显示
# 完全交给 attach 上来的那个 run_connected_repl 负责——和任何其他 connect
# 上来的外部客户端使用同一套渲染 + 输入协调机制，不会有重复或抢占。
_SUPPRESS_NATIVE_PRINT = False


def set_suppress_native_print(flag: bool) -> None:
    global _SUPPRESS_NATIVE_PRINT
    _SUPPRESS_NATIVE_PRINT = bool(flag)


def is_suppress_native_print() -> bool:
    return _SUPPRESS_NATIVE_PRINT

from typing import Optional, Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import AuthMiddleware, load_or_generate_token, print_token_banner
from .bridge import AgentBridge, init_bridge
from .fs_helper import FsHelper
from .models import EventType, AgentEvent
from .multi_auth import MultiUserAuthMiddleware
from .routes import router
from .user_store import UserStore, RoleProfileManager
from .._version import get_version


# ── 终端安全打印（可从任意线程调用）─────────────────────────────────────────

# ── daemon 多 session：output hook 的 bridge 路由 + 本地终端写锁 ────────────
#
# [FIX] _install_output_hook() 只在进程启动时对全局唯一的那个 bridge
# （self._bridge）调用一次，Monkey-patch 的是 mini_agent.ui.renderer 模块级
# 函数/类（print_assistant_prefix / print_markdown / StreamWriter / …）——
# 这些补丁是进程唯一的一份，天生绑定死了当时闭包捕获的那个 bridge 对象。
#
# 但 SessionAgentPool 给每个 session 都各自起一条独立的 AgentRunner 线程、
# 配一个独立的 AgentBridge（session_pool.py::_create_entry）。这些
# per-session 线程执行 agent.run_turn() 时，底层 renderer.StreamWriter()/
# print_assistant_prefix() 等仍然会调用同一份补丁——补丁里写死的
# `bridge.emit_xxx(...)` 永远发去那个当初安装补丁时捕获的全局 bridge，
# 不会发到这个 session 自己的 bridge 上。表现出来就是：
#   - agent_prefix（"orzooo ❯" 名字前缀）事件从来发不到正确的 session
#     bridge，daemon 本地终端和所有 connected 客户端都看不到名字；
#   - _tid() 读的是全局 bridge.agent._http_turn_id，不是这个 session 自己
#     正在跑的 turn_id，导致转发出去的事件 turn_id 对不上。
#
# 用线程局部变量记录"当前这条线程正在为哪个 session 的 bridge 工作"，
# AgentRunner._main_loop() 在开始处理一条消息时设置、finally 里清掉
# （见下面 _main_loop 的改动）。output hook 里所有原本直接使用闭包
# `bridge` 的地方，改成"线程局部变量里有就用它，没有（比如非 daemon/
# 非 pool 场景，只有一个全局 bridge 在跑）就退回闭包捕获的那个"——
# 单 session 场景行为完全不变，多 session 场景才会生效。
_current_session_bridge_tls = threading.local()


def _effective_output_bridge(fallback_bridge: "AgentBridge") -> "AgentBridge":
    return getattr(_current_session_bridge_tls, "bridge", None) or fallback_bridge


# [FIX] daemon 本地终端（mini_agent.ui.terminal.term）是进程内唯一的物理
# 终端单例，被所有 session 的 AgentRunner 线程共享。output hook 里的
# `_orig_print_xxx(...)` / StreamWriter.write() 里的 `term.stream_token(...)`
# 都是直接、无锁地往这同一个终端对象写——多个 session 同时有 turn 在跑时，
# 谁的 token/print 先到就先写一点，两边内容会在字符粒度上拼到一起，
# 表现为终端显示内容错位、缺字、乱序（"daemon 进程显示紊乱"）。
# 这里加一把可重入锁，让 output hook 里"真正往本地物理终端写"的那一小段
# 临界区互斥——只序列化本地终端的落笔顺序，不影响 agent 本身的并发执行
# （工具调用、LLM 请求等仍然完全并发，只有"打印这一下"是互斥的）。
_local_term_write_lock = threading.RLock()


def _print_to_term(markup: str) -> None:
    """
    从后台线程安全地向终端队列投递一条 print 消息。
    使用 Rich markup，与 renderer 输出风格一致。
    出错时静默忽略，不影响 agent 执行。
    """
    try:
        from mini_agent.ui.terminal import term as _term
        with _local_term_write_lock:
            _term.print(markup)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.server')
        pass


def _inject_permission_state_hook(bridge: AgentBridge, agent: Any) -> None:
    """
    向 agent 的 PermissionGuard 注入状态钩子：
    - 进入权限等待时：bridge._state = "waiting_permission"
    - 权限决定后（已由 broadcast_done 回调处理）：bridge._state = "running"

    PermissionGuard._prompt_with_http 调用 http_gate.register_pending()，
    后者通过 broadcaster 推送 permission_req 事件，但 bridge._state 没有变化。
    这里 patch PermissionGuard.check()，在其进入等待前设置状态。
    """
    try:
        # [BUGFIX] Agent 类（agent.py）里 PermissionGuard 实例的真实属性名是
        # `guard`（`self.guard = guard or PermissionGuard(...)`），下面这几个
        # 名字（permission_guard / _permission_guard / permissions）全都不
        # 存在，导致这里永远匹配不到，guard 恒为 None、hook 直接 return，
        # bridge._state 永远不会被设成 "waiting_permission"。
        # 后果：/v1/status 永远看不到 waiting_permission，web/看板前端如果
        # 依赖这个状态字段来判断"要不要显示权限审批面板"，就会表现为
        # ——权限请求实际已经通过 SSE 广播出去了，但看板完全没反应、
        # 也没法操作。这里把真正的属性名 `guard` 放在最前面，同时保留旧的
        # 候选名字做兼容（万一将来又换了别的属性名）。
        guard = getattr(agent, "guard", None)
        if guard is None:
            # 尝试其他常见属性名（兼容旧版本/自定义 Agent 子类）
            guard = getattr(agent, "permission_guard", None) or \
                    getattr(agent, "_permission_guard", None) or \
                    getattr(agent, "permissions", None)
        if guard is None:
            return

        # 避免重复 patch
        if getattr(guard, "_bridge_hooked", False):
            return
        guard._bridge_hooked = True

        _orig_check = guard.check

        def _hooked_check(tool_name: str, tool_input: dict) -> bool:
            # 在调用原始 check 前后设置 bridge 状态
            # check() 内部如果需要用户审批，会阻塞线程
            # 我们需要在阻塞前设置 waiting_permission，阻塞结束后改回 running
            from mini_agent.permissions import _SAFE_TOOLS, _RISKY_TOOLS
            needs_prompt = (
                not guard.auto_approve
                and tool_name not in _SAFE_TOOLS
                and tool_name not in guard._denied_tools
                and not guard._is_allowed(tool_name, tool_input)
                # sandbox 拦截 / 工具自己声明 requires_approval=False 时，
                # check() 根本不会走到审批分支，这里也不该误标 waiting_permission
                and not (guard.sandbox and tool_name in _RISKY_TOOLS)
            )
            if needs_prompt and tool_name not in _RISKY_TOOLS:
                try:
                    from mini_agent.tools import get_default_registry
                    td = get_default_registry().get(tool_name)
                    if td is not None and not td.requires_approval:
                        needs_prompt = False
                except Exception:
                    pass
            if needs_prompt:
                bridge.set_state("waiting_permission")

            try:
                result = _orig_check(tool_name, tool_input)
            finally:
                # 权限决定后恢复 running（broadcast_done 回调也会做，此处作为兜底）
                with bridge._state_lock:
                    if bridge._state == "waiting_permission":
                        bridge._state = "running"

            return result

        guard.check = _hooked_check

    except Exception as e:
        _print_to_term(f"[yellow]⚠ permission hook failed: {e}[/yellow]")


# ── AgentRunner ───────────────────────────────────────────────────────────────

class AgentRunner(threading.Thread):
    """
    后台线程：循环消费 InputQueue，驱动 agent.run_turn()。
    run_turn() 的所有输出（流式 token、工具调用等）通过
    OutputHook 拦截后广播到 HTTP 客户端。

    Stage 9 §7.2: 新增 AutonomousLoop 集成。
    dequeue(timeout=0.5) 超时返回 None（没有新用户消息）时，
    检查距上次 tick 是否已过 tick_interval，是则调用 autonomous_loop.tick()。
    这样"检查用户消息"和"自主周期任务"共享同一个循环，互相知道对方状态。
    """

    def __init__(
        self,
        bridge: AgentBridge,
        autonomous_loop=None,  # Optional[AutonomousLoop]，不强制依赖
        role_profile_mgr=None,  # daemon 多用户架构 Phase 2: Optional[RoleProfileManager]
        agent_factory=None,    # daemon 多用户架构 Phase 3: Optional[Callable[[], Agent]]
        on_ready=None,         # daemon 多用户架构 Phase 3: Optional[Callable[[Agent], None]]
        on_crash=None,         # daemon 多用户架构 Phase 3: Optional[Callable[[BaseException], None]]
        self_message_bus=None,  # daemon 多用户架构 Phase 4: Optional[SelfMessageBus]，
                                 # 非 None 时这条 AgentRunner 被视为"Self"，会在每次
                                 # idle 周期顺带 drain 并处理 to_id="self" 的消息。
    ) -> None:
        super().__init__(name="agent-runner", daemon=True)
        self._bridge = bridge
        # 命名说明：这里特意不叫 self._stop —— threading.Thread 自己有一个私有方法
        # 也叫 _stop()（线程真正结束后内部清理用，见 Thread._wait_for_tstate_lock）。
        # 之前这里用 self._stop = threading.Event() 会把父类的 _stop() 方法直接盖掉，
        # 只要从来没人对这个线程调用过 .join()，这个问题完全不会暴露（Phase 1/2
        # 确实从来没调用过 .join()）。Phase 3 的 SessionAgentPool._do_suspend()
        # 需要真正 join() 等线程退出，一调用就会因为 self._stop 变成 Event 而不是
        # 方法直接报 "Event object is not callable" 崩掉。改名为 _stop_evt 避免遮蔽。
        self._stop_evt   = threading.Event()
        self._autonomous_loop = autonomous_loop  # Stage 9: AutonomousLoop 实例
        self._role_profile_mgr = role_profile_mgr
        # daemon 多用户架构 Phase 2：记录"原本配置好的" system_extra（来自 --system
        # CLI 参数 / 配置文件，在 Agent 构造时就定好，不会再变）。每个 turn 临时往后拼接
        # 角色上下文时，都以这个值为基底重新拼，而不是在上一轮拼接结果上继续累加——
        # 否则角色上下文会无限累积，且上一个用户的角色提示会泄漏给下一个用户。
        # 用 None 而不是直接读 bridge.agent.cfg.system_extra，因为此时 bridge.agent
        # 可能还没设置好（HttpServer.__init__ 里 AgentRunner 在 agent 赋值之后才构造，
        # 但稳妥起见仍做一次懒加载）。
        self._base_system_extra: Optional[str] = None

        # daemon 多用户架构 Phase 4：Self ↔ SessionAgent 通信。
        self._self_message_bus = self_message_bus
        if self_message_bus is not None:
            self_message_bus.register("self")

        # daemon 多用户架构 Phase 3：agent_factory 是这次新增的核心机制。
        #
        # 背景（见设计文档 0.3 节）：tools/evolution.py、tools/workdir_knowledge.py、
        # tools/orchestration.py 里的"当前 Agent 上下文"都是用 threading.local()
        # 实现的，在 Agent.__init__() 里写入"调用 Agent() 那个线程"的 thread-local，
        # 工具函数运行时读取的是"工具函数实际运行所在线程"的 thread-local。
        # 这两者必须是同一个线程，否则工具读到的永远是 None/空。
        #
        # Phase 1/2（agent_factory=None）：bridge.agent 由外部预先构造好并赋值
        # （HttpServer.__init__ 里 `self._bridge.agent = agent`），这是历史行为，
        # 不受此次改动影响——单 Agent 模式下 Agent() 只构造一次，构造它的线程
        # 是主线程，run_turn() 永远在 AgentRunner 线程里跑，两者不一致这个问题
        # 本来就已经存在（设计文档已经记录），Phase 3 不强求回头修这个历史包袱，
        # 只保证新增的 per-session 路径是对的。
        #
        # Phase 3（agent_factory 非 None）：run() 的第一件事就是在**这条线程自己**
        # 上调用 agent_factory() 构造 Agent，赋给 bridge.agent，然后才进入主循环。
        # 这样 Agent.__init__() 里注册的所有 thread-local provider，写入的就是
        # 这条 AgentRunner 线程——和之后每次 run_turn()（同样在这条线程上跑）
        # 读取的是同一个线程，工具调用时能读到正确的 project_root/session_id。
        self._agent_factory = agent_factory
        self._on_ready = on_ready    # Agent 构造成功后回调（传入 agent 实例）
        self._on_crash = on_crash    # run() 内出现未捕获异常时回调（传入异常对象）
        self.ready_event = threading.Event()  # agent_factory 模式下，构造完成后 set()
        self.init_error: Optional[BaseException] = None

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        bridge = self._bridge

        # daemon 多用户架构 Phase 3：见 __init__ 里的详细说明——agent_factory
        # 非 None 时，必须在这里（这条线程自己）构造 Agent，不能让调用方在
        # 它自己的线程上提前构造好再传进来。
        if self._agent_factory is not None:
            try:
                agent = self._agent_factory()
                bridge.agent = agent
                if self._on_ready is not None:
                    try:
                        self._on_ready(agent)
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.api.server')
                        pass
            except Exception as e:
                self.init_error = e
                if self._on_crash is not None:
                    try:
                        self._on_crash(e)
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.api.server')
                        pass
                return  # 构造失败，这条线程直接结束，不进入主循环（finally 仍会 set ready_event）
            finally:
                # 无论成功还是失败都要 set：调用方（SessionAgentPool.get_or_create）
                # 是用 ready_event.wait(timeout=...) 等构造完成的，失败也得让它解除阻塞，
                # 否则会一直等到 timeout 才发现出错（self.init_error 才是判断真正依据）。
                self.ready_event.set()
        else:
            # Phase 1/2 路径（单 Agent 模式）：bridge.agent 应该已经由外部设置好。
            self.ready_event.set()

        iq = bridge.input_queue

        try:
            self._main_loop(bridge, iq)
        except Exception as e:
            # daemon 多用户架构 Phase 3：主循环内未被下面 try/except 捕获的异常
            # （理论上不应该发生，下面每个 turn 都有自己的 try/except）。
            # 兜底捕获，通知 on_crash，让 SessionAgentPool 能检测到并清理，
            # 不影响其他 SessionAgent 或 Self。
            if self._on_crash is not None:
                try:
                    self._on_crash(e)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.api.server')
                    pass

    def _main_loop(self, bridge: AgentBridge, iq) -> None:

        while not self._stop_evt.is_set():
            # 检查中断标志（在 idle 状态下也消耗掉，避免积压）
            bridge.consume_interrupt()

            cmd = iq.dequeue(timeout=0.5)
            if cmd is None:
                # Stage 9 §7.2: 没有用户消息时，检查是否应该 tick AutonomousLoop
                if (self._autonomous_loop is not None
                        and self._autonomous_loop.should_tick()):
                    try:
                        self._autonomous_loop.tick()
                    except Exception:
                        pass  # tick 异常不影响主循环

                # daemon 多用户架构 Phase 4：每个 idle 周期都顺带处理一下 Self
                # 收到的消息，不像 AutonomousLoop.tick() 那样受 tick_interval
                # 限制——session_crashed 这类通知应该尽快被看到，不应该等到
                # 下一个 tick 周期（默认 60 秒）才处理。
                if self._self_message_bus is not None:
                    self._drain_self_messages()

                # [事件总线接入] 即时层事件同样"每个 idle 周期顺带查一下"，
                # 与上面 _drain_self_messages() 同一节奏——都是"不应该等到
                # 60 秒 tick 周期才被看到"的信号，但走的是 events.jsonl（跨
                # session/跨进程持久化），不是 SelfMessageBus 的内存队列
                # （SelfMessageBus 是"实体间点对点通信"，这里是"状态变化广播"，
                # 语义不同，见 perception/system_events.py 模块 docstring）。
                if bridge.agent is not None:
                    self._drain_system_events(bridge)
                continue

            turn_id = cmd.turn_id
            # daemon 多用户架构 Phase 1：从 enqueue() 时传入的 meta 里取 user_id，
            # 单用户模式下 cmd.meta 为空，user_id 就是 ""，行为不变。
            user_id = cmd.meta.get("user_id", "") if cmd.meta else ""
            role    = cmd.meta.get("role", "") if cmd.meta else ""
            # [FIX] 见文件头部 _current_session_bridge_tls 的说明：这条
            # AgentRunner 线程接下来（直到 finally 清掉之前）触发的所有
            # output hook（agent_prefix / token / tool_call / …）都应该
            # 转发到*这个 session 自己的* bridge，而不是安装补丁时闭包
            # 捕获的那个全局 bridge。
            _current_session_bridge_tls.bridge = bridge
            bridge.set_state("running", turn_id=turn_id)
            bridge.emit_turn_start(turn_id, cmd.message, user_id=user_id)

            try:
                if bridge.agent is None:
                    raise RuntimeError("Agent not initialized")

                # daemon 多用户架构 Phase 2：把这一轮发起者的角色社交画像拼进
                # system_extra（临时改法，Phase 3 进入 per-session Agent 后会改成
                # "session 专属 cfg 在创建时就注入好"，不需要每条消息都换）。
                # 注意：role_profile_mgr 为 None（未开启多用户模式）或者
                # user_id 为空（单用户模式下 cmd.meta 本身就是空的）时，
                # 整段逻辑跳过，agent.cfg.system_extra 保持原样不动，行为与改造前完全一致。
                if self._role_profile_mgr is not None and user_id:
                    if self._base_system_extra is None:
                        # 懒加载：第一次真正用到时才读，此后固定不变
                        self._base_system_extra = getattr(bridge.agent.cfg, "system_extra", "") or ""
                    role_ctx = self._role_profile_mgr.build_system_context(user_id, role)
                    bridge.agent.cfg.system_extra = (
                        self._base_system_extra + "\n\n" + role_ctx
                    ).strip()

                # daemon 多用户架构 Phase 2：让 remember_about_user 工具（运行在
                # 这同一条 AgentRunner 线程上）能读到"这一轮是谁发的"。
                # 即使 role_profile_mgr 为 None 也调用（写入空字符串），
                # 这样 tools/user_memory.py::is_available() 的判断始终准确，
                # 不会读到上一次（如果有过）残留的用户身份。
                from mini_agent.tools.user_memory import set_current_user
                set_current_user(user_id, role)

                # ── 在终端模拟显示 Web 端发来的用户输入 ──────────────────
                # 让命令行侧看到 "You (web) ❯ <message>"，与正常 REPL 输入体验一致
                #
                # [FIX] 这条消息同时也会作为 turn_start 事件广播给所有 SSE
                # 订阅者，包括前台 --daemon-attach-console 场景下"attach 自己"
                # 的 run_connected_repl（见 cli/daemon.py::_handle_observer_frame
                # 的 turn_start 分支，同样会打印一遍 "You (web) ❯ <message>"）。
                # 之前这里没有检查 _SUPPRESS_NATIVE_PRINT，导致 attach-console
                # 模式下同一条用户输入被打印两次，且两边写终端还会互相打断、
                # 造成内容错位/被吞。开启该开关后交给 attach 上来的
                # run_connected_repl 唯一负责显示，这里不再重复打印。
                if not is_suppress_native_print():
                    from mini_agent.ui.terminal import _diag as _term_diag
                    if _term_diag._enabled:
                        _term_diag.log(
                            "server_turn",
                            f"echo 'You (web)' turn_id={turn_id!r} session={getattr(bridge.agent, 'session_id', None)!r} "
                            f"msg={cmd.message[:30]!r}",
                        )
                    _print_to_term(
                        f"\n[bold green]You (web)[/bold green][bold cyan] ❯ [/bold cyan]{cmd.message}"
                    )

                # 注入 turn_id，让 OutputHook 知道当前轮
                bridge.agent._http_turn_id = turn_id

                # 注入权限状态回调：当 PermissionGuard 进入等待时设置 bridge 状态
                # 这样 /v1/status 才能返回 waiting_permission，web 端才能显示权限面板
                _inject_permission_state_hook(bridge, bridge.agent)

                # ── slash 命令：本地执行，不当聊天内容发给 agent ─────────
                # 所有客户端（daemon connected CLI、web 面板等）统一通过
                # /v1/chat 提交消息，这里是唯一的、真正驱动 agent 的地方——
                # 之前这里没有区分，任何 "/xxx" 都被当成普通用户消息传给
                # agent.run_turn()，agent 会把它当一句话去理解/回答（例如
                # "/help" 被当成聊天内容，agent 甚至会去调用工具"猜"用户
                # 想干什么），完全没有执行真正的命令逻辑。
                #
                # 现在改成：识别到消息以 "/" 开头，就复用本地 REPL 同一套
                # cli.repl._handle_slash() 分发器，在这条 AgentRunner 线程上
                # （与 run_turn() 同一条线程，不会有并发访问 agent 状态的
                # 竞态）直接执行，通过 term.run_captured() 把它触发的所有
                # term.print()/rule()/panel()/... 输出捕获成纯文本，作为
                # "回复内容"通过 turn_done 事件推给发起请求的客户端——
                # 客户端侧的渲染逻辑完全不用改，就跟收到一段普通回复一样
                # 显示出来。
                _stripped = cmd.message.strip()
                if _stripped.startswith("/") and _stripped.lower() not in ("/exit", "/quit"):
                    from mini_agent.cli.repl import _handle_slash
                    from mini_agent.ui.terminal import term as _term_singleton

                    def _run_slash() -> None:
                        _handle_slash(_stripped, bridge.agent, getattr(bridge.agent, "skill_loader", None))

                    # ── 实时中继（daemon 模式命令行客户端显示不全 修复）──
                    # 之前这里只在 fn() 全部跑完后拿到整段 result，一次性
                    # 通过 turn_done 事件发出去——connected 客户端要么完全
                    # 不处理这个字段（历史 bug，已在 cli/daemon.py 修复），
                    # 要么长耗时命令执行期间完全没有任何反馈（"卡住了"的
                    # 观感）。现在用 run_captured(on_line=...) 把命令产生的
                    # 每一行输出实时转发成 command_output 事件；配合下面
                    # _SUPPRESS_TYPED_BROADCAST_DURING_CAPTURE（见其定义处
                    # 说明），期间 print_info/print_warning 等类型化事件
                    # 不再重复广播一次，避免同一行内容被显示两遍。turn_done
                    # 的 text 字段仍然保留完整文本，作为客户端一条实时事件
                    # 都没收到时（比如命令执行期间掉线重连）的兜底。
                    def _relay_line(_line: str, _tid=turn_id) -> None:
                        try:
                            bridge.emit_command_output(_line, turn_id=_tid)
                        except Exception as _mini_agent_exc:
                            from mini_agent.errors import log_exception
                            log_exception(_mini_agent_exc, where='mini_agent.api.server')

                    try:
                        # [FIX] 见文件头部 _local_term_write_lock 的说明：
                        # 光是给 output hook 里"单次" _orig_xxx()/write() 调用
                        # 加锁还不够——Terminal 内部的 _pending_stream 等状态
                        # 是跨调用累积的（给流式 token 里的标签做前瞻缓冲），
                        # 只序列化单次调用没法防止"A 的流刚写了一半，B 插进来
                        # 写了一截，把 A 留在 _pending_stream 里等着拼接的
                        # 尾部字符污染掉/冲掉"——表现出来就是本地终端显示的
                        # 内容开头缺字（比如"周杰伦的生日是…"少了"周杰伦"）。
                        # 这里把整个"这一轮会产生本地终端输出"的区间
                        # （run_captured / run_turn，内部会触发一整串
                        # prefix→token→...→结束的本地打印）当成一个不可
                        # 被别的 session 打断的临界区。不会影响真正的并发：
                        # 各 session 的 LLM 请求、工具调用本身仍然独立并发
                        # 执行，只有"这一轮的内容什么时候真正落到本地物理
                        # 终端上"被序列化了；每个 session 自己的 SSE 推送
                        # （客户端看到的内容）完全不受这把锁影响，一直都是
                        # 实时的。
                        with _local_term_write_lock:
                            result = _term_singleton.run_captured(_run_slash, on_line=_relay_line).strip()
                    except Exception as _cmd_e:
                        result = f"[error] command failed: {_cmd_e}"
                    if not result:
                        result = "(no output)"
                else:
                    from mini_agent.ui.terminal import _diag as _term_diag
                    if _term_diag._enabled:
                        _term_diag.log(
                            "server_turn",
                            f"about to acquire _local_term_write_lock for run_turn "
                            f"turn_id={turn_id!r} session={getattr(bridge.agent, 'session_id', None)!r}",
                        )
                    with _local_term_write_lock:
                        if _term_diag._enabled:
                            _term_diag.log("server_turn", f"lock acquired, calling run_turn turn_id={turn_id!r}")
                        result = bridge.agent.run_turn(cmd.message)
                        if _term_diag._enabled:
                            _term_diag.log("server_turn", f"run_turn returned, releasing lock turn_id={turn_id!r}")

                iq.mark_done(turn_id)
                bridge.emit_turn_done(turn_id, text=result or "", user_id=user_id)

                # ObjectiveExecutor 回调：若此 turn 是自主步骤，推进到下一步
                _obj_exec = getattr(bridge, "_objective_executor", None)
                if _obj_exec is not None and cmd.initiator in ("autonomous", "cron"):
                    try:
                        # 从 result 的首句提取摘要（不超过 200 字）
                        _summary = (result or "").strip()
                        _summary = _summary.split("\n")[0][:200]
                        _obj_exec.on_turn_done(turn_id, _summary)
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.api.server')
                        pass

                # daemon 多用户架构 Phase 2：每轮成功对话后更新该用户的 last_contact/
                # contact_count。放在这里（而不是设计文档原计划的"session 切换前"），
                # 是因为 Phase 1/2 阶段所有用户共享同一个全局 Agent/历史，用户很可能
                # 整段对话都不会触发 /sessions/new 或 /resume，"切换 session 时才更新"
                # 会导致这两个字段在很多场景下永远不更新，对不上验收标准里
                # "多轮对话后能看到 last_contact 更新"的要求。按 turn 更新才是
                # 在当前模型下真正对得上请求频率的时机。
                if self._role_profile_mgr is not None and user_id:
                    try:
                        profile = self._role_profile_mgr.get_profile(user_id)
                        contact_count = profile.get("contact_count", 0) + 1
                        self._role_profile_mgr.update_profile(user_id, {
                            "last_contact": time.time(),
                            "contact_count": contact_count,
                        })
                    except Exception:
                        pass  # 画像更新失败不应影响主对话流程

            except Exception as e:
                tb = traceback.format_exc()
                iq.mark_error(turn_id)
                bridge.emit_error(f"{type(e).__name__}: {e}\n{tb}", turn_id=turn_id, user_id=user_id)
                # 关键修复：即使出错也必须发出 turn_done（text 为空，附带 error 标记），
                # 否则等待 /v1/stream/{turn_id} 的客户端（CLI/Web）会一直阻塞到超时，
                # 既看不到错误也等不到下一个输入提示。
                bridge.emit_turn_done(
                    turn_id, text="", meta={"error": f"{type(e).__name__}: {e}"}, user_id=user_id
                )
                # ObjectiveExecutor 失败回调
                _obj_exec = getattr(bridge, "_objective_executor", None)
                if _obj_exec is not None and cmd.initiator in ("autonomous", "cron"):
                    try:
                        _obj_exec.on_turn_failed(turn_id, str(e))
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.api.server')
                        pass
            finally:
                bridge.set_state("idle", turn_id=None)
                if hasattr(bridge.agent, "_http_turn_id"):
                    bridge.agent._http_turn_id = ""
                # [FIX] 清掉线程局部变量，避免这条线程后面万一又跑到别的
                # 代码路径（比如 AutonomousLoop.tick()，同一条线程复用）时
                # 残留上一轮的 session bridge 指向。
                _current_session_bridge_tls.bridge = None

                # daemon 多用户架构 Phase 2：还原 system_extra，避免这一轮注入的角色
                # 上下文残留到"非 web 触发"的下一次使用（比如同一 daemon 进程里
                # CLI 命令行侧直接交互、或 AutonomousLoop.tick() 走到需要 system prompt
                # 的逻辑）。两者不应该看到上一个 web 用户的角色画像片段。
                if self._role_profile_mgr is not None and self._base_system_extra is not None:
                    bridge.agent.cfg.system_extra = self._base_system_extra

                # 同理清空 remember_about_user 工具读到的"当前用户"——
                # 这条 AgentRunner 线程稍后可能去跑 AutonomousLoop.tick()，
                # 那不是任何用户发起的，不应该让 remember_about_user 误以为
                # 还在为上一个用户服务。
                from mini_agent.tools.user_memory import clear_current_user
                clear_current_user()

                # ── run_turn 完成后，提示命令行侧可继续输入 ────────────
                # [FIX] 同上：attach-console 模式下这条提示没有实际意义
                # （run_connected_repl 自己的输入循环会在 turn_done 后自然
                # 恢复可输入状态，不需要额外提示），且如果不加判断会在这里
                # 又打印一遍、和 observer 端的输出交错，一并纳入抑制开关。
                if not is_suppress_native_print():
                    _print_to_term(
                        "[dim]─── Web 请求处理完毕，你可以继续在此输入 ───[/dim]"
                    )

    def _drain_self_messages(self) -> None:
        """
        daemon 多用户架构 Phase 4：处理 SelfMessageBus 上 to_id="self" 的消息。

        只在这条 AgentRunner 被识别为"Self"时才会被调用（self_message_bus
        非 None，即 HttpServer 在 multi_user_enabled 时传入的那个 bus 实例）。

        每种 msg_type 的处理都包在各自的 try/except 里——任何一条消息处理失败
        都不应该影响这条主循环继续往下处理其它消息，更不应该让 Self 自己
        的线程崩掉（Self 崩了影响面比某一个 SessionAgent 崩了大得多）。
        """
        for msg in self._self_message_bus.drain_all("self"):
            try:
                if msg.msg_type == "session_crashed":
                    self._handle_session_crashed(msg.payload)
                elif msg.msg_type == "session_summary":
                    self._handle_session_summary(msg.payload)
                # 其它 msg_type（approval_req / peer_message / context_inject 等）
                # 是 Phase 4 之后才会真正用到的扩展点，目前没有处理逻辑，
                # 静默忽略而不是报错——保持向前兼容，未来加新 msg_type 不需要
                # 同步改这里的 if/elif 链。
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.server')
                pass

    def _drain_system_events(self, bridge: AgentBridge) -> None:
        """
        [事件总线] 每个 idle 周期消费一次 events.jsonl 里的 instant 层事件。

        目前只处理 "proprioception.frustration_spike" 一种事件类型，作为
        事件总线的第一个真实接入案例：某个 session 的挫败感越过阈值时，
        提前跑一次 SelfMaintenanceModule 健康检查，而不是等 24 小时的
        should_run_self_maintenance() 时间门控——但依然要过这个时间门控
        （interval 可以设得比默认 24h 短，见下面注释），避免"一次挫败感尖峰
        触发自维护 → 自维护本身的工具调用又失败一次 → 又触发一次"的连锁抖动。

        这条 AgentRunner 不一定是产生该事件的那条 session 线程（同一 workdir
        下任意 session 的 frustration 都会被这里看到）——这正是事件总线要解决
        的问题：ResourceArbiter/SelfMaintenanceModule 是 workdir 级单例，不
        持有活跃 Agent 引用，需要靠这类跨 session 广播的信号来感知"当前是不是
        该缓一缓"。多个 AgentRunner 线程可能同时读到同一条 instant 事件（各自
        游标独立，consumer_name 目前用固定值 "daemon_instant_consumer"，即
        同一 workdir 下所有 AgentRunner 共享一个游标——这是有意为之：健康检查
        只需要跑一次，不需要每个 session 线程各跑一次），第一个跑到的线程
        推进游标，后续线程自然读不到已消费的事件，不会重复触发。
        """
        try:
            from mini_agent.perception import system_events as _se
            from mini_agent.storage.paths import AgentPaths as _AP

            paths = _AP(bridge.agent.cfg.project_root)
            events = _se.poll_since(
                paths,
                consumer_name="daemon_instant_consumer",
                tiers=["instant"],
            )
        except Exception:
            return  # 事件总线读取失败不应影响主循环，静默跳过本轮

        for evt in events:
            try:
                if evt.event_type == "proprioception.frustration_spike":
                    self._maybe_early_self_maintenance(paths)
                # 其它 instant event_type 是未来扩展点，目前静默忽略，
                # 与 _drain_self_messages() 对未知 msg_type 的处理方式一致。
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.api.server')
                pass

    def _maybe_early_self_maintenance(self, paths) -> None:
        """被 frustration_spike 事件唤醒时，尝试提前跑一次自维护健康检查。

        依然过 should_run_self_maintenance() 的时间门控——只是把 interval
        从默认 24h 换成一个更短的"事件触发专用"间隔，而不是完全绕开门控。
        这样即使短时间内收到多条 frustration_spike 事件（比如同一个 session
        反复越过阈值），也不会每次都真的重新跑一遍健康检查。
        """
        from mini_agent.evolution.self_maintenance import (
            should_run_self_maintenance,
            run_self_maintenance,
        )

        # 事件触发场景比常规 24h 定时扫描更紧急，但也不该短到几分钟内被
        # 同一个反复受挫的 session 打成刷屏——1 小时是一个保守的起点，
        # 后续如果观察到确实有价值再考虑做成可配置项。
        _EVENT_TRIGGERED_INTERVAL_HOURS = 1.0

        if not should_run_self_maintenance(paths, interval_hours=_EVENT_TRIGGERED_INTERVAL_HOURS):
            return

        run_self_maintenance(paths)

    def _handle_session_crashed(self, payload: dict) -> None:
        """
        记录某个 SessionAgent 崩溃的通知。

        目前的处理方式：写进 Self 的 activity digest（`mini-agent daemon status`/
        将来的 `mini-agent self status` 能读到），并在 daemon 自己的终端打印一行
        提示——这是"主人应该知道，但不需要打断当前对话"级别的事件，不通过
        bridge.emit_error 之类的方式广播给任何 HTTP 客户端（这条消息从语义上
        就只跟 Self 自己有关，不属于任何用户的对话流）。
        """
        from mini_agent.evolution.resource_arbiter import append_activity_digest

        session_id = payload.get("session_id", "?")
        user_id    = payload.get("user_id", "?")
        error      = payload.get("error", "")

        _print_to_term(
            f"[yellow]⚠ SessionAgent crashed: session={session_id} user={user_id} "
            f"error={error}[/yellow]"
        )

        try:
            # 修复一个真实存在的 bug：Agent 实例上根本没有 `_paths` 这个属性
            # （agent.py 内部需要 AgentPaths 时，都是 AgentPaths(cfg.project_root)
            # 现场构造，从不缓存成 self._paths/self.paths）。之前这里写的
            # `getattr(self._bridge.agent, "_paths", None)` 永远拿到 None，
            # 这段 activity_digest 写入代码从一开始就是静默 no-op，从来没真正
            # 执行过——是写 Phase 4 测试时才发现的（测试断言 digest 文件存在，
            # 实际上文件从未被创建）。
            from mini_agent.storage.paths import AgentPaths
            project_root = getattr(self._bridge.agent.cfg, "project_root", None)
            if project_root is not None:
                paths = AgentPaths(project_root)
                append_activity_digest(paths, {
                    "type":    "session_crashed",
                    "summary": f"Session {session_id} (user={user_id}) crashed: {error}",
                    "session_id": session_id,
                    "user_id":    user_id,
                    "error":      error,
                })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.server')
            pass

    def _handle_session_summary(self, payload: dict) -> None:
        """
        处理某个正常结束（suspend）的 session 的摘要上报。

        按设计文档 Phase 4 计划：调用 RoleProfileManager.update_profile()
        做汇总——往该用户画像的 recent_sessions 里追加一条记录（保留最近 10 条，
        避免 profile.json 随对话次数无限增长）。owner 的 session 不做这个处理：
        owner 已经有独立的 profile.py::UserProfileManager 在自动生成更详细的
        个性化总结，这里再记一份是重复劳动，也会跟那套系统的数据混在一起。
        """
        if self._role_profile_mgr is None:
            return

        user_id = payload.get("user_id", "")
        if not user_id or user_id == "owner":
            return

        try:
            profile = self._role_profile_mgr.get_profile(user_id)
            recent = list(profile.get("recent_sessions", []))
            recent.append({
                "session_id": payload.get("session_id", ""),
                "title":      payload.get("title", ""),
                "summary":    payload.get("summary", ""),
                "turns":      payload.get("turns", 0),
                "ended_at":   time.time(),
            })
            recent = recent[-10:]  # 只保留最近 10 条，避免无限增长
            self._role_profile_mgr.update_profile(user_id, {"recent_sessions": recent})
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.server')
            pass


# ── FastAPI App 工厂 ──────────────────────────────────────────────────────────

def create_app(
    bridge:      AgentBridge,
    fs_helper:   FsHelper,
    token:       str,
    allowed_ips: list[str],
    cors_origins: list[str],
    role_store: Optional[UserStore] = None,
    project_root: Optional[Path] = None,
    session_pool: Optional[Any] = None,  # daemon 多用户架构 Phase 3: Optional[SessionAgentPool]
) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 绑定 asyncio 事件循环给广播器（必须在 async 上下文中）
        loop = asyncio.get_running_loop()
        bridge.broadcaster.set_loop(loop)
        # [FIX] daemon 多 session 隔离场景下，SessionAgentPool 会为每个新
        # session 各自 init_bridge() 出一个独立的 AgentBridge（见
        # session_pool.py::_create_entry）。之前只有这里传进来的全局
        # `bridge`（app.state.bridge，单用户模式下唯一真正会被使用的那个）
        # 绑定了事件循环，SessionAgentPool 新建的那些 per-session bridge
        # 的 broadcaster._loop 永远是 None——OutputBroadcaster.publish()
        # 判断 `if self._loop and not self._loop.is_closed()` 为 False 时
        # 直接跳过 call_soon_threadsafe，事件根本不会被送进任何 SSE 订阅者
        # 的队列。表现出来就是：任何"带 session_id 落到 SessionAgentPool"
        # 的会话（典型场景：daemon 多客户端场景下新建的 session），daemon
        # 自己的终端能正常看到 AgentRunner 跑完、打印出回复（那是
        # _print_to_term 直接写终端，不走 SSE），但通过 /v1/stream/{turn_id}
        # 订阅的客户端永远收不到任何 token/turn_done 事件，只能拿到
        # heartbeat，最终radio静默、连接超时也看不到回复。
        # 这里把同一个 loop 也告诉 session_pool，让它给此后新建的每个
        # per-session bridge 都调用 set_loop()，事件才能真正广播出去。
        if session_pool is not None:
            session_pool.set_loop(loop)
        bridge.emit(AgentEvent(
            type=EventType.STATUS,
            data={"message": "HTTP API server ready"},
        ))
        yield
        # 关闭时不做特别处理（AgentRunner 是 daemon 线程，随主进程退出）

    app = FastAPI(
        title       = "mini-agent HTTP API",
        version     = get_version(),
        lifespan    = lifespan,
        docs_url    = "/docs",
        redoc_url   = "/redoc",
        openapi_url = "/openapi.json",
    )

    # ── CORS（外部 Web UI / 移动端需要）──────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins     = cors_origins or ["*"],
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    # ── 鉴权中间件 ────────────────────────────────────────────────────────
    # daemon 多用户架构 Phase 1：role_store 非空时，说明开启了多用户模式，
    # 挂载 MultiUserAuthMiddleware（按 token 区分用户身份/角色）；
    # 否则保持现状，挂载单 token 的 AuthMiddleware，不影响现有单用户部署。
    if role_store is not None:
        app.add_middleware(MultiUserAuthMiddleware, role_store=role_store, allowed_ips=allowed_ips)
    else:
        app.add_middleware(AuthMiddleware, token=token, allowed_ips=allowed_ips)

    # ── 路由 ──────────────────────────────────────────────────────────────
    app.include_router(router)

    @app.get("/")
    async def root():
        return {
            "name":    "mini-agent HTTP API",
            "version": get_version(),
            "docs":    "/docs",
        }

    # ── 注入到 app.state ──────────────────────────────────────────────────
    app.state.bridge       = bridge
    app.state.fs_helper    = fs_helper
    app.state.role_store   = role_store    # None = 单用户模式（Phase 1 未开启）
    app.state.project_root = project_root  # daemon 多用户架构 Phase 3
    app.state.session_pool = session_pool  # None = 单用户模式 / Phase 3 未开启

    return app


# ── 服务启动入口 ──────────────────────────────────────────────────────────────

class HttpServer:
    """
    封装 uvicorn 服务的启动/停止，在独立线程中运行，
    不阻塞主进程的 REPL 循环。
    """

    def __init__(
        self,
        agent:            Any,
        project_root:     Path,
        host:             str  = "127.0.0.1",
        port:             int  = 8765,
        configured_token: str  = "",
        allowed_ips:      Optional[list[str]] = None,
        cors_origins:     Optional[list[str]] = None,
        fs_readonly:      bool = False,
        fs_excludes:      Optional[list[str]] = None,
        ring_maxlen:      int  = 2000,
        multi_user_enabled: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._project_root = project_root

        # Token
        self._token = load_or_generate_token(project_root, configured_token)

        # IP 白名单（默认只允许本机）
        self._allowed_ips = allowed_ips if allowed_ips is not None else ["127.0.0.1", "::1"]

        # CORS 允许来源
        self._cors_origins = cors_origins or []

        # Bridge
        self._bridge = init_bridge(ring_maxlen=ring_maxlen)
        self._bridge.agent = agent

        # 文件系统助手
        self._fs_helper = FsHelper(
            project_root = project_root,
            readonly     = fs_readonly,
            excludes     = fs_excludes,
        )

        # daemon 多用户架构 Phase 1：multi_user_enabled 时创建 UserStore 并确保
        # owner 用户存在（owner token 复用上面已经算好的 self._token，保持
        # "原有单 token 升级为 owner token"的自然过渡——开启多用户模式前用的
        # 那个 token 不会失效，只是现在它对应的身份叫 owner）。
        # Phase 2：同时创建 RoleProfileManager，管理每个用户的社交画像
        # （<project_root>/.agent/users/<user_id>/profile.json）。
        # Phase 3：同时创建 SessionAgentPool + SelfMessageBus，管理每个用户
        # 每个 session 各自独立的 Agent 实例。注意：传进来的 `agent` 参数
        # （app.py 在主线程构造好的那个）在多用户模式下被重新定位为"Self"——
        # 它继续驱动 self._bridge/self._runner（下面几行不变），SessionAgentPool
        # 不会复用它，每个 session 都会用 agent.cfg 当模板各自深拷贝一份、
        # 各自独立构造全新的 Agent（见 session_pool.py 模块 docstring）。
        self._multi_user_enabled = multi_user_enabled
        self._role_store: Optional[UserStore] = None
        self._role_profile_mgr: Optional[RoleProfileManager] = None
        if multi_user_enabled:
            users_dir = project_root / ".agent" / "users"
            self._role_store = UserStore(users_dir)
            self._role_store.ensure_owner(configured_token=self._token)
            self._role_profile_mgr = RoleProfileManager(users_dir)

        # daemon 多 session 隔离：无论是否开启多用户认证（multi_user_enabled），
        # 都要构造 SessionAgentPool——它是"不同客户端连接到不同 session 时
        # 互不干扰"的关键机制（按 (user_id, session_id) 各自独立的 Agent 实例）。
        # 之前这里只在 multi_user_enabled 时才创建，导致单 token 部署下所有
        # 客户端共用 app.state.bridge 上唯一的那个全局 agent：任何一个客户端
        # 新建/切换 session，都会通过 bridge.agent 影响到其它所有客户端。
        # 单 token 模式下 AuthMiddleware 会给所有请求注入同一个
        # user_id="owner" 的 UserContext（见 api/auth.py），owner 的 session
        # 仍然落在原来的全局 <project_root>/.agent/sessions/ 目录，完全向后兼容。
        from mini_agent.api.session_pool import SessionAgentPool, SelfMessageBus

        skill_loader = getattr(agent, "skill_loader", None)
        skill_dirs = list(getattr(skill_loader, "dirs", []) or [])

        self._self_message_bus = SelfMessageBus()
        self._session_pool = SessionAgentPool(
            base_cfg=agent.cfg,
            role_profile_mgr=self._role_profile_mgr,
            bus=self._self_message_bus,
            skill_dirs=skill_dirs,
        )
        self._session_pool.start_monitor()

        # daemon 多用户架构 Phase 2：把 RoleProfileManager 注入 remember_about_user
        # 工具（tools/user_memory.py）。多用户模式未开启时传 None，
        # is_available() 会返回 False，工具调用时直接给出友好提示，不会报错崩溃。
        from mini_agent.tools.user_memory import set_role_profile_manager
        set_role_profile_manager(self._role_profile_mgr)

        # Stage 9 §7.2: 初始化 AutonomousLoop（在 daemon 进程中挂载到 AgentRunner）
        self._autonomous_loop = self._build_autonomous_loop(agent)

        # AgentRunner（后台驱动 agent.run_turn），注入 AutonomousLoop + RoleProfileManager
        # + SelfMessageBus（Phase 4：这条 AgentRunner 就是"Self"，需要消费
        # SessionAgentPool 发过来的 session_crashed/session_summary 等消息）。
        self._runner = AgentRunner(
            self._bridge,
            autonomous_loop=self._autonomous_loop,
            role_profile_mgr=self._role_profile_mgr,
            self_message_bus=self._self_message_bus,
        )

        # uvicorn 服务线程
        self._server_thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

    def _build_autonomous_loop(self, agent: Any):
        """
        Stage 9 §7.2: 构建 AutonomousLoop 实例，同时初始化 CronScheduler 和
        ObjectiveExecutor，注入到 AutonomousLoop 和 AgentRunner。
        """
        try:
            from mini_agent.evolution.autonomous_loop import AutonomousLoop
            from mini_agent.perception.goal_backlog import load_goal_backlog
            from mini_agent.storage.paths import AgentPaths

            cfg = getattr(agent, "cfg", None)
            project_root = getattr(cfg, "project_root", None) if cfg is not None else None
            if cfg is None or project_root is None:
                return None
            paths = AgentPaths(project_root)

            goal_backlog = load_goal_backlog(paths)

            # ── CronScheduler ────────────────────────────────────────────────
            def _cron_submit(message: str, initiator: str, meta: dict):
                try:
                    return self._bridge.input_queue.enqueue(
                        message=message,
                        initiator=initiator,
                        meta=meta,
                    )
                except Exception:
                    return None

            from mini_agent.evolution.cron_scheduler import load_cron_scheduler
            cron_scheduler = load_cron_scheduler(paths, submit_fn=_cron_submit)

            # ── ObjectiveExecutor ────────────────────────────────────────────
            def _obj_submit(message: str, initiator: str, meta: dict):
                """提交自主步骤到 InputQueue，返回 turn_id。"""
                try:
                    return self._bridge.input_queue.enqueue(
                        message=message,
                        initiator=initiator,
                        meta=meta,
                    )
                except Exception:
                    return None

            def _llm_decompose(objective):
                """用 agent 当前 LLM client 拆解 Objective。"""
                try:
                    from mini_agent.evolution.objective_executor import _default_llm_decompose
                    llm = getattr(agent, "_llm", None)
                    if llm is None:
                        return []
                    return _default_llm_decompose(llm, objective)
                except Exception:
                    return []

            bridge_ref = self._bridge

            def _on_progress(execution):
                """Objective 步骤推进时推 SSE 事件。"""
                try:
                    done, total = execution.progress_ratio
                    cur = execution.current_step
                    bridge_ref.emit_objective_progress(
                        execution_id=execution.execution_id,
                        objective_id=execution.objective_id,
                        title=execution.objective_title,
                        status=execution.status,
                        progress=f"{done}/{total}",
                        current_step=cur.description[:80] if cur else "",
                    )
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.api.server')
                    pass

            from mini_agent.evolution.objective_executor import ObjectiveExecutor
            objective_executor = ObjectiveExecutor(
                paths=paths,
                submit_fn=_obj_submit,
                llm_decompose_fn=_llm_decompose,
                on_progress_fn=_on_progress,
            )
            objective_executor.load()

            # 把 ObjectiveExecutor 和 CronScheduler 挂到 bridge，
            # 供 AgentRunner.run() 在 turn 完成后回调
            self._bridge._objective_executor = objective_executor
            self._bridge._cron_scheduler = cron_scheduler
            # 也挂到 agent，供 /cron REPL 命令使用
            if agent is not None:
                agent._cron_scheduler = cron_scheduler
                agent._objective_executor = objective_executor

            return AutonomousLoop(
                goal_backlog=goal_backlog,
                input_queue=self._bridge.input_queue,
                paths=paths,
                cfg=cfg,
                tick_interval_seconds=60.0,
                cron_scheduler=cron_scheduler,
                objective_executor=objective_executor,
            )
        except Exception:
            return None

    @property
    def bridge(self) -> AgentBridge:
        return self._bridge

    @property
    def token(self) -> str:
        return self._token

    @property
    def role_store(self) -> Optional[UserStore]:
        """daemon 多用户架构 Phase 1：未开启多用户模式时为 None。"""
        return self._role_store

    @property
    def role_profile_mgr(self) -> Optional[RoleProfileManager]:
        """daemon 多用户架构 Phase 2：未开启多用户模式时为 None。"""
        return self._role_profile_mgr

    @property
    def session_pool(self):
        """daemon 多用户架构 Phase 3：未开启多用户模式时为 None。"""
        return self._session_pool

    @property
    def autonomous_loop(self):
        """返回 AutonomousLoop 实例（供 daemon status 命令查询）。"""
        return self._autonomous_loop

    def start(self) -> None:
        """启动 AgentRunner 和 uvicorn（均在后台线程）。"""
        # 安装输出钩子（把 agent 的 Renderer 输出接入 bridge）
        _install_output_hook(self._bridge)

        # 启动 AgentRunner
        self._runner.start()

        # 创建 FastAPI app
        app = create_app(
            bridge       = self._bridge,
            fs_helper    = self._fs_helper,
            token        = self._token,
            allowed_ips  = self._allowed_ips,
            cors_origins = self._cors_origins,
            role_store   = self._role_store,
            project_root = self._project_root,
            session_pool = self._session_pool,
        )
        # Stage 9 §3: 注入 HttpServer 自身到 app.state，使 routes.py 可查询 AutonomousLoop
        app.state.http_server = self

        # uvicorn config（禁用 access log，避免污染终端）
        cfg = uvicorn.Config(
            app        = app,
            host       = self._host,
            port       = self._port,
            log_level  = "warning",
            access_log = False,
            loop       = "asyncio",
        )
        self._uvicorn_server = uvicorn.Server(cfg)

        def _run():
            self._uvicorn_server.run()  # type: ignore[union-attr]

        self._server_thread = threading.Thread(
            target=_run, name="http-server", daemon=True
        )
        self._server_thread.start()

        # 等待 uvicorn 就绪（最多 5 秒）
        for _ in range(50):
            if self._uvicorn_server.started:
                break
            time.sleep(0.1)

        print_token_banner(self._token, self._host, self._port)
        if self._multi_user_enabled:
            self._print_multi_user_banner()

    def _print_multi_user_banner(self) -> None:
        """daemon 多用户架构 Phase 1：提示已开启多用户模式，上面打印的 token 即 owner token。"""
        n_others = max(len(self._role_store.list_users()) - 1, 0)  # 减去 owner 自己
        print(
            "  👥  Multi-user mode: ON  (above token = owner)\n"
            f"  Other users: {n_others}\n"
            "  Manage: mini-agent user list / add / remove\n",
            flush=True,
        )

    def stop(self) -> None:
        """优雅关闭。"""
        self._runner.stop()
        # daemon 多用户架构 Phase 3：保存并停止所有活跃的 SessionAgent，
        # 不要让用户的对话历史因为 daemon 关闭而丢失未落盘的内容。
        if self._session_pool is not None:
            self._session_pool.stop_all()
        if self._uvicorn_server:
            self._uvicorn_server.should_exit = True
        if self._server_thread:
            self._server_thread.join(timeout=3.0)


# ── 输出钩子：把 Renderer 的输出接入 bridge ───────────────────────────────────

def _install_output_hook(bridge: AgentBridge) -> None:
    """
    Monkey-patch mini_agent.ui.renderer 模块的输出函数，
    在原有终端输出的同时把内容推入 bridge 广播给 HTTP 客户端。

    renderer.py 里全部是模块级函数（print_tool_call / print_tool_result / …），
    没有 R 类实例。必须直接 patch 模块属性，而不是 patch 类方法。

    同时 patch StreamWriter.write() 拦截流式 token。
    """
    try:
        from mini_agent.ui import renderer as mod
    except Exception as e:
        _print_to_term(f"[yellow]⚠ output hook import failed: {e}[/yellow]")
        return

    def _tid() -> str:
        b = _effective_output_bridge(bridge)
        return getattr(b.agent, "_http_turn_id", "") if b.agent else ""

    # ── capture 模式下抑制类型化事件的重复广播 ──────────────────────────
    # 背景：run_captured() 现在支持 on_line 回调，把 slash 命令执行期间的
    # 每一行输出实时转发成 command_output 事件（见 api/server.py 里调用
    # run_captured(..., on_line=_relay_line) 的地方）。但下面这些
    # print_info/print_warning/print_tool_call/... 补丁本来就会在*任何*
    # 时候（包括 run_captured() 期间）把同一次调用广播成一条类型化事件
    # （info/warning/tool_call/...）——如果不做区分，slash 命令里每一条
    # R.print_info() 之类的调用都会被广播两次：一次是这里的类型化事件，
    # 一次是 command_output 的实时中继，客户端就会看到同一行内容显示
    # 两遍。用 term._capture_mode + term._capture_relay 是否同时为真
    # （即"正处于一个设置了实时中继的 run_captured() 调用中"）来判断要不要
    # 跳过这里的广播——只跳过*广播*，本地渲染（_orig_xxx 调用）永远不受
    # 影响，daemon 本地终端的显示效果不变。
    def _in_relayed_capture() -> bool:
        try:
            return bool(_term_singleton._capture_mode) and _term_singleton._capture_relay is not None
        except Exception:
            return False

    from mini_agent.ui.terminal import term as _term_singleton

    # ── print_assistant_prefix（"XXX ❯ " 前缀，标识当前是谁在说话）────────
    # [SYS-AGENT-PREFIX] 主 Agent 和 GoalJudge/TurnJudge 等内部子 Agent 都会
    # 各自带着自己的 cfg.agent_name 调用这个函数，之前完全没转发给 SSE，
    # 是 daemon connected 模式 / kanban 显示错误前缀（永远是启动时那个固定
    # agent_name）的根因。这里转发一条 agent_prefix 事件，客户端据此更新
    # "当前这一段输出是谁在说"，而不是自己瞎猜。
    _orig_print_assistant_prefix = mod.print_assistant_prefix
    def _print_assistant_prefix(agent_name: str = "orzooo") -> None:
        from mini_agent.ui.terminal import _diag as _term_diag
        if _term_diag._enabled:
            _term_diag.log(
                "server_hook",
                f"print_assistant_prefix agent_name={agent_name!r} "
                f"suppress={_SUPPRESS_NATIVE_PRINT} capture={_in_relayed_capture()} "
                f"turn_id={_tid()!r}",
            )
        if not _SUPPRESS_NATIVE_PRINT:
            with _local_term_write_lock:
                _orig_print_assistant_prefix(agent_name)
        if not _in_relayed_capture():
            _effective_output_bridge(bridge).emit_agent_prefix(agent_name, turn_id=_tid())
    mod.print_assistant_prefix = _print_assistant_prefix

    # ── print_markdown（非流式回复的最终文本走这里）──────────────────────
    _orig_print_markdown = mod.print_markdown
    def _print_markdown(md: str) -> None:
        from mini_agent.ui.terminal import _diag as _term_diag
        if _term_diag._enabled:
            _term_diag.log(
                "server_hook",
                f"print_markdown len={len(md)} suppress={_SUPPRESS_NATIVE_PRINT} "
                f"capture={_in_relayed_capture()} turn_id={_tid()!r} "
                f"head={md[:30]!r} tail={md[-30:]!r}",
            )
        if not _SUPPRESS_NATIVE_PRINT:
            with _local_term_write_lock:
                _orig_print_markdown(md)
        if not _in_relayed_capture():
            _effective_output_bridge(bridge).emit_token(md, turn_id=_tid())
    mod.print_markdown = _print_markdown

    # ── StreamWriter.write（流式 token）──────────────────────────────────
    _OrigStreamWriter = mod.StreamWriter
    class _PatchedStreamWriter(_OrigStreamWriter):
        def write(self, token: str) -> None:
            from mini_agent.ui.terminal import _diag as _term_diag
            if _term_diag._enabled:
                _term_diag.log(
                    "server_hook",
                    f"stream_token turn_id={_tid()!r} suppress={_SUPPRESS_NATIVE_PRINT} "
                    f"token={token!r}",
                )
            if not _SUPPRESS_NATIVE_PRINT:
                # [FIX] 本地物理终端是所有 session 共享的单例，多个 session
                # 同时流式输出 token 时，不加锁会在字符粒度上相互打断、
                # 拼接错乱（"daemon 进程显示紊乱"的直接成因）。这里序列化
                # 每一次 token 落笔，只影响本地终端显示顺序，不影响各
                # session 各自的 SSE 推送（那部分从来就是各走各的 bridge，
                # 不受这把锁影响）。
                with _local_term_write_lock:
                    super().write(token)
            # [FIX] 之前这里恒用闭包捕获的全局 bridge，daemon 多 session
            # 场景下这个 token 属于哪个 session 就完全对不上——见文件头部
            # _current_session_bridge_tls 的说明。
            _effective_output_bridge(bridge).emit_token(token, turn_id=_tid())
    mod.StreamWriter = _PatchedStreamWriter

    # ── print_reasoning / header / footer（思维链流式输出）───────────────────
    # 之前完全没有转发——是 connected 客户端看不到 "── Reasoning ──" 整块
    # 内容的根因，见 bridge.py::emit_reasoning() 的详细说明。
    _orig_print_reasoning = mod.print_reasoning
    def _print_reasoning(token: str) -> None:
        if not _SUPPRESS_NATIVE_PRINT:
            with _local_term_write_lock:
                _orig_print_reasoning(token)
        if not _in_relayed_capture():
            _effective_output_bridge(bridge).emit_reasoning(turn_id=_tid(), text=token)
    mod.print_reasoning = _print_reasoning

    _orig_print_reasoning_header = mod.print_reasoning_header
    def _print_reasoning_header() -> None:
        if not _SUPPRESS_NATIVE_PRINT:
            with _local_term_write_lock:
                _orig_print_reasoning_header()
        if not _in_relayed_capture():
            _effective_output_bridge(bridge).emit_reasoning(turn_id=_tid(), marker="start")
    mod.print_reasoning_header = _print_reasoning_header

    _orig_print_reasoning_footer = mod.print_reasoning_footer
    def _print_reasoning_footer() -> None:
        if not _SUPPRESS_NATIVE_PRINT:
            with _local_term_write_lock:
                _orig_print_reasoning_footer()
        if not _in_relayed_capture():
            _effective_output_bridge(bridge).emit_reasoning(turn_id=_tid(), marker="end")
    mod.print_reasoning_footer = _print_reasoning_footer

    # ── print_skill_loaded ──────────────────────────────────────────────────
    _orig_print_skill_loaded = mod.print_skill_loaded
    def _print_skill_loaded(name: str) -> None:
        if not _SUPPRESS_NATIVE_PRINT:
            with _local_term_write_lock:
                _orig_print_skill_loaded(name)
        if not _in_relayed_capture():
            _effective_output_bridge(bridge).emit_skill_loaded(name, turn_id=_tid())
    mod.print_skill_loaded = _print_skill_loaded

    # ── print_tool_call ───────────────────────────────────────────────────
    _orig_print_tool_call = mod.print_tool_call
    def _print_tool_call(tool_name: str, tool_input: dict, **kw) -> None:
        if not _SUPPRESS_NATIVE_PRINT:
            with _local_term_write_lock:
                _orig_print_tool_call(tool_name, tool_input, **kw)
        # tool_executor.py 调用 print_tool_call() 时传了
        # verbose=self.cfg.verbose（决定本地终端是否展示完整入参 JSON）。
        # 之前这里的 **kw 被吃掉、从未转发给 emit_tool_call()，导致
        # connected 客户端永远收不到这个信息、只能展示成"非 verbose"
        # 效果——即便 daemon 本地明明是 verbose 模式。这里显式取出并透传。
        if not _in_relayed_capture():
            _effective_output_bridge(bridge).emit_tool_call(
                tool_name, tool_input, turn_id=_tid(),
                verbose=bool(kw.get("verbose", False)),
            )
    mod.print_tool_call = _print_tool_call

    # ── print_tool_result ─────────────────────────────────────────────────
    _orig_print_tool_result = mod.print_tool_result
    def _print_tool_result(tool_name: str, result: str, **kw) -> None:
        if not _SUPPRESS_NATIVE_PRINT:
            with _local_term_write_lock:
                _orig_print_tool_result(tool_name, result, **kw)
        if not _in_relayed_capture():
            _effective_output_bridge(bridge).emit_tool_result(tool_name, str(result), turn_id=_tid())
    mod.print_tool_result = _print_tool_result

    # ── print_tool_error ──────────────────────────────────────────────────
    _orig_print_tool_error = mod.print_tool_error
    def _print_tool_error(tool_name: str, error: str, **kw) -> None:
        if not _SUPPRESS_NATIVE_PRINT:
            with _local_term_write_lock:
                _orig_print_tool_error(tool_name, error, **kw)
        if not _in_relayed_capture():
            _effective_output_bridge(bridge).emit(AgentEvent(
                type=EventType.TOOL_ERROR,
                turn_id=_tid(),
                data={"tool_name": tool_name, "message": str(error)},
            ))
    mod.print_tool_error = _print_tool_error

    # ── print_info / print_warning ────────────────────────────────────────
    for _fname, _etype in [("print_info", EventType.INFO), ("print_warning", EventType.WARNING)]:
        _orig = getattr(mod, _fname)
        def _make(orig, etype):
            def _patched(msg: str, **kw) -> None:
                if not _SUPPRESS_NATIVE_PRINT:
                    with _local_term_write_lock:
                        orig(msg, **kw)
                if not _in_relayed_capture():
                    # [FIX] 之前这里没有带 turn_id——任何在 run_turn() 期间
                    # 调用的 R.print_info()/R.print_warning()（比如
                    # "正在后台生成会话摘要 / 更新长期记忆..."）广播出去的
                    # 事件都没有归属到当前这一轮。后果有两个：
                    #   1. /v1/stream/{turn_id} 的过滤规则对没有 turn_id 的
                    #      事件不生效（视为"不过滤"），这类事件会被每一次
                    #      新 turn 的 per-turn SSE 回放重新放送一遍，越到
                    #      后面回放的历史 info/warning 越多。
                    #   2. cli/daemon.py 的 observer 线程用 turn_id 判断
                    #      "这是不是我自己这一轮的事件"，没有 turn_id 会被
                    #      误判成"别的客户端发的"，不但重复显示、带上多余
                    #      的"[其他终端]"前缀，还会把 connected 客户端主路径
                    #      的 `_own_printed_any_holder` 复位，导致它随后在
                    #      turn_done 时又把已经流式显示过的回复内容当成
                    #      "本轮没有流过 token"重新整段打印一遍（表现为
                    #      "最后一条回复重复了两次"）。
                    _effective_output_bridge(bridge).emit(AgentEvent(
                        type=etype, turn_id=_tid(), data={"message": str(msg)}
                    ))
            return _patched
        setattr(mod, _fname, _make(_orig, _etype))

    _print_to_term("[dim]✓ HTTP output hook installed[/dim]")