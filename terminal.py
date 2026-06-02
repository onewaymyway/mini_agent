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

    def update_statusbar(self, lines: list[str]) -> None:
        self._q.put(_Msg("statusbar", lines))

    def redraw_statusbar(self) -> None:
        self._q.put(_Msg("redraw", None))

    # ═══════════════════════════════════════════════════════════════════════
    # 输入通道（阻塞，主线程调用）
    # ═══════════════════════════════════════════════════════════════════════

    def prompt_user(self, prompt_text: str = "") -> str:
        """
        REPL 用户输入。
        阻塞前确保屏幕上没有状态栏干扰，输入完成后恢复状态栏。
        """
        self._enter_input_mode()
        try:
            return self._read_line(prompt_text)
        finally:
            self._exit_input_mode()

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
        """
        # 进入输入模式：暂停刷新，等队列清空（包括上面 term.print 的提示内容），
        # 然后擦状态栏。此时屏幕上已经有提示文字，且不会被任何刷新覆盖。
        self._enter_input_mode()
        try:
            # 打印选项提示符（直接写，不经队列——此时渲染线程已空闲）
            sys.stdout.write(f"  {choices} : ")
            sys.stdout.flush()
            try:
                choice = input().strip().lower() or default
            except (EOFError, KeyboardInterrupt):
                choice = "n"
            return choice
        finally:
            self._exit_input_mode()

    # ── 输入模式管理 ──────────────────────────────────────────────────────

    def _enter_input_mode(self) -> None:
        """
        进入阻塞输入前的准备：
        1. 暂停刷新线程（停止投递 _refresh）
        2. 等待队列完全清空（渲染线程处理完所有消息，含提示文字）
        3. 直接擦除状态栏（不经队列，此时渲染线程空闲安全）
        """
        self._refresh_paused.set()
        # 等刷新线程当前 sleep 周期结束（最多等一个周期，确保它不再投递新消息）
        time.sleep(self._refresh_interval + 0.05)
        # 等队列清空（渲染线程把所有消息处理完）
        self._q.join()
        # 此时渲染线程在 queue.Empty 等待，刷新线程不投新消息，安全直接写屏幕
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
                continue
            try:
                self._handle(msg)
            finally:
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
        while not self._refresh_stop.is_set():
            time.sleep(self._refresh_interval)
            if not self._refresh_paused.is_set() and not self._refresh_stop.is_set():
                self._q.put(_Msg("_refresh", None))

    # ── 用户输入底层 ──────────────────────────────────────────────────────

    def _read_line(self, prompt_text: str = "") -> str:
        """使用 prompt_toolkit 或降级 input() 读取一行。"""
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.formatted_text import HTML
            from prompt_toolkit.styles import Style
            from prompt_toolkit.history import InMemoryHistory
            from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
            from prompt_toolkit.completion import WordCompleter

            if not hasattr(self, "_ptk_session"):
                self._ptk_session = PromptSession(
                    history=InMemoryHistory(),
                    auto_suggest=AutoSuggestFromHistory(),
                    completer=WordCompleter(_SLASH_COMPLETIONS, sentence=True, ignore_case=True),
                    complete_while_typing=True,
                    enable_history_search=True,
                    mouse_support=False,
                )

            html_prompt = prompt_text or HTML(
                "<b><ansgreen>You</ansgreen></b><ansicyan> ❯ </ansicyan>"
            )
            result = self._ptk_session.prompt(
                html_prompt,
                style=Style.from_dict({"ansgreen": "bold #00cc00", "ansicyan": "bold #00cccc"}),
            )
            return (result or "").strip()
        except ImportError:
            pass
        except Exception:
            pass

        # 降级：普通 input
        if prompt_text:
            sys.stdout.write(str(prompt_text))
        else:
            sys.stdout.write("\n\033[1;32mYou\033[0m\033[1;36m ❯ \033[0m")
        sys.stdout.flush()
        return input().strip()

    def stop(self) -> None:
        """程序退出时调用。"""
        self._refresh_stop.set()
        self._q.join()
        self._erase_bar_direct()


class _StreamCtx:
    def __init__(self, t: Terminal): self._t = t
    def __enter__(self) -> Callable[[str], None]:
        return self._t.stream_token
    def __exit__(self, *_): self._t.stream_end()


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
