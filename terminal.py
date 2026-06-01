"""
terminal.py — 统一终端 I/O 管理器

所有命令行输出和输入都必须通过这里，不允许直接调用
sys.stdout.write / print / Console / input 等。

┌─────────────────────────────────────────────────────────────────┐
│                        Terminal 类                              │
│                                                                 │
│  输出通道                          输入通道                     │
│  ──────────────────────            ────────────────             │
│  stream()      LLM流式输出          prompt_user()  REPL输入    │
│  print()       普通单行输出         confirm()      y/n 确认    │
│  panel()       Rich Panel           choose()       选项选择    │
│  rule()        分隔线                                           │
│  debug()       调试信息                                         │
│  status_bar()  状态栏刷新                                       │
│                                                                 │
│  渲染器                                                         │
│  ──────────────────────                                         │
│  _render_queue  所有输出排队，主线程串行消费                     │
│  _statusbar     当前状态栏内容（N行ANSI字符串）                  │
└─────────────────────────────────────────────────────────────────┘

设计原则：
  1. 单一写者：所有内容都写 stdout，通过队列串行化
  2. 状态栏常驻底部：每次有内容输出时，先擦状态栏，输出内容，再重绘
  3. 等待输入时：擦除状态栏，显示提示符，输入完成后重绘
  4. 后台线程：仅负责把队列里的渲染任务消费掉，不直接写终端
     例外：status_bar 刷新是唯一由计时器触发的写操作，
           通过队列投递，不直接写，保证串行

队列消息类型（内部）：
  ("print",   payload)   — 普通内容，包在 erase/redraw 之间输出
  ("stream",  token)     — 流式 token，直接写（不重绘，batch 结束再重绘）
  ("stream_end", "")     — 流结束，重绘状态栏
  ("statusbar", lines)   — 更新状态栏内容（不触发立刻重绘，下次输出时生效）
  ("redraw",  "")        — 仅重绘状态栏（用于 task 状态变更后）
  ("pause",   ev)        — 擦除状态栏，set(ev) 通知调用方可以开始读输入
  ("resume",  "")        — 重绘状态栏

所有 public 方法都是线程安全的（通过队列）。
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich import box as rbox

# ── 终端能力检测 ──────────────────────────────────────────────────────────────

_IS_TTY: bool = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

# ── 消息类型 ──────────────────────────────────────────────────────────────────

@dataclass
class _Msg:
    kind: str
    payload: Any = None


# ── 核心类 ────────────────────────────────────────────────────────────────────

class Terminal:
    """
    统一终端 I/O。整个进程只应有一个实例（通过模块级 `term` 访问）。

    状态栏刷新频率由 status_refresh_hz 控制（默认 4Hz）。
    如果不在 TTY 环境（如重定向到文件），状态栏自动禁用。
    """

    def __init__(self, status_refresh_hz: int = 4) -> None:
        self._console = Console(highlight=False)
        self._q: queue.Queue[_Msg] = queue.Queue()
        self._statusbar_lines: list[str] = []  # 当前状态栏内容
        self._bar_drawn: int = 0                # 已在屏幕上的状态栏行数
        self._streaming: bool = False           # 正在流式输出中
        self._stream_had_output: bool = False   # 本次流有可见输出

        # 渲染线程
        self._render_thread = threading.Thread(
            target=self._render_loop, daemon=True, name="terminal-render"
        )
        self._render_thread.start()

        # 状态栏定时刷新线程
        self._refresh_interval = 1.0 / max(1, status_refresh_hz)
        self._refresh_stop = threading.Event()
        self._refresh_paused = threading.Event()  # set = 暂停刷新
        self._refresh_thread = threading.Thread(
            target=self._refresh_loop, daemon=True, name="terminal-refresh"
        )
        self._refresh_thread.start()

    # ═══════════════════════════════════════════════════════════════════════
    # 输出通道（线程安全，可从任意线程调用）
    # ═══════════════════════════════════════════════════════════════════════

    def print(self, *args, **kwargs) -> None:
        """通用输出：支持 Rich markup，样式，end= 等所有 Console.print 参数。"""
        self._q.put(_Msg("print", (args, kwargs)))

    def rule(self, title: str = "", **kwargs) -> None:
        """水平分隔线。"""
        self._q.put(_Msg("rule", (title, kwargs)))

    def panel(self, content: Any, **kwargs) -> None:
        """Rich Panel 输出。"""
        self._q.put(_Msg("panel", (content, kwargs)))

    def syntax(self, code: str, language: str, **kwargs) -> None:
        """代码高亮输出。"""
        self._q.put(_Msg("syntax", (code, language, kwargs)))

    def markdown(self, text: str) -> None:
        """Markdown 渲染输出。"""
        self._q.put(_Msg("markdown", text))

    # ── 流式输出（LLM token by token）────────────────────────────────────────

    def stream_token(self, token: str) -> None:
        """
        投递一个流式 token。
        第一个 token 会擦除状态栏，后续 token 直接追加，不触发重绘。
        调用 stream_end() 结束后重绘状态栏。
        """
        self._q.put(_Msg("stream", token))

    def stream_end(self) -> None:
        """结束流式输出，重绘状态栏。"""
        self._q.put(_Msg("stream_end", None))

    @contextmanager
    def streaming(self) -> Iterator[Callable[[str], None]]:
        """
        上下文管理器版本：
            with term.streaming() as write:
                write(token)
        """
        try:
            yield self.stream_token
        finally:
            self.stream_end()

    # ── 状态栏 ────────────────────────────────────────────────────────────────

    def update_statusbar(self, lines: list[str]) -> None:
        """
        更新状态栏内容。不立刻重绘，等下次 redraw 或定时刷新时生效。
        lines 是纯 ANSI 字符串列表（每行一条）。
        """
        self._q.put(_Msg("statusbar", lines))

    def redraw_statusbar(self) -> None:
        """立刻重绘状态栏（task 状态变更后调用）。"""
        self._q.put(_Msg("redraw", None))

    # ── 调试输出 ──────────────────────────────────────────────────────────────

    def debug(self, msg: str, *, prefix: str = "🔍") -> None:
        """调试信息（dim 样式）。"""
        self._q.put(_Msg("print", ((f"[dim]{prefix} {msg}[/dim]",), {})))

    # ═══════════════════════════════════════════════════════════════════════
    # 输入通道（阻塞调用，必须在主线程使用）
    # ═══════════════════════════════════════════════════════════════════════

    def prompt_user(self, prompt_text: str = "") -> str:
        """
        显示输入提示符并读取用户输入。
        在读取前擦除状态栏，读取完成后重绘。
        返回已 strip 的字符串；EOFError / KeyboardInterrupt 向上抛出。
        """
        # 确保队列里所有待渲染内容先处理完
        self._flush_queue()
        # 擦除状态栏，暂停定时刷新
        self._pause_refresh()
        self._erase_bar_direct()
        try:
            return self._read_line(prompt_text)
        finally:
            self._resume_refresh()

    def confirm(
        self,
        message: str,
        choices: str = "(y)es  (a)lways  (n)o  (d)eny-always",
        default: str = "y",
    ) -> str:
        """
        显示审批提示，读取用户选择，返回小写的选择字符串。
        在读取前后自动管理状态栏。
        """
        self._flush_queue()
        self._pause_refresh()
        self._erase_bar_direct()
        try:
            self._write_direct(f"\n{message}\n  {choices} : ")
            try:
                choice = input().strip().lower() or default
            except (EOFError, KeyboardInterrupt):
                choice = "n"
            return choice
        finally:
            self._resume_refresh()

    # ═══════════════════════════════════════════════════════════════════════
    # 渲染循环（在 render_thread 中运行）
    # ═══════════════════════════════════════════════════════════════════════

    def _render_loop(self) -> None:
        while True:
            try:
                msg = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._handle(msg)
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
                    # 第一个可见 token：擦状态栏，打印空行
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
            # 由定时刷新线程投递，仅在非流式时重绘
            if not self._streaming:
                self._erase_bar()
                self._draw_bar()

    # ── 状态栏底层操作（仅在 render_thread 中调用）───────────────────────────

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

    # ── 状态栏底层操作（在主线程中直接操作，用于 prompt_user/confirm）────────

    def _erase_bar_direct(self) -> None:
        """直接在当前线程擦除状态栏（不通过队列）。"""
        if not _IS_TTY or self._bar_drawn == 0:
            return
        sys.stdout.write(f"\x1b[{self._bar_drawn}A\x1b[0J")
        sys.stdout.flush()
        self._bar_drawn = 0

    def _write_direct(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    # ── 流式 token 过滤（过滤 <tool_use> 块）────────────────────────────────

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

    # ── 定时刷新循环（status_refresh_thread）────────────────────────────────

    def _refresh_loop(self) -> None:
        while not self._refresh_stop.is_set():
            time.sleep(self._refresh_interval)
            if not self._refresh_paused.is_set() and not self._refresh_stop.is_set():
                self._q.put(_Msg("_refresh", None))

    def _pause_refresh(self) -> None:
        self._refresh_paused.set()
        # 等待队列清空，确保渲染线程不在中途写屏幕
        self._q.join()

    def _resume_refresh(self) -> None:
        # 重绘状态栏再恢复
        self._erase_bar_direct()
        # 通过队列重绘，让渲染线程来画
        self._refresh_paused.clear()
        self._q.put(_Msg("redraw", None))

    def _flush_queue(self) -> None:
        """等待队列中所有消息处理完。"""
        self._q.join()

    # ── 用户输入底层 ──────────────────────────────────────────────────────────

    def _read_line(self, prompt_text: str) -> str:
        """用 prompt_toolkit 或降级 input() 读取一行。"""
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
        """程序退出时调用，擦除状态栏。"""
        self._refresh_stop.set()
        self._flush_queue()
        self._erase_bar_direct()


# ── slash 命令自动补全列表 ────────────────────────────────────────────────────

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
