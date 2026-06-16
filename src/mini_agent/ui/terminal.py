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
        # 状态栏暂停标志：当最近一次 print 以 end="" 结尾（即光标停留在行中、
        # 尚未换行，例如 assistant 名字前缀）时设为 True，暂停状态栏的
        # erase/redraw，避免 \x1b[NA\x1b[0J 把同一行已输出的内容一并擦除。
        # 在下一次产生换行的输出（流式结束 / markdown 渲染等）时恢复为 False。
        self._bar_suspended: bool = False

        # 等待 LLM 响应时的状态栏扩展标志：
        # 当 _bar_suspended=True 时收到 _refresh，说明光标停在 "agent ❯ " 后面、
        # LLM 还没有任何输出。此时我们先输出 \n 把光标推到新行，然后正常绘制
        # 状态栏，并置此标志=True，告知后续 stream 的 erase 逻辑：
        # 需要额外向上移动一行（跨过 "agent ❯ " 那一行），再清除状态栏。
        # 首个 stream token 到来时此标志清零。
        self._bar_below_prefix: bool = False

        # 状态栏内容提供者回调（由 status_bar 模块注册）
        # 架构改进：Terminal 自己在刷新周期内调用回调拉取内容，
        # 而不是由外部线程主动 push update+redraw 两条消息。
        # 这样 _refresh_paused 只需一处检查，消除了 push_loop 与
        # _enter_input_mode 之间的竞态窗口。
        self._statusbar_provider: Optional[Callable[[], list[str]]] = None

        # ── Task 焦点状态 ────────────────────────────────────────────────
        # 当 _task_focus 非 None 时，主输出区被"接管"，刷新循环会把
        # 焦点 task 的最新日志增量打印到屏幕；agent 主输出则被暂存。
        self._task_focus: Optional[str] = None          # 当前焦点 task_id
        self._focus_log_offset: int = 0                 # 已渲染到的日志行数
        self._focus_lock = threading.Lock()             # 保护焦点状态变更

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
        # 焦点模式下主输出静默（焦点退出后日志仍完整保留在 session）
        if self._task_focus is not None:
            return
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

    # ── Task 焦点控制（线程安全）────────────────────────────────────────

    def set_task_focus(self, task_id: "Optional[str]") -> None:
        """
        设置/清除 task 焦点。

        task_id 非 None → 进入焦点模式：主输出区接管，实时打印该 task 日志。
        task_id 为 None → 退出焦点模式，主输出恢复正常。

        可从任意线程调用（通过消息队列转发到 render_thread）。
        """
        with self._focus_lock:
            old = self._task_focus
            self._task_focus = task_id
            self._focus_log_offset = 0
        if old != task_id:
            self._q.put(_Msg("_focus_change", (old, task_id)))

    def get_task_focus(self) -> "Optional[str]":
        """返回当前焦点 task_id，None 表示主视图模式。"""
        return self._task_focus

    def focus_next_task(self) -> None:
        """切换到下一个 task（tab 列表循环）。"""
        self._q.put(_Msg("_focus_cycle", +1))

    def focus_prev_task(self) -> None:
        """切换到上一个 task（tab 列表循环）。"""
        self._q.put(_Msg("_focus_cycle", -1))

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
        interrupt_event=None,
    ) -> str:
        """
        审批/确认输入。

        prompt_lines: 已经通过 term.print() 输出的提示内容（询问文字）。
                      调用方应先 term.print() 输出提示，再调用 confirm()。
                      confirm() 只负责显示选项提示符并读取输入。

        interrupt_event: 可选的 threading.Event。若设置，当该 Event 被 set()
                         时（例如 HTTP 端已先响应），readline 等待会被中断，
                         抛出 _InterruptedByHTTP（由调用方捕获）。

        返回用户输入的小写字符串。
        """
        import threading as _threading

        # 进入输入模式：暂停刷新，双哨兵排空队列，擦状态栏
        self._enter_input_mode()
        try:
            # 打印选项提示符（直接写，不经队列——此时渲染线程已空闲）
            sys.stdout.write(f"  {choices} : ")
            sys.stdout.flush()

            if interrupt_event is not None:
                # ── 可中断模式：用独立线程读 stdin，主线程等两者之一 ──────
                result_holder: list = []
                stdin_done = _threading.Event()

                def _read_stdin():
                    try:
                        line = sys.stdin.readline()
                        result_holder.append(line)
                    except Exception:
                        result_holder.append("")
                    finally:
                        stdin_done.set()

                reader = _threading.Thread(target=_read_stdin, daemon=True)
                reader.start()

                # 等待 stdin 读完 或 interrupt_event 先触发
                finished = _threading.Event()
                while not finished.is_set():
                    if stdin_done.wait(timeout=0.2):
                        finished.set()
                    elif interrupt_event.is_set():
                        # HTTP 端先响应了——打印换行保持终端整洁，然后抛出中断
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        # reader 线程还阻塞在 readline，但它是 daemon 线程，进程退出时自动清理
                        # 用户下次按回车时 readline 会返回，但调用方已经不在等了
                        try:
                            from mini_agent.permissions import _InterruptedByHTTP
                        except ImportError:
                            class _InterruptedByHTTP(Exception): pass
                        raise _InterruptedByHTTP()

                line = result_holder[0] if result_holder else ""
            else:
                # ── 普通模式：直接阻塞 readline ──────────────────────────
                try:
                    line = sys.stdin.readline()
                except (EOFError, KeyboardInterrupt):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return "n"

            # readline() 已包含 \n（用户按回车），但 EOF 时返回 ""
            # 补一个换行保证光标在新行，状态栏重绘位置正确
            if not line.endswith("\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
            choice = line.strip().lower() if line else default
            choice = choice or default
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
            if kwargs.get("end", "\n") == "":
                # 光标停在行中（如 "orzooo " 前缀），暂停状态栏重绘，
                # 等待后续 stream/markdown 产生换行后再恢复
                self._bar_suspended = True
            else:
                self._bar_suspended = False
                self._draw_bar()

        elif kind == "rule":
            title, kwargs = msg.payload
            self._erase_bar()
            self._console.rule(title, **kwargs)
            self._bar_suspended = False
            self._draw_bar()

        elif kind == "panel":
            content, kwargs = msg.payload
            self._erase_bar()
            self._console.print(Panel(content, **kwargs))
            self._bar_suspended = False
            self._draw_bar()

        elif kind == "syntax":
            code, language, kwargs = msg.payload
            self._erase_bar()
            self._console.print(Syntax(code, language, **kwargs))
            self._bar_suspended = False
            self._draw_bar()

        elif kind == "markdown":
            self._erase_bar()
            self._console.print(Markdown(msg.payload))
            self._bar_suspended = False
            self._draw_bar()

        elif kind == "stream":
            token = msg.payload
            filtered = self._filter_token(token)
            if filtered:
                if not self._streaming:
                    if self._bar_below_prefix:
                        # 状态栏画在了 "agent ❯ " 下方，需先擦除状态栏，
                        # 然后再向上移动一行回到 "agent ❯ " 那行末尾，
                        # 这样 stream 内容就紧接在前缀后面输出。
                        if self._bar_drawn > 0:
                            # 上移 bar_drawn 行 + 1 行（prefix 行），清除到屏幕底部
                            lines_up = self._bar_drawn + 1
                            sys.stdout.write(f"[{lines_up}A[0J")
                            sys.stdout.flush()
                            self._bar_drawn = 0
                        else:
                            # 状态栏还没画出来（内容为空），只需上移 1 行
                            sys.stdout.write("[1A[0J")
                            sys.stdout.flush()
                        self._bar_below_prefix = False
                    else:
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

            if self._bar_below_prefix and not self._stream_had_output:
                # LLM 没有产生任何可见输出（纯工具调用等情况），
                # 但状态栏已画在 "agent ❯ " 下方，需擦除并回到 prefix 行末尾。
                if self._bar_drawn > 0:
                    lines_up = self._bar_drawn + 1
                    sys.stdout.write(f"\x1b[{lines_up}A\x1b[0J")
                    sys.stdout.flush()
                    self._bar_drawn = 0
                else:
                    sys.stdout.write("\x1b[1A\x1b[0J")
                    sys.stdout.flush()
                self._bar_below_prefix = False

            if self._stream_had_output:
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._bar_suspended = False
                self._bar_below_prefix = False
                self._draw_bar()
            self._streaming = False
            self._stream_had_output = False
            self._stream_filter_reset()

        elif kind == "statusbar":
            self._statusbar_lines = msg.payload

        elif kind == "redraw":
            if not self._streaming and not self._bar_suspended:
                self._erase_bar()
                self._draw_bar()

        elif kind == "_refresh":
            if self._streaming:
                pass  # 流式输出中：不干扰 stdout
            elif self._bar_suspended:
                # 光标停在 "agent ❯ " 后面，等待 LLM 响应。
                # 先输出 \n 把光标推到新行，再正常绘制状态栏。
                # 设置 _bar_below_prefix，让首个 stream token 到来时
                # erase 逻辑知道要额外上移一行（跨过 "agent ❯ " 行）。
                if self._statusbar_lines:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    self._bar_suspended = False
                    self._bar_below_prefix = True
                    self._draw_bar()
            else:
                self._erase_bar()
                self._draw_bar()

        elif kind == "_focus_lines":
            # 增量打印焦点 task 的新日志行（在 render_thread，串行安全）
            lines = msg.payload
            self._erase_bar()
            for line in lines:
                # 简单的日志着色：工具调用紫色，成功绿色，其余默认
                if "[tool]" in line or "tool_use" in line.lower():
                    sys.stdout.write(f"\033[35m{line}\033[0m\n")
                elif line.lstrip().startswith("✓") or "PASSED" in line or "success" in line.lower():
                    sys.stdout.write(f"\033[32m{line}\033[0m\n")
                elif line.lstrip().startswith("✗") or "FAILED" in line or "error" in line.lower():
                    sys.stdout.write(f"\033[31m{line}\033[0m\n")
                elif line.lstrip().startswith("["):
                    sys.stdout.write(f"\033[90m{line}\033[0m\n")
                else:
                    sys.stdout.write(f"{line}\n")
            sys.stdout.flush()
            self._bar_suspended = False
            self._draw_bar()

        elif kind == "_noop":
            pass  # 哨兵消息，仅用于同步等待队列清空

        elif kind == "_focus_change":
            old_id, new_id = msg.payload
            self._erase_bar()
            if new_id:
                sys.stdout.write(
                    f"\033[36m\n── focus: {new_id} "
                    f"{'─' * max(0, 54 - len(new_id))}\033[0m\n"
                )
                sys.stdout.flush()
                with self._focus_lock:
                    self._focus_log_offset = 0
            else:
                sys.stdout.write(
                    "\033[90m\n── focus cleared ─────────────────────────────────\033[0m\n"
                )
                sys.stdout.flush()
            self._bar_suspended = False
            self._draw_bar()

        elif kind == "_focus_cycle":
            # 在 render_thread 内执行循环切换，避免竞态
            delta = msg.payload
            try:
                from mini_agent.tools.orchestration import get_task_manager
                mgr = get_task_manager()
                if mgr:
                    records = mgr.list_records()
                    if records:
                        ids = [r.task_id for r in records]
                        cur = self._task_focus
                        if cur in ids:
                            idx = (ids.index(cur) + delta) % len(ids)
                        else:
                            idx = 0 if delta > 0 else len(ids) - 1
                        new_id = ids[idx]
                        with self._focus_lock:
                            old = self._task_focus
                            self._task_focus = new_id
                            self._focus_log_offset = 0
                        if old != new_id:
                            self._erase_bar()
                            sys.stdout.write(
                                f"\033[36m\n── focus: {new_id} "
                                f"{'─' * max(0, 54 - len(new_id))}\033[0m\n"
                            )
                            sys.stdout.flush()
                            self._bar_suspended = False
                            self._draw_bar()
            except Exception:
                pass

        elif kind == "_force_end_stream":
            # 强制结束流式状态（异常恢复时使用）
            if self._bar_below_prefix:
                if self._bar_drawn > 0:
                    lines_up = self._bar_drawn + 1
                    sys.stdout.write(f"\x1b[{lines_up}A\x1b[0J")
                    sys.stdout.flush()
                    self._bar_drawn = 0
                else:
                    sys.stdout.write("\x1b[1A\x1b[0J")
                    sys.stdout.flush()
                self._bar_below_prefix = False
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
                self._bar_suspended = False
                self._bar_below_prefix = False
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
            self._bar_suspended = False
            return
        sys.stdout.write(f"\x1b[{self._bar_drawn}A\x1b[0J")
        sys.stdout.flush()
        self._bar_drawn = 0
        self._bar_suspended = False

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

            # ── 焦点日志增量投递 ──────────────────────────────────────
            # 若有焦点 task，把新增日志行投入队列，由 render_thread 打印，
            # 不与状态栏重绘竞争（同一渲染线程串行处理）。
            focus_id = self._task_focus
            if focus_id:
                try:
                    from mini_agent.tools.orchestration import get_task_manager
                    mgr = get_task_manager()
                    if mgr:
                        rec = mgr.get(focus_id)
                        if rec:
                            with self._focus_lock:
                                offset = self._focus_log_offset
                                new_lines = list(rec.log_lines[offset:])
                                if new_lines:
                                    self._focus_log_offset = offset + len(new_lines)
                            if new_lines:
                                self._q.put(_Msg("_focus_lines", new_lines))
                except Exception:
                    pass

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
        使用 prompt_toolkit 或降级 sys.stdin.readline() 读取一行。

        prompt_toolkit 配置（安装后自动启用）：
        - NestedCompleter：/skill → on/off/list 子命令分层弹出，右侧显示描述
        - @PathCompleter：@src/ 触发文件路径补全
        - FuzzyCompleter：模糊匹配，输入 "sess" 即可匹配 "/session"
        - AutoSuggestFromHistory：历史建议，灰色虚影，→ 接受
        - Tab / Shift-Tab：在候选项间移动
        - 补全菜单暗色主题（Catppuccin Mocha 风格）

        降级策略：
        - ImportError（未安装 ptk）→ 直接降级，不报错
        - 运行时异常（dumb terminal 等）→ 设 _ptk_failed，后续跳过
        """
        if not getattr(self, "_ptk_failed", False):
            try:
                from prompt_toolkit.formatted_text import HTML

                if not hasattr(self, "_ptk_session"):
                    _completer = _build_slash_completer()
                    self._ptk_session = _build_ptk_session(_completer)

                html_prompt = prompt_text or HTML(
                    "<b><ansgreen>You</ansgreen></b><ansicyan> ❯ </ansicyan>"
                )
                result = self._ptk_session.prompt(html_prompt)
                return (result or "").strip()
            except ImportError:
                pass  # 未安装 prompt_toolkit，直接降级
            except (KeyboardInterrupt, EOFError):
                raise  # 由上层处理
            except Exception:
                # ptk 运行时异常（dumb terminal、Windows ConPTY 等），标记后降级
                self._ptk_failed = True

        # 降级：ANSI 提示符 + readline
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


# ── 补全系统 ─────────────────────────────────────────────────────────────────
#
# 实现三层补全，对标 Claude Code 体验：
#
#  1. slash 命令补全（NestedCompleter）
#     /skill → on / off / list 子命令弹出，每个命令带右侧描述文字
#  2. @ 文件路径补全（PathCompleter）
#     @src/ 展开为当前目录下的文件列表
#  3. 历史命令建议（AutoSuggestFromHistory）
#     向右箭头接受建议，灰色虚影显示
#
# NestedCompleter 格式：
#   { "/cmd": None }                       → 叶子命令，无子命令
#   { "/cmd": { "sub": None } }            → 有子命令
#   { "/cmd": Completer(...) }             → 子命令用独立 Completer


# ── 命令定义表 ───────────────────────────────────────────────────────────────
# 每条命令：(完整命令字符串, 描述, 子命令列表)
# 子命令列表为空表示叶子命令。

_COMMANDS: list[tuple[str, str, list[str]]] = [
    ("/help",        "Show help",                    []),
    ("/clear",       "Clear conversation history",   []),
    ("/compact",     "Compress history",              []),
    ("/memory",      "Generate/refresh session memory now", []),
    ("/profile",     "Refresh user profile now",      []),
    ("/stats",       "Show session statistics",       []),
    ("/verbose",     "Toggle verbose mode",           []),
    ("/prompts",     "List prompt files",             []),
    ("/retry",       "Retry last turn",               []),
    ("/rollback",    "Rollback last turn",            []),
    ("/skills",      "List all skills",               []),
    ("/skill",       "Manage skills",                 ["on", "off", "list"]),
    ("/model",       "Switch LLM model",              []),
    ("/session",     "Session management",            ["list", "new", "load", "delete"]),
    ("/tasks",       "Task management",               ["focus", "unfocus", "dashboard", "log", "cancel", "cancel-all", "workers"]),
    ("/plan",        "Plan management",               ["clear", "summary"]),
    ("/concurrency", "Concurrency settings",          ["tasks", "llm"]),
    ("/cc",          "Concurrency alias",             ["tasks", "llm"]),
    ("/provider",    "LLM provider settings",         ["list", "switch"]),
    ("/exit",        "Exit mini-agent",               []),
    ("/quit",        "Exit mini-agent",               []),
]


def _build_slash_completer():
    """
    构建前缀匹配的分层 slash 命令补全器。

    行为（对标 Claude Code）：
    - 输入 "/"   → 列出所有命令，右侧显示描述
    - 输入 "/h"  → 只显示 /h 开头的命令（/help）
    - 输入 "/se" → /session
    - 输入 "/skill " → 弹出子命令 on / off / list
    - 输入 "@src/" → 文件路径补全
    """
    try:
        from prompt_toolkit.completion import Completer, Completion, PathCompleter, merge_completers
        from prompt_toolkit.document import Document
    except ImportError:
        return None

    class _SlashCompleter(Completer):
        """
        两阶段前缀补全：
        1. 光标前的最后一个 token 以 "/" 开头 → 顶层命令前缀匹配
        2. 光标前已有完整命令且后面有空格 → 子命令前缀匹配
        """
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor

            # ── 阶段 2：子命令补全 ──────────────────────────────────
            # 格式："/cmd sub_prefix"，中间有空格
            if " " in text:
                parts = text.split()
                if not parts:
                    return
                cmd = parts[0].lower()
                sub_prefix = parts[-1].lower() if len(parts) > 1 else ""
                # 找到对应命令的子命令列表
                for name, _desc, subs in _COMMANDS:
                    if name == cmd and subs:
                        for sub in subs:
                            if sub.startswith(sub_prefix):
                                # 计算插入偏移：替换光标前的 sub_prefix 部分
                                yield Completion(
                                    sub,
                                    start_position=-len(sub_prefix),
                                    display_meta=f"{name} {sub}",
                                )
                        return

            # ── 阶段 1：顶层命令前缀补全 ────────────────────────────
            # text 以 "/" 开头（可能后面有字符，但没有空格）
            if not text.startswith("/"):
                return
            prefix = text.lower()
            for name, desc, subs in _COMMANDS:
                if name.startswith(prefix):
                    hint = f"  {desc}"
                    if subs:
                        hint += f"  [{' | '.join(subs)}]"
                    yield Completion(
                        name,
                        start_position=-len(text),   # 替换掉已输入的前缀
                        display=name,
                        display_meta=hint,
                    )

    class _AtPathCompleter(Completer):
        """'@path' 触发文件路径补全。"""
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            at_pos = text.rfind("@")
            if at_pos == -1:
                return
            path_fragment = text[at_pos + 1:]
            pc = PathCompleter(expanduser=True, only_directories=False)
            sub_doc = Document(path_fragment, len(path_fragment))
            for c in pc.get_completions(sub_doc, complete_event):
                yield Completion(
                    c.text, c.start_position,
                    display=c.display,
                    display_meta="file",
                    style="fg:#888888",
                )

    return merge_completers([_SlashCompleter(), _AtPathCompleter()])


def _build_ptk_session(completer):
    """构建配置好的 PromptSession。"""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.filters import completion_is_selected

    kb = KeyBindings()

    @kb.add("tab")
    def _tab(event):
        """Tab 打开补全菜单；菜单已打开时选下一项。"""
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_next()
        else:
            b.start_completion(select_first=False)

    @kb.add("s-tab")
    def _shift_tab(event):
        """Shift-Tab 选上一项。"""
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_previous()

    @kb.add("enter", filter=completion_is_selected)
    def _accept_completion(event):
        """补全菜单打开且已选中时，Enter 接受选中项而非提交。"""
        event.app.current_buffer.apply_completion(
            event.app.current_buffer.complete_state.current_completion
        )

    # ── Task 焦点快捷键说明 ─────────────────────────────────────────
    # 方向键焦点切换【不在这里绑定】。
    #
    # 原因：ptk 的 KeyBindings 只在 .prompt() 阻塞期间活跃（ptk 拥有终端）。
    # 用户需要在 agent.run_turn() 期间按方向键切换 task 焦点，但此时 ptk 已
    # 退出，终端回到 cooked 模式，这里绑的任何快捷键都不会触发。
    #
    # 真正的方向键监听由 ui/raw_key_listener.py 的 RawKeyListener 负责：
    # - repl.py 在 run_turn() 前调用 listener.start()（tty.setraw + 线程）
    # - run_turn() 结束后调用 listener.stop()（恢复 termios）
    # - 线程解析 ESC[A/B/C/D 序列，直接调用 terminal.focus_*()

    # 注意：enable_history_search=True 会在 prompt_toolkit 内部把
    # complete_while_typing 强制设为 False（两者在源码里互斥）。
    # 解决方案：关掉 enable_history_search，改用 Ctrl+R 手动触发历史搜索。
    # AutoSuggestFromHistory 的灰色虚影（→ 接受）仍然保留。
    from prompt_toolkit.filters import Condition

    return PromptSession(
        history=InMemoryHistory(),
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        complete_while_typing=True,
        enable_history_search=False,   # 必须关闭，否则 complete_while_typing 被强制 False
        mouse_support=False,
        key_bindings=kb,
        style=_PTK_STYLE,
    )


try:
    from prompt_toolkit.styles import Style as _PtkStyle
    _PTK_STYLE = _PtkStyle.from_dict({
        # 提示符颜色
        "ansgreen":  "bold #00cc00",
        "ansicyan":  "#00cccc bold",
        # 补全菜单
        "completion-menu.completion":           "bg:#1e1e2e fg:#cdd6f4",
        "completion-menu.completion.current":   "bg:#313244 fg:#cba6f7 bold",
        "completion-menu.meta.completion":      "bg:#1e1e2e fg:#6c7086",
        "completion-menu.meta.completion.current": "bg:#313244 fg:#a6adc8",
        # 模糊匹配高亮
        "completion-menu.completion fuzzymatch.inside": "fg:#f38ba8 bold",
        # 历史建议（灰色虚影）
        "auto-suggestion": "fg:#585b70 italic",
    })
except ImportError:
    _PTK_STYLE = None


# ── 模块级单例 ────────────────────────────────────────────────────────────────

term: Terminal = Terminal()


def get_terminal() -> Terminal:
    return term