"""
terminal.py — 统一终端 I/O 管理器

所有命令行输出和输入都必须通过这里。

核心结构：
  - 渲染线程（render_thread）：唯一写屏幕的线程，串行处理队列消息
  - 刷新线程（refresh_thread）：每 250ms 投递 _refresh 消息，触发状态栏刷新
  - 主线程（或任意线程）：通过 term.print() / term.stream_token() 等投递消息

输入（阻塞操作）通过特殊流程处理：
  1. 把询问文字作为普通 print 消息投入队列（保证串行渲染，不被状态栏覆盖）
  2. 暂停刷新线程（不再投 _refresh）
  3. 等队列完全清空（渲染线程处理完所有消息，包括询问文字）
  4. 擦除状态栏，打印输入提示符
  5. 阻塞等待用户输入（此时渲染线程空闲，刷新线程暂停，不会有任何写屏幕操作）
  6. 用户输入完成后恢复刷新
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Any, Callable, Iterator, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


_IS_TTY: bool = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class _Msg:
    __slots__ = ("kind", "payload")
    def __init__(self, kind: str, payload: Any = None):
        self.kind = kind
        self.payload = payload


class Terminal:
    """唯一的终端 I/O 管理器。通过模块级 `term` 单例访问。"""

    def __init__(self, status_refresh_hz: int = 4) -> None:
        self._console = Console(highlight=False)
        self._q: queue.Queue[_Msg] = queue.Queue()
        self._statusbar_lines: list[str] = []
        self._bar_drawn: int = 0
        self._streaming: bool = False
        self._stream_had_output: bool = False
        self._render_stop: bool = False
        # HTTP 注入支持：持久后台 ptk loop + session（由 _read_line 懒初始化）
        self._ptk_loop: Optional[Any]    = None   # asyncio 后台事件循环
        self._ptk_session: Optional[Any] = None   # PromptSession（持久复用）
        self._inject_queue: Optional[Any] = None  # asyncio.Queue[str]（在 ptk loop 里）

        # 状态栏内容提供者回调（由 status_bar 模块注册）
        # 架构改进：Terminal 自己在刷新周期内调用回调拉取内容，
        # 而不是由外部线程主动 push update+redraw 两条消息。
        # 这样 _refresh_paused 只需一处检查，消除了 push_loop 与
        # _enter_input_mode 之间的竞态窗口。
        self._statusbar_provider: Optional[Callable[[], list[str]]] = None

        # 渲染线程：唯一写屏幕的线程
        self._render_thread = threading.Thread(
            target=self._render_loop, daemon=True, name="terminal-render"
        )
        self._render_thread.start()

        # 刷新线程控制
        self._refresh_interval = 1.0 / max(1, status_refresh_hz)
        self._refresh_stop = threading.Event()
        self._refresh_paused = threading.Event()   # set = 暂停投递 _refresh 消息
        self._refresh_thread = threading.Thread(
            target=self._refresh_loop, daemon=True, name="terminal-refresh"
        )
        self._refresh_thread.start()

    # ═══════════════════════════════════════════════════════════════════════
    # 输出通道（线程安全）
    # ═══════════════════════════════════════════════════════════════════════

    def print(self, *args, **kwargs) -> None:
        self._q.put(_Msg("print", (args, kwargs)))

    def rule(self, title: str = "", **kwargs) -> None:
        self._q.put(_Msg("rule", (title, kwargs)))

    def panel(self, content: Any, **kwargs) -> None:
        self._q.put(_Msg("panel", (content, kwargs)))

    def syntax(self, code: str, language: str, **kwargs) -> None:
        self._q.put(_Msg("syntax", (code, language, kwargs)))

    def markdown(self, text: str) -> None:
        self._q.put(_Msg("markdown", text))

    def debug(self, msg: str, *, prefix: str = "🔍") -> None:
        self._q.put(_Msg("print", ((f"[dim]{prefix} {msg}[/dim]",), {})))

    # ── 流式输出 ──────────────────────────────────────────────────────────

    def stream_token(self, token: str) -> None:
        self._q.put(_Msg("stream", token))

    def stream_end(self) -> None:
        self._q.put(_Msg("stream_end", None))

    def streaming(self):
        return _StreamCtx(self)

    # ── 状态栏 ────────────────────────────────────────────────────────────

    def set_statusbar_provider(self, provider: "Optional[Callable[[], list[str]]]") -> None:
        """
        注册状态栏内容提供者回调。
        刷新线程每个周期调用 provider() 拉取最新内容，然后自己决定是否重绘。
        传入 None 清除提供者（停止状态栏）。

        取代旧的 update_statusbar + redraw_statusbar 两步推送模式：
        旧模式下 status_bar._push_loop 每 250ms 向队列投入两条消息，
        与 _enter_input_mode 存在竞态；新模式下内容拉取完全在 _refresh_loop
        内部完成，_refresh_paused 一个标志即可彻底静止所有状态栏活动。
        """
        self._statusbar_provider = provider

    def update_statusbar(self, lines: list[str]) -> None:
        """向后兼容接口：直接设置状态栏内容（不经回调）。"""
        self._q.put(_Msg("statusbar", lines))

    def redraw_statusbar(self) -> None:
        """向后兼容接口：触发一次状态栏重绘。"""
        self._q.put(_Msg("redraw", None))

    # ═══════════════════════════════════════════════════════════════════════
    # 输入通道（阻塞，主线程调用）
    # ═══════════════════════════════════════════════════════════════════════

    def prompt_user(self, prompt_text: str = "") -> str:
        """
        REPL 用户输入。支持两种输入源竞速：
          1. HTTP 注入（_inject_queue）—— 外部线程 call_soon_threadsafe 写入
          2. 键盘输入（prompt_toolkit prompt_async）
        哪个先到就用哪个，完全等同于用户在键盘上输入。
        """
        self._enter_input_mode()
        try:
            return self._read_line(prompt_text)
        finally:
            self._exit_input_mode()

    def inject_input(self, text: str) -> None:
        """
        从任意线程注入一行输入（HTTP 后台线程调用）。
        通过 ptk 的 buffer + validate_and_handle 模拟用户键入文字并按回车：
          - ptk 自己负责回显（显示 'You ❯ <text>'）
          - 提示符只渲染一次，无双重 You ❯
          - 效果与真实键盘输入完全一致
        如果 ptk session/loop 尚未就绪，缓存到 _inject_pending 等待。
        """
        loop    = self._ptk_loop
        session = self._ptk_session

        if loop is None or session is None or not loop.is_running():
            if not hasattr(self, "_inject_pending"):
                self._inject_pending: list[str] = []
            self._inject_pending.append(text)
            return

        def _do_inject():
            app = session.app
            if app and app.is_running:
                from prompt_toolkit.document import Document
                buf = app.current_buffer
                buf.set_document(Document(text=text, cursor_position=len(text)))
                buf.validate_and_handle()
            else:
                # app 尚未运行（prompt_async 还没启动），等 50ms 再试
                import asyncio
                async def _retry():
                    await asyncio.sleep(0.05)
                    _do_inject()
                asyncio.ensure_future(_retry(), loop=loop)

        loop.call_soon_threadsafe(_do_inject)

    def force_end_stream(self) -> None:
        """
        强制结束流式状态。当流式输出因异常中断时调用，
        确保 _streaming 标志重置，后续输入/输出不受影响。
        """
        self._q.put(_Msg("_force_end_stream", None))

    def confirm(
        self,
        prompt_lines: list[str],
        choices: str = "(y)es  (a)lways  (n)o  (d)eny-always",
        default: str = "y",
    ) -> str:
        """
        审批/确认输入。

        prompt_lines: 已经通过 term.print() 输出的提示内容（询问文字）。
                      调用方应先 term.print() 输出提示，再调用 confirm()。
                      confirm() 只负责显示选项提示符并读取输入。

        返回用户输入的小写字符串。

        修复：
        1. _enter_input_mode 改为双哨兵，确保 status_bar push_loop 在
           _refresh_paused set 后已投入的残余 redraw 消息被彻底排空，
           输入等待期间渲染线程不再写屏幕。
        2. readline() 读取后补一个换行，确保光标在新行，
           _exit_input_mode 的 redraw 从正确位置开始绘制状态栏。
        """
        # 进入输入模式：暂停刷新，双哨兵排空队列，擦状态栏
        self._enter_input_mode()
        try:
            # 打印选项提示符（直接写，不经队列——此时渲染线程已空闲）
            sys.stdout.write(f"  {choices} : ")
            sys.stdout.flush()
            try:
                line = sys.stdin.readline()
                # readline() 已包含 \n（用户按回车），但 EOF 时返回 ""
                # 补一个换行保证光标在新行，状态栏重绘位置正确
                if not line.endswith("\n"):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                choice = line.strip().lower() if line else default
                choice = choice or default
            except (EOFError, KeyboardInterrupt):
                sys.stdout.write("\n")
                sys.stdout.flush()
                choice = "n"
            return choice
        finally:
            self._exit_input_mode()

    # ── 输入模式管理 ──────────────────────────────────────────────────────

    def _enter_input_mode(self) -> None:
        """
        进入阻塞输入前的准备：
        1. 设置暂停标志（_refresh_paused），通知 refresh_thread 和 status_bar
           push_loop 停止向队列投递新消息
        2. 投双重哨兵 + join，彻底排空队列（含提示文字 + pause 前可能已入队的
           残余 _refresh / redraw 消息）
        3. 直接擦除状态栏（此时渲染线程空闲，安全直接写屏幕）

        双重哨兵原因：
          设置 _refresh_paused 后，status_bar._push_loop 可能正处于
          sleep 结束、检查标志之前的窗口，已向队列投入 update_statusbar +
          redraw_statusbar 两条消息。第一个哨兵消费完这些残余消息后，
          push_loop 在下一轮 sleep 结束前不会再投新消息；但若 push_loop 恰好
          在第一个哨兵入队前完成了检查（标志已 set，但消息已在队列），
          第二个哨兵确保渲染线程真正空闲、无残余 redraw 待处理。
        """
        # 1. 告知所有后台线程停止向队列投递消息
        self._refresh_paused.set()
        # 2a. 第一个哨兵：排空 pause 前可能已入队的残余消息（含提示文字）
        self._q.put(_Msg("_noop", None))
        self._q.join()
        # 2b. 第二个哨兵：确认渲染线程在处理完所有残余 redraw 后真正空闲
        self._q.put(_Msg("_noop", None))
        self._q.join()
        # 3. 此时渲染线程空闲，无任何后台线程会写屏幕，安全直接操作
        self._erase_bar_direct()

    def _exit_input_mode(self) -> None:
        """输入完成后：恢复刷新，重绘状态栏。"""
        self._refresh_paused.clear()
        # 重绘通过队列（让渲染线程来画）
        self._q.put(_Msg("redraw", None))

    # ═══════════════════════════════════════════════════════════════════════
    # 渲染循环（render_thread）
    # ═══════════════════════════════════════════════════════════════════════

    def _render_loop(self) -> None:
        while True:
            try:
                msg = self._q.get(timeout=0.5)
            except queue.Empty:
                if self._render_stop:
                    break
                continue
            try:
                if msg.kind == "_stop":
                    self._q.task_done()
                    break
                self._handle(msg)
            finally:
                if msg.kind != "_stop":
                    self._q.task_done()

    def _handle(self, msg: _Msg) -> None:
        kind = msg.kind

        if kind == "print":
            args, kwargs = msg.payload
            self._erase_bar()
            self._console.print(*args, **kwargs)
            self._draw_bar()

        elif kind == "rule":
            title, kwargs = msg.payload
            self._erase_bar()
            self._console.rule(title, **kwargs)
            self._draw_bar()

        elif kind == "panel":
            content, kwargs = msg.payload
            self._erase_bar()
            self._console.print(Panel(content, **kwargs))
            self._draw_bar()

        elif kind == "syntax":
            code, language, kwargs = msg.payload
            self._erase_bar()
            self._console.print(Syntax(code, language, **kwargs))
            self._draw_bar()

        elif kind == "markdown":
            self._erase_bar()
            self._console.print(Markdown(msg.payload))
            self._draw_bar()

        elif kind == "stream":
            token = msg.payload
            filtered = self._filter_token(token)
            if filtered:
                if not self._streaming:
                    self._erase_bar()
                    self._streaming = True
                    self._stream_had_output = True
                sys.stdout.write(filtered)
                sys.stdout.flush()

        elif kind == "stream_end":
            # 把过滤器里缓冲的最后几个字符也打印出来（避免末尾内容丢失）
            if self._pending_stream:
                sys.stdout.write(self._pending_stream)
                self._stream_had_output = True
            if self._stream_had_output:
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._draw_bar()
            self._streaming = False
            self._stream_had_output = False
            self._stream_filter_reset()

        elif kind == "statusbar":
            self._statusbar_lines = msg.payload

        elif kind == "redraw":
            if not self._streaming:
                self._erase_bar()
                self._draw_bar()

        elif kind == "_refresh":
            if not self._streaming:
                self._erase_bar()
                self._draw_bar()

        elif kind == "_noop":
            pass  # 哨兵消息，仅用于同步等待队列清空

        elif kind == "_force_end_stream":
            # 强制结束流式状态（异常恢复时使用）
            if self._streaming or self._stream_had_output:
                if self._pending_stream:
                    sys.stdout.write(self._pending_stream)
                    self._stream_had_output = True
                if self._stream_had_output:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                self._streaming = False
                self._stream_had_output = False
                self._stream_filter_reset()
                self._draw_bar()

    # ── 状态栏绘制（仅在 render_thread 中调用）───────────────────────────

    def _draw_bar(self) -> None:
        if not _IS_TTY or not self._statusbar_lines:
            return
        out = sys.stdout
        if self._bar_drawn > 0:
            out.write(f"\x1b[{self._bar_drawn}A\x1b[0J")
        for line in self._statusbar_lines:
            out.write(line + "\n")
        out.flush()
        self._bar_drawn = len(self._statusbar_lines)

    def _erase_bar(self) -> None:
        if not _IS_TTY or self._bar_drawn == 0:
            return
        sys.stdout.write(f"\x1b[{self._bar_drawn}A\x1b[0J")
        sys.stdout.flush()
        self._bar_drawn = 0

    # ── 状态栏操作（主线程直接调用，仅在队列空闲时安全）─────────────────

    def _erase_bar_direct(self) -> None:
        if not _IS_TTY or self._bar_drawn == 0:
            return
        sys.stdout.write(f"\x1b[{self._bar_drawn}A\x1b[0J")
        sys.stdout.flush()
        self._bar_drawn = 0

    # ── 流式 token 过滤（过滤 <tool_use> 块）────────────────────────────

    _suppress_stream: bool = False
    _pending_stream: str = ""

    def _filter_token(self, token: str) -> str:
        result = []
        text = self._pending_stream + token
        self._pending_stream = ""
        i = 0
        while i < len(text):
            if self._suppress_stream:
                end = text.find("</tool_use>", i)
                if end == -1:
                    tail = text[i:]
                    self._pending_stream = tail if len(tail) <= 11 else ""
                    i = len(text)
                else:
                    self._suppress_stream = False
                    i = end + len("</tool_use>")
            else:
                start = text.find("<tool_use>", i)
                if start == -1:
                    visible = text[i:]
                    if len(visible) > 10:
                        result.append(visible[:-10])
                        self._pending_stream = visible[-10:]
                    else:
                        self._pending_stream = visible
                    i = len(text)
                elif start > i:
                    result.append(text[i:start])
                    self._suppress_stream = True
                    i = start + len("<tool_use>")
                else:
                    self._suppress_stream = True
                    i = start + len("<tool_use>")
        return "".join(result)

    def _stream_filter_reset(self) -> None:
        self._suppress_stream = False
        self._pending_stream = ""

    # ── 刷新循环（refresh_thread）────────────────────────────────────────

    def _refresh_loop(self) -> None:
        """
        刷新循环：每个周期检查 _refresh_paused；
        若未暂停，调用 _statusbar_provider 拉取最新内容，
        然后投递一条 _refresh 消息（携带内容）到渲染队列。

        架构优势：所有状态栏活动（内容拉取 + 重绘）都在同一个
        "是否暂停"判断分支内完成，_enter_input_mode 设置
        _refresh_paused 后，下一个刷新周期绝对不会产生任何
        与状态栏相关的队列消息，彻底消除竞态。
        """
        while not self._refresh_stop.is_set():
            time.sleep(self._refresh_interval)
            if self._refresh_paused.is_set() or self._refresh_stop.is_set():
                continue
            # 在 refresh_thread 中拉取内容（可以调用任意函数，不写屏幕）
            lines: list[str] = []
            provider = self._statusbar_provider
            if provider is not None:
                try:
                    lines = provider()
                except Exception:
                    lines = []
                # 同步更新状态栏内容缓存（通过队列，确保 render_thread 安全读写）
                if lines:
                    self._q.put(_Msg("statusbar", lines))
            self._q.put(_Msg("_refresh", None))

    # ── 用户输入底层 ──────────────────────────────────────────────────────

    def _read_line(self, prompt_text: str = "") -> str:
        """
        读取一行用户输入。

        使用持久后台 asyncio 事件循环运行 prompt_toolkit prompt_async。
        HTTP 注入通过 inject_input() 直接写入 ptk buffer 并模拟回车，
        ptk 自己负责回显，提示符只渲染一次，两个 You ❯ 问题彻底解决。
        多行粘贴由 prompt_toolkit 原生处理，不受影响。
        """
        if not getattr(self, "_ptk_failed", False):
            try:
                import asyncio
                import concurrent.futures
                from prompt_toolkit import PromptSession
                from prompt_toolkit.formatted_text import HTML
                from prompt_toolkit.styles import Style
                from prompt_toolkit.history import InMemoryHistory
                from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
                from prompt_toolkit.completion import WordCompleter

                # 懒初始化持久后台事件循环（只创建一次，跨多轮输入复用）
                if self._ptk_loop is None:
                    self._ptk_loop = asyncio.new_event_loop()
                    t = threading.Thread(
                        target=self._ptk_loop.run_forever,
                        daemon=True,
                        name="ptk-event-loop",
                    )
                    t.start()

                # 懒初始化 PromptSession（只创建一次，状态持久）
                if self._ptk_session is None:
                    self._ptk_session = PromptSession(
                        history=InMemoryHistory(),
                        auto_suggest=AutoSuggestFromHistory(),
                        completer=WordCompleter(
                            _SLASH_COMPLETIONS, sentence=True, ignore_case=True
                        ),
                        complete_while_typing=True,
                        enable_history_search=True,
                        mouse_support=False,
                    )
                    # 消费 inject_input() 在 session 就绪前缓存的消息
                    # inject_input() 的重试机制会自动处理，此处清空缓存即可
                    self._inject_pending = getattr(self, "_inject_pending", [])
                    # 这些消息会在 inject_input 下次 retry 时处理

                html_prompt = prompt_text or HTML(
                    "<b><ansgreen>You</ansgreen></b><ansicyan> ❯ </ansicyan>"
                )
                style = Style.from_dict(
                    {"ansgreen": "bold #00cc00", "ansicyan": "bold #00cccc"}
                )

                async def _do_prompt() -> str:
                    """在持久后台 loop 里运行 prompt_async。"""
                    result = await self._ptk_session.prompt_async(html_prompt, style=style)
                    return (result or "").strip()

                # 在后台 loop 里运行，主线程阻塞等待结果
                future = asyncio.run_coroutine_threadsafe(_do_prompt(), self._ptk_loop)
                return future.result()

            except ImportError:
                pass
            except (KeyboardInterrupt, EOFError):
                raise
            except Exception:
                self._ptk_failed = True

        # 降级：直接写提示符 + readline（不支持注入，但至少能用）
        sys.stdout.write("\n")
        if prompt_text:
            sys.stdout.write(str(prompt_text))
        else:
            sys.stdout.write("\033[1;32mYou\033[0m\033[1;36m ❯ \033[0m")
        sys.stdout.flush()
        try:
            line = sys.stdin.readline()
            return line.strip() if line else ""
        except (EOFError, KeyboardInterrupt):
            raise

    def stop(self) -> None:
        """程序退出时调用，优雅关闭后台线程，避免 daemon 线程在解释器关闭时
        争抢 stdout 锁导致 Fatal Python error: _enter_buffered_busy。"""
        # 1. 先停刷新线程，防止它继续往队列里投消息
        self._refresh_stop.set()
        self._refresh_paused.set()   # 防止 refresh_loop 卡在 paused.wait
        self._refresh_thread.join(timeout=1.0)

        # 2. 向渲染线程投哨兵，通知它退出
        self._render_stop = True
        self._q.put(_Msg("_stop", None))

        # 3. 等待渲染线程处理完队列中所有剩余消息（包括哨兵）后退出
        self._render_thread.join(timeout=2.0)

        # 4. 最后擦除状态栏（渲染线程已停，直接写 stdout 安全）
        self._erase_bar_direct()


class _StreamCtx:
    def __init__(self, t: Terminal): self._t = t
    def __enter__(self) -> Callable[[str], None]:
        return self._t.stream_token
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # 异常中断时，强制结束流式状态，确保 _streaming 不泄漏
            self._t.force_end_stream()
        else:
            self._t.stream_end()
        return False  # 不吞异常


# ── slash 命令补全列表 ────────────────────────────────────────────────────────

_SLASH_COMPLETIONS = [
    "/help", "/clear",
    "/skills", "/skill on", "/skill off",
    "/stats", "/verbose",
    "/model", "/compact", "/prompts",
    "/tasks", "/tasks dashboard", "/tasks log", "/tasks cancel",
    "/tasks cancel-all", "/tasks workers",
    "/plan", "/plan clear", "/plan summary",
    "/concurrency", "/concurrency tasks", "/concurrency llm", "/cc",
    "/provider", "/provider list", "/provider switch",
    "/session", "/session list", "/session new",
    "/exit", "/quit",
]


# ── 模块级单例 ────────────────────────────────────────────────────────────────

term: Terminal = Terminal()


def get_terminal() -> Terminal:
    return term