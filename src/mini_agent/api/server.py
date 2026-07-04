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

def _print_to_term(markup: str) -> None:
    """
    从后台线程安全地向终端队列投递一条 print 消息。
    使用 Rich markup，与 renderer 输出风格一致。
    出错时静默忽略，不影响 agent 执行。
    """
    try:
        from mini_agent.ui.terminal import term as _term
        _term.print(markup)
    except Exception:
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
        guard = getattr(agent, "permission_guard", None)
        if guard is None:
            # 尝试其他常见属性名
            guard = getattr(agent, "_permission_guard", None) or \
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
            from mini_agent.permissions import _SAFE_TOOLS
            needs_prompt = (
                not guard.auto_approve
                and tool_name not in _SAFE_TOOLS
                and tool_name not in guard._denied_tools
                and not guard._is_allowed(tool_name, tool_input)
            )
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
                    except Exception:
                        pass
            except Exception as e:
                self.init_error = e
                if self._on_crash is not None:
                    try:
                        self._on_crash(e)
                    except Exception:
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
                except Exception:
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
                continue

            turn_id = cmd.turn_id
            # daemon 多用户架构 Phase 1：从 enqueue() 时传入的 meta 里取 user_id，
            # 单用户模式下 cmd.meta 为空，user_id 就是 ""，行为不变。
            user_id = cmd.meta.get("user_id", "") if cmd.meta else ""
            role    = cmd.meta.get("role", "") if cmd.meta else ""
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

                    try:
                        result = _term_singleton.run_captured(_run_slash).strip()
                    except Exception as _cmd_e:
                        result = f"[error] command failed: {_cmd_e}"
                    if not result:
                        result = "(no output)"
                else:
                    result = bridge.agent.run_turn(cmd.message)

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
                    except Exception:
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
                    except Exception:
                        pass
            finally:
                bridge.set_state("idle", turn_id=None)
                if hasattr(bridge.agent, "_http_turn_id"):
                    bridge.agent._http_turn_id = ""

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
            except Exception:
                pass

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
        except Exception:
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
        except Exception:
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
        self._session_pool = None
        if multi_user_enabled:
            users_dir = project_root / ".agent" / "users"
            self._role_store = UserStore(users_dir)
            self._role_store.ensure_owner(configured_token=self._token)
            self._role_profile_mgr = RoleProfileManager(users_dir)

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
        else:
            self._self_message_bus = None

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
                except Exception:
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
        return getattr(bridge.agent, "_http_turn_id", "") if bridge.agent else ""

    # ── print_markdown（非流式回复的最终文本走这里）──────────────────────
    _orig_print_markdown = mod.print_markdown
    def _print_markdown(md: str) -> None:
        _orig_print_markdown(md)
        bridge.emit_token(md, turn_id=_tid())
    mod.print_markdown = _print_markdown

    # ── StreamWriter.write（流式 token）──────────────────────────────────
    _OrigStreamWriter = mod.StreamWriter
    class _PatchedStreamWriter(_OrigStreamWriter):
        def write(self, token: str) -> None:
            super().write(token)
            bridge.emit_token(token, turn_id=_tid())
    mod.StreamWriter = _PatchedStreamWriter

    # ── print_tool_call ───────────────────────────────────────────────────
    _orig_print_tool_call = mod.print_tool_call
    def _print_tool_call(tool_name: str, tool_input: dict, **kw) -> None:
        _orig_print_tool_call(tool_name, tool_input, **kw)
        bridge.emit_tool_call(tool_name, tool_input, turn_id=_tid())
    mod.print_tool_call = _print_tool_call

    # ── print_tool_result ─────────────────────────────────────────────────
    _orig_print_tool_result = mod.print_tool_result
    def _print_tool_result(tool_name: str, result: str, **kw) -> None:
        _orig_print_tool_result(tool_name, result, **kw)
        bridge.emit_tool_result(tool_name, str(result), turn_id=_tid())
    mod.print_tool_result = _print_tool_result

    # ── print_tool_error ──────────────────────────────────────────────────
    _orig_print_tool_error = mod.print_tool_error
    def _print_tool_error(tool_name: str, error: str, **kw) -> None:
        _orig_print_tool_error(tool_name, error, **kw)
        bridge.emit(AgentEvent(
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
                orig(msg, **kw)
                bridge.emit(AgentEvent(type=etype, data={"message": str(msg)}))
            return _patched
        setattr(mod, _fname, _make(_orig, _etype))

    _print_to_term("[dim]✓ HTTP output hook installed[/dim]")