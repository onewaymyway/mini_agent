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

                # 注入 turn_id，让 OutputHook 知道当前轮
                bridge.agent._http_turn_id = turn_id

                # HTTP 路径没有 REPL 的 prompt_user()，手动在终端回显用户输入
                try:
                    from mini_agent.ui.terminal import term
                    term.print(
                        f"\n[bold green]You[/bold green][cyan] ❯ [/cyan]{cmd.message}"
                    )
                except Exception:
                    pass

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
    Monkey-patch mini_agent.ui.renderer 模块级函数 + StreamWriter 类，
    在原有终端输出的同时把内容推入 bridge 广播给 HTTP 客户端。

    关键：renderer.py 里全是模块级函数（print_markdown、print_tool_call …）
    和模块级类（StreamWriter），没有任何 Renderer 实例（原 server.py 里
    `R = _renderer_mod.R` 会 AttributeError，导致整个 hook 静默失败）。
    正确做法是直接替换模块属性。
    """
    try:
        from mini_agent.ui import renderer as _mod
    except Exception:
        return

    def _turn_id() -> str:
        return getattr(bridge.agent, "_http_turn_id", "")

    # ── 1. 流式输出：patch StreamWriter.write ────────────────────────────
    # agent.py 每次流式回复都 new 一个 StreamWriter()，所以 patch 类本身即可。
    _OrigSW = _mod.StreamWriter

    class _PatchedStreamWriter(_OrigSW):
        def write(self, token: str) -> None:
            super().write(token)                       # 保持终端输出不变
            bridge.emit_token(token, turn_id=_turn_id())

    _mod.StreamWriter = _PatchedStreamWriter

    # ── 2. 非流式回复：patch print_markdown ──────────────────────────────
    # agent.py 非流式模式下调用 R.print_markdown(resp.text)，
    # 这里 R 是 `import mini_agent.ui.renderer as R`，即模块本身，
    # 调用的是模块级函数 print_markdown。
    _orig_print_markdown = _mod.print_markdown

    def _patched_print_markdown(md: str) -> None:
        _orig_print_markdown(md)
        bridge.emit_token(md, turn_id=_turn_id())

    _mod.print_markdown = _patched_print_markdown

    # ── 4. 工具调用 ───────────────────────────────────────────────────────
    _orig_tool_call = _mod.print_tool_call

    def _patched_print_tool_call(tool_name: str, tool_input: dict,
                                  verbose: bool = False) -> None:
        _orig_tool_call(tool_name, tool_input, verbose=verbose)
        bridge.emit_tool_call(tool_name, tool_input, turn_id=_turn_id())

    _mod.print_tool_call = _patched_print_tool_call

    # ── 5. 工具结果（不截断）─────────────────────────────────────────────
    _orig_tool_result = _mod.print_tool_result

    def _patched_print_tool_result(tool_name: str, result: str,
                                    truncate: int = 2000) -> None:
        _orig_tool_result(tool_name, result, truncate=truncate)
        # 事件推送完整结果，不截断
        bridge.emit_tool_result(tool_name, result, turn_id=_turn_id())

    _mod.print_tool_result = _patched_print_tool_result

    # ── 6. 工具错误 ───────────────────────────────────────────────────────
    _orig_tool_error = _mod.print_tool_error

    def _patched_print_tool_error(tool_name: str, error: str) -> None:
        _orig_tool_error(tool_name, error)
        bridge.emit(AgentEvent(
            type=EventType.TOOL_ERROR,
            turn_id=_turn_id(),
            data={"tool_name": tool_name, "message": error},
        ))

    _mod.print_tool_error = _patched_print_tool_error

    # ── 7. info / warning ─────────────────────────────────────────────────
    for _fn_name, _etype in [
        ("print_info",    EventType.INFO),
        ("print_warning", EventType.WARNING),
    ]:
        _orig_fn = getattr(_mod, _fn_name)

        def _make_patched_log(orig, etype):
            def _patched(msg: str) -> None:
                orig(msg)
                bridge.emit(AgentEvent(type=etype, data={"message": msg}))
            return _patched

        setattr(_mod, _fn_name, _make_patched_log(_orig_fn, _etype))