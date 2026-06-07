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
from .routes import router


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


# ── AgentRunner ───────────────────────────────────────────────────────────────

class AgentRunner(threading.Thread):
    """
    后台线程：循环消费 InputQueue，驱动 agent.run_turn()。
    run_turn() 的所有输出（流式 token、工具调用等）通过
    OutputHook 拦截后广播到 HTTP 客户端。
    """

    def __init__(self, bridge: AgentBridge) -> None:
        super().__init__(name="agent-runner", daemon=True)
        self._bridge = bridge
        self._stop   = threading.Event()

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
                continue

            turn_id = cmd.turn_id
            bridge.set_state("running", turn_id=turn_id)
            bridge.emit_turn_start(turn_id, cmd.message)

            try:
                if bridge.agent is None:
                    raise RuntimeError("Agent not initialized")

                # ── 在终端模拟显示 Web 端发来的用户输入 ──────────────────
                # 让命令行侧看到 "You (web) ❯ <message>"，与正常 REPL 输入体验一致
                _print_to_term(
                    f"\n[bold green]You (web)[/bold green][bold cyan] ❯ [/bold cyan]{cmd.message}"
                )

                # 注入 turn_id，让 OutputHook 知道当前轮
                bridge.agent._http_turn_id = turn_id

                result = bridge.agent.run_turn(cmd.message)

                iq.mark_done(turn_id)
                bridge.emit_turn_done(turn_id, text=result or "")

            except Exception as e:
                tb = traceback.format_exc()
                iq.mark_error(turn_id)
                bridge.emit_error(f"{type(e).__name__}: {e}\n{tb}", turn_id=turn_id)
            finally:
                bridge.set_state("idle", turn_id=None)
                if hasattr(bridge.agent, "_http_turn_id"):
                    bridge.agent._http_turn_id = ""

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
    app.state.bridge    = bridge
    app.state.fs_helper = fs_helper

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
    ) -> None:
        self._host = host
        self._port = port

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

        # AgentRunner（后台驱动 agent.run_turn）
        self._runner = AgentRunner(self._bridge)

        # uvicorn 服务线程
        self._server_thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

    @property
    def bridge(self) -> AgentBridge:
        return self._bridge

    @property
    def token(self) -> str:
        return self._token

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
        )

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
    Monkey-patch mini_agent.ui.renderer.Renderer 的输出方法，
    在原有终端输出的同时，把内容推入 bridge 广播给 HTTP 客户端。

    这样 agent.py 本身无需任何改动。
    """
    try:
        from mini_agent.ui import renderer as _renderer_mod
        R = _renderer_mod.R   # 全局 Renderer 单例
    except Exception:
        return

    # ── print_markdown（非流式模式下 assistant 最终回复走这里）────────────────
    _orig_print_markdown = getattr(_renderer_mod, "print_markdown", None)
    if _orig_print_markdown:
        def _patched_print_markdown(md: str) -> None:
            _orig_print_markdown(md)
            turn_id = getattr(bridge.agent, "_http_turn_id", "")
            # 把整段 markdown 作为一个 token 事件推出，让 HTTP 客户端能收到完整文本
            bridge.emit_token(md, turn_id=turn_id)
        _renderer_mod.print_markdown = _patched_print_markdown

    # ── stream token ──────────────────────────────────────────────────────
    _orig_stream_token = R.__class__.stream_token if hasattr(R.__class__, 'stream_token') else None

    # 通过 StreamWriter 拦截流式 token
    _OrigStreamWriter = getattr(R, "StreamWriter", None)
    if _OrigStreamWriter is not None:
        class _PatchedStreamWriter(_OrigStreamWriter):  # type: ignore[valid-type]
            def write(self, text: str) -> None:
                super().write(text)
                turn_id = getattr(bridge.agent, "_http_turn_id", "")
                bridge.emit_token(text, turn_id=turn_id)

        R.__class__.StreamWriter = _PatchedStreamWriter

    # ── tool call ─────────────────────────────────────────────────────────
    _orig_print_tool_call = getattr(R.__class__, "print_tool_call", None)
    if _orig_print_tool_call:
        def _patched_print_tool_call(self, name, inp, **kw):
            _orig_print_tool_call(self, name, inp, **kw)
            turn_id = getattr(bridge.agent, "_http_turn_id", "")
            bridge.emit_tool_call(name, inp, turn_id=turn_id)
        R.__class__.print_tool_call = _patched_print_tool_call

    # ── tool result ───────────────────────────────────────────────────────
    _orig_print_tool_result = getattr(R.__class__, "print_tool_result", None)
    if _orig_print_tool_result:
        def _patched_print_tool_result(self, name, result, **kw):
            _orig_print_tool_result(self, name, result, **kw)
            turn_id = getattr(bridge.agent, "_http_turn_id", "")
            bridge.emit_tool_result(name, str(result), turn_id=turn_id)
        R.__class__.print_tool_result = _patched_print_tool_result

    # ── tool error ────────────────────────────────────────────────────────
    _orig_print_tool_error = getattr(R.__class__, "print_tool_error", None)
    if _orig_print_tool_error:
        def _patched_print_tool_error(self, name, msg, **kw):
            _orig_print_tool_error(self, name, msg, **kw)
            turn_id = getattr(bridge.agent, "_http_turn_id", "")
            bridge.emit(AgentEvent(
                type=EventType.TOOL_ERROR,
                turn_id=turn_id,
                data={"tool_name": name, "message": str(msg)},
            ))
        R.__class__.print_tool_error = _patched_print_tool_error

    # ── print_info / print_warning ────────────────────────────────────────
    for method_name, evt_type in [
        ("print_info",    EventType.INFO),
        ("print_warning", EventType.WARNING),
    ]:
        _orig = getattr(R.__class__, method_name, None)
        if _orig:
            def _make_patched(orig, etype):
                def _patched(self, msg, **kw):
                    orig(self, msg, **kw)
                    bridge.emit(AgentEvent(type=etype, data={"message": str(msg)}))
                return _patched
            setattr(R.__class__, method_name, _make_patched(_orig, evt_type))