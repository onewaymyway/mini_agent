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
    ) -> None:
        super().__init__(name="agent-runner", daemon=True)
        self._bridge = bridge
        self._stop   = threading.Event()
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

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        bridge = self._bridge
        iq     = bridge.input_queue

        while not self._stop.is_set():
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

                result = bridge.agent.run_turn(cmd.message)

                iq.mark_done(turn_id)
                bridge.emit_turn_done(turn_id, text=result or "", user_id=user_id)

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


# ── FastAPI App 工厂 ──────────────────────────────────────────────────────────

def create_app(
    bridge:      AgentBridge,
    fs_helper:   FsHelper,
    token:       str,
    allowed_ips: list[str],
    cors_origins: list[str],
    role_store: Optional[UserStore] = None,
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
        version     = "1.0.0",
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
            "version": "1.0.0",
            "docs":    "/docs",
        }

    # ── 注入到 app.state ──────────────────────────────────────────────────
    app.state.bridge     = bridge
    app.state.fs_helper  = fs_helper
    app.state.role_store = role_store   # None = 单用户模式（Phase 1 未开启）

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
        self._multi_user_enabled = multi_user_enabled
        self._role_store: Optional[UserStore] = None
        self._role_profile_mgr: Optional[RoleProfileManager] = None
        if multi_user_enabled:
            users_dir = project_root / ".agent" / "users"
            self._role_store = UserStore(users_dir)
            self._role_store.ensure_owner(configured_token=self._token)
            self._role_profile_mgr = RoleProfileManager(users_dir)

        # daemon 多用户架构 Phase 2：把 RoleProfileManager 注入 remember_about_user
        # 工具（tools/user_memory.py）。多用户模式未开启时传 None，
        # is_available() 会返回 False，工具调用时直接给出友好提示，不会报错崩溃。
        from mini_agent.tools.user_memory import set_role_profile_manager
        set_role_profile_manager(self._role_profile_mgr)

        # Stage 9 §7.2: 初始化 AutonomousLoop（在 daemon 进程中挂载到 AgentRunner）
        self._autonomous_loop = self._build_autonomous_loop(agent)

        # AgentRunner（后台驱动 agent.run_turn），注入 AutonomousLoop + RoleProfileManager
        self._runner = AgentRunner(
            self._bridge,
            autonomous_loop=self._autonomous_loop,
            role_profile_mgr=self._role_profile_mgr,
        )

        # uvicorn 服务线程
        self._server_thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

    def _build_autonomous_loop(self, agent: Any):
        """
        Stage 9 §7.2: 构建 AutonomousLoop 实例。
        若依赖不满足（paths/cfg 不可用），返回 None（AgentRunner 降级为无自主能力）。
        """
        try:
            from mini_agent.evolution.autonomous_loop import AutonomousLoop
            from mini_agent.perception.goal_backlog import load_goal_backlog

            # 从 agent 拿到 paths 和 cfg
            paths = getattr(agent, "_paths", None)
            cfg = getattr(agent, "cfg", None)
            if paths is None or cfg is None:
                return None

            goal_backlog = load_goal_backlog(paths)

            return AutonomousLoop(
                goal_backlog=goal_backlog,
                input_queue=self._bridge.input_queue,
                paths=paths,
                cfg=cfg,
                tick_interval_seconds=60.0,  # 每分钟检查一次
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