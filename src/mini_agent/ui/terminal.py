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

import os
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

# ── simple-mode 默认值 ───────────────────────────────────────────────────────
# 部分环境（典型如 Termux、某些精简终端模拟器、串口控制台）的 ANSI 光标控制
# 支持不完整或行为不一致：`\x1b[NA`（上移）、`\x1b[0J`（擦除到屏底）等序列
# 可能不生效或生效不完整，导致 terminal.py 赖以实现"无闪烁刷新"的
# erase→redraw 循环在这些环境里反而表现为：状态栏重复堆叠、文字错位、
# 内容被错误擦除等"排版混乱"问题（比正常滚动输出体验更差）。
# simple-mode 关闭所有光标定位/擦除操作，所有内容一律正常 print（只追加，
# 不回退/不擦除），用空间换正确性。可通过 --simple-mode CLI 参数或
# MINI_AGENT_SIMPLE_MODE=1 环境变量开启。
_SIMPLE_MODE_ENV: bool = os.environ.get("MINI_AGENT_SIMPLE_MODE", "").strip().lower() in (
    "1", "true", "yes", "on",
)


class _Msg:
    __slots__ = ("kind", "payload")
    def __init__(self, kind: str, payload: Any = None):
        self.kind = kind
        self.payload = payload


class Terminal:
    """唯一的终端 I/O 管理器。通过模块级 `term` 单例访问。"""

    def __init__(self, status_refresh_hz: int = 4, simple_mode: Optional[bool] = None) -> None:
        self._console = Console(highlight=False)
        self._q: queue.Queue[_Msg] = queue.Queue()
        self._statusbar_lines: list[str] = []
        self._bar_drawn: int = 0

        # ── simple-mode：特殊环境降级显示 ────────────────────────────────
        # True 时关闭一切"先擦除再重绘"的 ANSI 光标控制逻辑，并且完全
        # 不显示状态栏（不是"降级为追加打印"，是彻底不打印任何状态栏
        # 内容）：
        #   - 状态栏不再渲染，也不再以任何形式打印（包括追加打印）
        #   - 流式输出/print 等不再调用 _erase_bar()，直接顺序写出
        #   - _bar_below_prefix 三阶段状态机整体跳过（不需要，因为从不擦除）
        #   - _erase_bar() / _draw_bar() / _erase_bar_direct() 三个函数
        #     内部都各自有 simple_mode 早退保护，无论从哪里被调用，
        #     在 simple-mode 下都绝不会发出任何 ANSI 光标控制序列
        # 默认值：显式传参 > MINI_AGENT_SIMPLE_MODE 环境变量 > False。
        self._simple_mode: bool = bool(simple_mode) if simple_mode is not None else _SIMPLE_MODE_ENV
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

        # 保存最近一次「行内挂起」打印（end="" 的 print 调用，典型场景是
        # print_assistant_prefix() 打印 "agent ❯ "）的原始 args/kwargs。
        #
        # 背景（曾经真实出现过的回归 bug）：当状态栏因等待 LLM 响应被画到
        # prefix 行下方后，stream 分支需要"回到 prefix 行末尾继续输出"。
        # 早期实现试图用 \x1b[NA（CUU，仅控制行、不控制列）配合 \x1b[0J
        # 清除状态栏来实现，隐含假设"上移 N 行后列位置会自动停在 prefix
        # 文本之后"——但这是错误的：ANSI 的 CUU 只改变行号，列号始终保持
        # 在"当前列"。而前面为了把状态栏推到新行已经写过 "\n"
        # （终端 OPOST 会把它转成 \r\n），列号早被重置为 0。于是
        # \x1b[NA\x1b[0J 实际把光标定位到了 prefix 那一行的【行首】而非
        # 【行尾】，0J 清除到屏幕底部，连 prefix 文本本身都被一并擦掉，
        # 造成 "agent ❯ " 前缀在视觉上消失、正文另起一行出现的错乱画面。
        #
        # 正确做法：不依赖光标"记住"列位置，而是显式上移到该行行首、清除，
        # 再重新打印一次保存好的 prefix 内容——列位置必然正确，因为是
        # 重新渲染而不是寄望于光标状态被保留。
        self._open_line_render: tuple | None = None

        # 状态栏内容提供者回调（由 status_bar 模块注册）
        # 架构改进：Terminal 自己在刷新周期内调用回调拉取内容，
        # 而不是由外部线程主动 push update+redraw 两条消息。
        # 这样 _refresh_paused 只需一处检查，消除了 push_loop 与
        # _enter_input_mode 之间的竞态窗口。
        self._statusbar_provider: Optional[Callable[[], list[str]]] = None

        # 阻塞输入期间的消息缓存：
        # _enter_input_mode() 只能保证 Terminal 自己的状态栏刷新线程不再
        # 写屏幕，但业务代码（如后台摘要/画像生成线程）仍可能在用户正阻塞
        # 于 prompt_toolkit.prompt() 等待输入时调用 term.print() 等方法。
        # 这类消息一旦被渲染线程直接写 stdout，会与 prompt_toolkit 自己
        # 管理的输入行/光标产生竞争，造成画面错乱（消息插入到 "You ❯" 之后、
        # 状态栏被意外重绘等）。
        # 解决方式：_input_blocking=True 期间，print/rule/panel/syntax/
        # markdown/stream/stream_end 类消息一律缓存到 _pending_during_input，
        # 不写屏幕；待 _exit_input_mode() 后统一重新入队补打印，保证消息
        # 不丢失，只是延迟到不会撕裂输入行的时机显示。
        #
        # 注意：stream/stream_end 必须和 print 一起拦截——见 _handle() 中
        # 的详细说明。二者是同一段输出（"agent ❯ " 前缀 + 紧随其后的流式
        # 正文）的两半，必须同进同出，否则会出现前缀缺席、正文却正常显示
        # 的错乱画面。
        #
        # 看门狗：_input_blocking 理论上只应在 _enter_input_mode() 到
        # _exit_input_mode() 之间的极短窗口（人类按键间隔）内为 True。
        # 如果因为某个未预见的异常路径（例如调用方持有的 confirm()/
        # prompt_user() 在 finally 执行前进程崩溃式退出某个线程、或多线程
        # 权限确认场景下的边界条件）导致标志没能被正确清除，所有后续 agent
        # 输出都会被无限期缓存、永不上屏。_refresh_loop 中的看门狗会在
        # 标志持续为 True 超过 _INPUT_BLOCKING_TIMEOUT 秒后强制复位，
        # 避免输出永久卡死（详见 _refresh_loop）。
        self._input_blocking: bool = False
        self._pending_during_input: list[_Msg] = []
        self._input_blocking_since: float = 0.0
        self._INPUT_BLOCKING_TIMEOUT: float = 120.0  # 秒；远大于正常人类输入耗时

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

        # ── SIGWINCH 处理（终端窗口 resize）────────────────────────────────
        # 问题根源：用户拖动终端窗口改变尺寸时，rich Console 会缓存旧的终端
        # 宽度（_width/_height）。_draw_bar() 用旧宽度渲染出的内容行数估算
        # 偏高（同等内容在窄终端占更多行），导致 _bar_drawn 虚高；下一次
        # _erase_bar() 上移 _bar_drawn 行时超出实际行数，把正在等待用户输入
        # 的 "You ❯" 提示符也一并擦掉，造成闪烁/消失现象。
        #
        # 修复：捕获 SIGWINCH，在 resize 时：
        #   1. 让 Console 丢弃宽度缓存（_width/_height = None），下次渲染时
        #      自动从 os.get_terminal_size() 重新读取正确尺寸
        #   2. 把 _bar_drawn 重置为 0，避免用旧行数做超界擦除
        #   3. 若此刻不在输入阻塞期间，投一条 redraw 让状态栏以新宽度重绘
        #
        # 注意：
        #   - SIGWINCH 只在主线程可靠（CPython signal 限制）；这里在构造函数
        #     （主线程执行）中注册，不影响其他 signal 处理逻辑。
        #   - Windows 没有 SIGWINCH，用 hasattr 保护。
        #   - prompt_toolkit 自己也会注册 SIGWINCH handler 来刷新输入行；
        #     我们在自己的 handler 末尾转发给之前的 handler，保证 ptk 的
        #     resize 重绘逻辑依然能正常触发。
        import signal as _signal
        if hasattr(_signal, "SIGWINCH"):
            _prev_sigwinch = _signal.getsignal(_signal.SIGWINCH)

            def _on_sigwinch(signum, frame):
                # 1. 让 rich Console 丢弃宽度缓存，下次渲染自动重测
                try:
                    self._console._width = None
                    self._console._height = None
                except Exception:
                    pass
                # 2. 重置已绘状态栏行数，防止超界擦除把 "You ❯" 也擦掉
                self._bar_drawn = 0
                # 3. 若不在输入期间，重绘状态栏（适应新宽度）
                if not self._input_blocking and not self._refresh_paused.is_set():
                    self._q.put(_Msg("redraw", None))
                # 4. 转发给之前的 handler（如 prompt_toolkit 自己注册的）
                if callable(_prev_sigwinch):
                    _prev_sigwinch(signum, frame)

            _signal.signal(_signal.SIGWINCH, _on_sigwinch)

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

    # ── simple-mode 控制 ─────────────────────────────────────────────────

    def set_simple_mode(self, enabled: bool) -> None:
        """
        运行期切换 simple-mode（典型用法：app.py 在解析完 CLI 参数后、
        产生任何输出之前调用一次）。

        切换为 True 时顺手把已经画在屏幕上的状态栏"作废"——不主动擦除
        （simple-mode 本身就不做擦除操作，也不再显示状态栏），只是重置
        内部 _bar_drawn 计数，避免后续误判为"原地刷新模式下已绘制 N 行"。
        """
        self._simple_mode = bool(enabled)
        if self._simple_mode:
            self._bar_drawn = 0
            self._bar_suspended = False
            self._bar_below_prefix = False

    def is_simple_mode(self) -> bool:
        return self._simple_mode

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
        4. 设置 _input_blocking，让渲染线程在接下来的阻塞输入期间把
           print/rule/panel/syntax/markdown 类消息缓存而非直接写屏幕
           （见 _handle()），避免与 prompt_toolkit 的输入行渲染竞争。

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
        # 4. 开始缓存阻塞输入期间到达的输出类消息（业务后台线程可能仍在产生）
        self._input_blocking = True
        self._input_blocking_since = time.monotonic()

    def _exit_input_mode(self) -> None:
        """
        输入完成后：
        1. 停止缓存模式，取出阻塞期间积压的消息
        2. 恢复刷新
        3. 把积压消息重新入队，让渲染线程正常补打印（此时已脱离
           prompt_toolkit 的输入行上下文，可以安全写屏幕）
        4. 重绘状态栏
        """
        self._input_blocking = False
        self._input_blocking_since = 0.0
        pending, self._pending_during_input = self._pending_during_input, []
        self._refresh_paused.clear()
        for msg in pending:
            self._q.put(msg)
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

        # 阻塞输入期间（主线程正卡在 prompt_toolkit.prompt() 等待用户输入），
        # 业务后台线程（如摘要/画像生成）仍可能调用 term.print() 等方法。
        # 这类会直接写 stdout 的消息一旦在此时渲染，会撕裂 prompt_toolkit
        # 自己管理的输入行，造成画面错乱。因此先缓存，等 _exit_input_mode()
        # 后再统一补打印。_noop/_refresh/statusbar 等不写屏幕的消息不受影响。
        #
        # 重要：stream/stream_end 必须和 print（用于打印 "agent ❯ " 前缀，
        # end=""）一起被拦截，不能只拦截 print。二者是同一个逻辑输出的
        # 不可分割的两半——print_assistant_prefix() 打印前缀后，紧跟着
        # 一连串 stream token 续写在同一行。如果前缀被缓存暂不上屏，而
        # stream token 不在拦截名单内、被正常处理写到了 sys.stdout，
        # 就会出现「正文内容显示了，但前面的 "agent ❯ " 前缀却缺席」的
        # 错乱画面——这正是本函数早期版本的一个回归 bug：当时只拦截了
        # print/rule/panel/syntax/markdown，遗漏了 stream/stream_end，
        # 导致两路消息在 _input_blocking 期间被区别对待、显示不一致。
        if self._input_blocking and kind in (
            "print", "rule", "panel", "syntax", "markdown",
            "stream", "stream_end",
        ):
            self._pending_during_input.append(msg)
            return

        if self._simple_mode:
            self._handle_simple(msg)
            return

        if kind == "print":
            args, kwargs = msg.payload
            self._erase_bar()
            self._console.print(*args, **kwargs)
            if kwargs.get("end", "\n") == "":
                # 光标停在行中（如 "orzooo " 前缀），暂停状态栏重绘，
                # 等待后续 stream/markdown 产生换行后再恢复。
                # 保存这次调用，供状态栏被画到下方后需要"回到行尾"时
                # 重新打印恢复正确列位置（见 _open_line_render 注释）。
                self._bar_suspended = True
                self._open_line_render = (args, kwargs)
            else:
                self._bar_suspended = False
                self._open_line_render = None
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
                        # 状态栏画在了 "agent ❯ " 下方。不能依赖 \x1b[NA
                        # （只控制行，不控制列）回到 prefix 文本之后的列
                        # 位置——中途的 "\n" 已经把列号重置为 0，单纯上移
                        # N 行只会停在 prefix 那一行的行首，而非行尾，
                        # 之后的 \x1b[0J 会把 prefix 文本本身也清除掉，
                        # 造成 "agent ❯ " 前缀视觉上消失、正文另起一行
                        # 出现的错乱画面（曾经真实复现过的回归 bug）。
                        # 正确做法：上移到 prefix 行的行首、清除该行及
                        # 以下所有内容，再重新打印一次保存好的 prefix
                        # 渲染，列位置必然正确（见 _open_line_render 的
                        # 定义注释）。
                        lines_up = (self._bar_drawn if self._bar_drawn > 0 else 0) + 1
                        sys.stdout.write(f"\x1b[{lines_up}A\x1b[0J")
                        sys.stdout.flush()
                        self._bar_drawn = 0
                        self._replay_open_line()
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
                # LLM 没有产生任何可见输出（纯工具调用等情况），但状态栏
                # 已画在 "agent ❯ " 下方。同样不能依赖 \x1b[NA 回到 prefix
                # 行尾的列位置（原因见 stream 分支的详细注释）——这里
                # 上移到 prefix 行行首、清除，再重新打印一次 prefix，
                # 然后让下面的 "_stream_had_output" 判断走兜底换行逻辑
                # （prefix 后没有内容追加，直接收尾换行）。
                lines_up = (self._bar_drawn if self._bar_drawn > 0 else 0) + 1
                sys.stdout.write(f"\x1b[{lines_up}A\x1b[0J")
                sys.stdout.flush()
                self._bar_drawn = 0
                # _replay_open_line() 内部已设置 _stream_had_output=True，
                # 确保下面的兜底换行逻辑会被触发（prefix 被重新打印后，
                # 即便本轮无内容，也需要换行收尾，否则下一次输出会接在
                # prefix 同一行造成粘连）。
                self._replay_open_line()
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
                # 同样不能依赖 \x1b[NA 回到 prefix 行尾的列位置
                # （原因见 stream 分支的详细注释）：上移到行首、清除，
                # 再重新打印一次 prefix，保证列位置正确。
                lines_up = (self._bar_drawn if self._bar_drawn > 0 else 0) + 1
                sys.stdout.write(f"\x1b[{lines_up}A\x1b[0J")
                sys.stdout.flush()
                self._bar_drawn = 0
                self._replay_open_line()
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

    # ── simple-mode 分发（无擦除、无光标控制，仅顺序打印）────────────────
    #
    # 设计原则：simple-mode 下完全不依赖光标位置/行数这些"会被特殊终端
    # 破坏"的状态。所有内容一律按收到的顺序原样写出并换行，状态栏也
    # 不再"原地刷新"，退化为偶尔打印一行新的状态摘要（只在内容变化时
    # 打印，且自带换行），效果类似于普通日志输出——这正是用户在 Termux
    # 等环境下想要的：宁可多刷几行，也不要错位、不要内容被覆盖。
    def _handle_simple(self, msg: _Msg) -> None:
        kind = msg.kind

        if kind == "print":
            args, kwargs = msg.payload
            # simple-mode 下不需要"行内挂起"机制：即使调用方传了 end=""
            # （例如 print_assistant_prefix 打印 "agent ❯ "），这里也尊重
            # 原始 end 参数，让前缀和后续 stream token 自然拼接在同一行，
            # 不去管理状态栏与之的位置关系——因为 simple-mode 根本不会把
            # 状态栏画到这一行下面，没有"回到行尾"的需要。
            self._console.print(*args, **kwargs)

        elif kind == "rule":
            title, kwargs = msg.payload
            self._console.rule(title, **kwargs)

        elif kind == "panel":
            content, kwargs = msg.payload
            self._console.print(Panel(content, **kwargs))

        elif kind == "syntax":
            code, language, kwargs = msg.payload
            self._console.print(Syntax(code, language, **kwargs))

        elif kind == "markdown":
            self._console.print(Markdown(msg.payload))

        elif kind == "stream":
            token = msg.payload
            filtered = self._filter_token(token)
            if filtered:
                self._streaming = True
                self._stream_had_output = True
                sys.stdout.write(filtered)
                sys.stdout.flush()

        elif kind == "stream_end":
            if self._pending_stream:
                sys.stdout.write(self._pending_stream)
                self._stream_had_output = True
            if self._stream_had_output:
                sys.stdout.write("\n")
                sys.stdout.flush()
            self._streaming = False
            self._stream_had_output = False
            self._stream_filter_reset()

        elif kind == "statusbar":
            # simple-mode 下不显示状态栏（用户明确要求：simple-mode 不应该
            # 出现状态栏这种"持续刷新的活动内容"，哪怕是追加打印也不要）。
            # 这里仍然更新内部缓存只是为了保持状态一致、不报错，没有任何
            # 打印路径会读取它。
            self._statusbar_lines = msg.payload

        elif kind == "redraw":
            # simple-mode 下状态栏整体不显示，redraw 请求直接忽略。
            pass

        elif kind == "_refresh":
            # simple-mode 下状态栏整体不显示，周期性 tick 直接忽略。
            # 不打印任何内容，也绝不触碰光标/擦除——保持"零额外输出、
            # 零光标控制"的简化模式承诺。
            pass

        elif kind == "_focus_lines":
            lines = msg.payload
            for line in lines:
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

        elif kind == "_noop":
            pass

        elif kind == "_focus_change":
            old_id, new_id = msg.payload
            if new_id:
                sys.stdout.write(f"\n── focus: {new_id} ──\n")
                with self._focus_lock:
                    self._focus_log_offset = 0
            else:
                sys.stdout.write("\n── focus cleared ──\n")
            sys.stdout.flush()

        elif kind == "_focus_cycle":
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
                            sys.stdout.write(f"\n── focus: {new_id} ──\n")
                            sys.stdout.flush()
            except Exception:
                pass

        elif kind == "_force_end_stream":
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

    # ── 状态栏绘制（仅在 render_thread 中调用）───────────────────────────

    def _draw_bar(self) -> None:
        # 防御性保护：simple-mode 下不显示状态栏，也绝不使用擦除/原地
        # 重绘机制。正常情况下 _handle_simple() 根本不会调用到这里
        # （它走完全独立的代码路径），这里再加一道保险，防止未来有人
        # 不小心从别处调用 _draw_bar() 时仍然违反 simple-mode 的承诺。
        if self._simple_mode:
            return
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
        # 同上：防御性保护，simple-mode 下绝不发出擦除序列。
        if self._simple_mode:
            return
        if not _IS_TTY or self._bar_drawn == 0:
            return
        sys.stdout.write(f"\x1b[{self._bar_drawn}A\x1b[0J")
        sys.stdout.flush()
        self._bar_drawn = 0

    # ── 状态栏操作（主线程直接调用，仅在队列空闲时安全）─────────────────

    def _erase_bar_direct(self) -> None:
        # simple-mode 下没有"原地绘制"的状态栏可擦——_bar_drawn 也不会被
        # 维护成非零值（_handle_simple 从不调用 _draw_bar）。这里直接早退，
        # 不发任何 ANSI 控制序列，避免对不支持光标控制的终端产生副作用。
        if self._simple_mode:
            self._bar_suspended = False
            return
        if not _IS_TTY or self._bar_drawn == 0:
            self._bar_suspended = False
            return
        sys.stdout.write(f"\x1b[{self._bar_drawn}A\x1b[0J")
        sys.stdout.flush()
        self._bar_drawn = 0
        self._bar_suspended = False

    # ── 行内挂起内容重放 ─────────────────────────────────────────────────

    def _replay_open_line(self) -> None:
        """
        重新打印保存的「行内挂起」内容（典型场景：状态栏被画到
        "agent ❯ " 前缀下方后，需要回到 prefix 行重新输出）。

        不能直接复用原始 args 重新调用 console.print()：第一次打印时
        prefix 字符串通常带有一个前导 "\n"（用来和上一段输出隔开一个
        空行）。这次是在已经清空、定位好的位置重新打印，不需要再插入
        这个前导换行，否则会比首次打印多出一个空行。这里只对字符串
        类型的位置参数做 lstrip("\n")，不触碰非字符串参数或 kwargs。
        """
        if self._open_line_render is None:
            return
        args, kwargs = self._open_line_render
        stripped_args = tuple(
            a.lstrip("\n") if isinstance(a, str) else a for a in args
        )
        self._console.print(*stripped_args, **kwargs)
        self._stream_had_output = True

    # ── 流式 token 过滤（过滤 <tool_use> 块）────────────────────────────

    _suppress_stream: bool = False
    _pending_stream: str = ""

    def _filter_token(self, token: str) -> str:
        """
        过滤流式 token 中的 <tool_use>...</tool_use> 块，只把可见对话文本
        透传给屏幕。需要正确处理标签被截断在两个 token 边界之间的情况
        （例如 "</tool" 和 "_use>" 分属两次 on_token 回调）。

        核心不变量：无论当前缓冲区文本（tail）有多长，只要没能在其中找到
        完整的目标标签（"</tool_use>" 或 "<tool_use>"），就必须保留其
        末尾 len(TAG) - 1 个字符存入 _pending_stream，留给下一个 token
        拼接后继续查找——因为标签的前缀可能恰好就停在这次缓冲区的结尾。

        早期版本在这里有一个边界 bug：当 tail 长度超过 11 时直接整体丢弃
        （_pending_stream = ""），把本该保留用于拼接的标签前缀（如
        "</tool" 中的若干字符）一并丢掉，导致下一个 token（如 "_use>"）
        永远凑不出完整的 "</tool_use>"，_suppress_stream 标志再也不会
        被清除，造成后续所有正常对话文本被永久吞掉、屏幕上只剩下
        "_use>" 之类的标签残片。修复方式：tail 无论长短，都只截取最后
        10 个字符存入 _pending_stream（suppress 分支不需要把前面的内容
        输出，因为那本就是要被抑制的工具调用块内容；非 suppress 分支则
        把前面的内容正常输出，只缓冲最后 10 个字符）。
        """
        result = []
        text = self._pending_stream + token
        self._pending_stream = ""
        i = 0
        while i < len(text):
            if self._suppress_stream:
                end = text.find("</tool_use>", i)
                if end == -1:
                    # 未找到完整结束标签：保留末尾最多 10 个字符（足够拼出
                    # "</tool_use>" 的任意前缀）以待下个 token，前面的内容
                    # 本就处于抑制区间内，无需保留、不输出。
                    tail = text[i:]
                    self._pending_stream = tail[-10:] if len(tail) > 10 else tail
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
                # 看门狗：_input_blocking 正常情况下只会在用户实际输入期间
                # （人类按键间隔，通常数秒）为 True。如果持续超过
                # _INPUT_BLOCKING_TIMEOUT 仍未被 _exit_input_mode() 清除
                # （说明触发了某个未预见的异常路径，标志卡死），强制复位
                # 并 flush 所有缓存消息，避免 agent 输出永久不可见。
                # 这是纵深防御的最后一道保险，正常路径不应触发。
                if (
                    self._input_blocking
                    and self._input_blocking_since
                    and (time.monotonic() - self._input_blocking_since)
                    > self._INPUT_BLOCKING_TIMEOUT
                ):
                    self._input_blocking = False
                    self._input_blocking_since = 0.0
                    pending, self._pending_during_input = self._pending_during_input, []
                    self._refresh_paused.clear()
                    for msg in pending:
                        self._q.put(msg)
                    self._q.put(_Msg("redraw", None))
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