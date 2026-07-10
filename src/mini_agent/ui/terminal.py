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

import asyncio
import os
import queue
import sys
import threading
import time
from typing import Any, Callable, Iterator, Optional

from rich.cells import cell_len
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


_IS_TTY: bool = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _wait_stdin_readable(timeout: float) -> Optional[bool]:
    """
    [BUGFIX] 非阻塞地判断 stdin 在 `timeout` 秒内是否已经有一整行可读，
    不消费任何数据。

    背景（真实复现过的 bug）：`confirm()` / `interruptible_prompt()`
    之前的"可中断读取"实现是每次调用都新起一条线程去 `sys.stdin.readline()`，
    主线程则轮询 `interrupt_event`。一旦 HTTP 端（另一个已连接的客户端 /
    web 看板）先给出了答案，主线程就直接返回、抛 `_InterruptedByHTTP`，
    但那条正阻塞在 `readline()` 里的线程**没有任何办法被强制终止**，
    只能一直留在那里，成为一条"僵尸读取线程"。

    等到之后用户在同一个终端上真正输入下一行内容时（比如主 REPL 的
    "You ❯" 提示符），操作系统会把这一行数据交给"当时恰好在
    readline() 里等待"的**某一个**线程——可能是这次真正当前的读取，
    也可能是之前遗留的僵尸线程。命中后者时，现在这个真正等待输入的
    调用永远收不到这行数据，表现为"提示符显示了、字也打了、回车也按了，
    但完全没反应"——这正是"看到 You ❯ 但实际无法输入"的根因，且
    一定发生在"之前出现过一次被 HTTP 端抢先响应的权限/交互请求"之后，
    与实际观察到的复现条件完全吻合。

    修复方式：不再起新线程，而是用 `select()` 在当前调用线程里*轮询*
    stdin 是否可读——不消费任何字节。POSIX 终端处于行缓冲（canonical/
    cooked）模式时，只有用户真正按下回车、一整行已经进了内核缓冲区，
    `select()` 才会报告"可读"；这之前我们只是反复"看一眼"，从不阻塞、
    也从不留下任何"半路等待"的线程。真被打断时，用户还没敲完的输入
    原封不动留在内核缓冲区里，交给下一个真正的读取者（无论是下一次
    confirm()/interruptible_prompt()，还是主 REPL 的 prompt_toolkit
    读取）干净地拿到，不会被任何遗留线程抢先吃掉。

    返回 True/False 表示 POSIX 下的判断结果；返回 None 表示当前平台
    不支持这种非消费式探测（Windows 的 `select()` 不能用于 stdin 这类
    文件对象），调用方需要退化到旧的线程式实现。
    """
    if sys.platform == "win32":
        return None
    try:
        import select as _select
        r, _, _ = _select.select([sys.stdin], [], [], timeout)
        return bool(r)
    except Exception:
        return None


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

# ── raw-output 默认值 ────────────────────────────────────────────────────────
# 默认情况下，命令行会实时过滤掉 LLM 流式输出中的 <tool_use>...</tool_use>
# 标签块（工具调用 JSON，见 _filter_token()），只把可见对话文本打印给用户。
# 开启 raw-output 后跳过这层过滤，将模型原始输出（包括 <tool_use> 标签本身）
# 不做任何改动地直接打印出来——典型用于调试模型到底输出了什么、标签格式
# 是否符合预期等场景。可通过 --raw-output CLI 参数或
# MINI_AGENT_RAW_OUTPUT=1 环境变量开启。
_RAW_OUTPUT_ENV: bool = os.environ.get("MINI_AGENT_RAW_OUTPUT", "").strip().lower() in (
    "1", "true", "yes", "on",
)


class _Msg:
    __slots__ = ("kind", "payload")
    def __init__(self, kind: str, payload: Any = None):
        self.kind = kind
        self.payload = payload


class _RemoteTurnInterrupt(Exception):
    """
    daemon connected 多客户端场景下的输入锁定机制：当同一 session 里
    *另一个*客户端的 turn 开始时，本端如果正阻塞在 prompt_user() 里
    等待用户输入，会被这个异常从 ptk 的 .prompt()/降级 readline() 中
    强制"踢出来"，交由上层（daemon.py 的 REPL 主循环）改为展示一个
    不可编辑的"等待中"状态，直到对方 turn 结束再重新进入真正的输入态。

    这是解决"任意一端输入时，其它端都应该同步进入不可输入状态"这个
    需求的关键：单靠 _pending_during_input 的延迟补打印/定时 flush，
    只解决了"别人的内容会不会显示出来"，没有解决"本端此时还能不能
    继续敲键盘、且会不会跟正在打印的别人内容抢屏幕"这个更根本的问题。
    """
    pass


# 公开别名：daemon.py 等外部模块 catch 这个异常时用这个名字，不直接引用
# 下划线开头的"私有"类名。
RemoteTurnInterrupt = _RemoteTurnInterrupt


class Terminal:
    """唯一的终端 I/O 管理器。通过模块级 `term` 单例访问。"""

    def __init__(self, status_refresh_hz: int = 4, simple_mode: Optional[bool] = None,
                 raw_output: Optional[bool] = None) -> None:
        self._console = Console(highlight=False)
        self._q: queue.Queue[_Msg] = queue.Queue()
        self._statusbar_lines: list[str] = []
        self._bar_drawn: int = 0
        # ── 命令捕获模式（daemon HTTP 端远程执行 slash 命令用）────────────
        # True 时 _handle() 里所有会触碰真实屏幕的动作（_erase_bar/_draw_bar/
        # 状态栏悬挂逻辑）一律跳过，只把内容写进临时替换掉的 self._console
        # （此时指向一个写内存缓冲区的 rich Console）。见 run_captured()。
        self._capture_mode: bool = False

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
        # raw-output：显式传参 > MINI_AGENT_RAW_OUTPUT 环境变量 > False。
        # 开启后 _filter_token() 直接透传，不再过滤 <tool_use> 标签块。
        self._raw_output: bool = bool(raw_output) if raw_output is not None else _RAW_OUTPUT_ENV
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

        # ── resize 不确定期标志（本次修复的核心） ───────────────────────
        # True 表示"已经收到至少一次 SIGWINCH，但尚未确认尺寸已经安静
        # 稳定"——覆盖从信号触发瞬间到 debounce settle 完成之间的整段
        # 窗口（典型 0.15 秒，见 _SIGWINCH_DEBOUNCE_SECONDS）。
        #
        # ★★★ 真实复现过的严重 bug（比第一版"折行记账"修复更深一层）★★★
        # 第一次修复只在 _on_sigwinch_settled() 这一条路径上做了"放弃
        # 旧记账、换行重画"的处理，但完全没意识到：状态栏最高频的刷新
        # 路径是 _refresh_loop 每 _refresh_interval（默认 0.25 秒）投递
        # 一次的 "_refresh" 消息，它会直接调用 self._erase_bar()，用的
        # 是当前的 self._bar_drawn（可能是 resize 前、按旧宽度算出的、
        # 已经因为终端 reflow 而失真的值）去做 \x1b[1A\x1b[2K 相对
        # 擦除——而 0.25 秒的刷新间隔比 0.15 秒的 debounce 窗口更长，
        # 意味着**几乎每次 resize 都会在 settle 真正触发之前，先撞上
        # 至少一次正常的 _refresh**，那一次擦除完全没有受到任何保护，
        # 仍然可能越界擦掉状态栏上方的正常历史输出——这正是用户反馈
        # "切后台再切回来，还是会向上擦除之前的历史输出"的真正根因：
        # 第一版修复保护的不是高频命中的那条路径。
        #
        # 修复：不再试图在每一个调用 _erase_bar()/_erase_bar_direct()
        # 的上层分支（print/_refresh/stream/_focus_lines/...）里逐一
        # 加判断（容易遗漏，事实证明也确实遗漏过）。而是把保护下沉到
        # 擦除函数本身——_erase_bar()/_erase_bar_direct() 内部检查
        # 这个标志：只要它为 True，就完全不发任何 \x1b[1A 相对位移
        # 序列，转而换行放弃旧内容、把 _bar_drawn 归零，让调用方紧接
        # 着的 _draw_bar() 在全新的一行正常画出——不可能越界，因为
        # 没有任何相对位移操作。
        #
        # 标志的生命周期：
        #   - _on_sigwinch（信号触发瞬间）：置为 True。
        #   - _on_sigwinch_settled（debounce 安静期之后，确认尺寸已经
        #     稳定）：置回 False，重新允许相对擦除——此后画的内容用的
        #     是新宽度，记账重新可信，直到下一次 resize 再次打破它。
        self._resize_unsettled: bool = False

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
        # 看门狗：_refresh_loop 中的看门狗会在 _input_blocking 持续为 True
        # 超过 _INPUT_BLOCKING_TIMEOUT 秒后强制复位（清 _input_blocking /
        # _refresh_paused、flush 所有缓存消息），目的是兜底"某个未预见的
        # 异常路径导致标志没能被正确清除，所有后续 agent 输出被无限期
        # 缓存、永不上屏"这一情况（详见 _refresh_loop）。
        #
        # ★★★ 真实复现过的严重回归（曾经把超时设成 120 秒）★★★
        # 早期版本的注释里写着"_input_blocking 理论上只应在...人类按键
        # 间隔内为 True"——这个假设是错的。_input_blocking 覆盖的是从
        # "进入阻塞输入"到"用户真正提交"之间的*整段*等待，不是"按键
        # 间隔"：用户读完提示、思考、打一段较长的回答，或者（尤其在
        # Termux 等移动端）中途把 App 切到后台去做别的事再切回来，都完全
        # 可能轻松超过 120 秒——这些都是完全正常、合法的人类行为，不是
        # bug。而看门狗一旦误判"卡死"并强制复位，会在用户*仍然合法地*
        # 停留在 prompt_toolkit 的 .prompt() 或 confirm() 的 readline()
        # 里（这两个调用完全不知道、也不关心我们这边的内部计时器）的
        # 情况下，把 _refresh_paused 清掉、让 refresh_thread 重新开始
        # 按周期投递 _refresh 消息——状态栏从此开始一遍遍重绘，跟用户
        # 正在输入的那一行抢屏幕，画面上呈现"一直在刷新、看不到自己刚
        # 打的字"——这正是真实环境里复现过的 bug。
        #
        # 修复：把超时大幅拉长到远超任何正常人类交互（包括"切到后台
        # 待一会儿再切回来"）会触发的量级，让看门狗只在"真的已经没人
        # 会回来了"的极端情况下才介入；宁可异常路径下恢复得慢一点，
        # 也不要在正常使用中频繁误伤正在合法等待输入的场景。
        self._input_blocking: bool = False
        self._pending_during_input: list[_Msg] = []
        self._input_blocking_since: float = 0.0
        self._INPUT_BLOCKING_TIMEOUT: float = 180000.0  # 30 分钟，仅作极端兜底

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

        # 当前正在阻塞等待输入的 ptk Application 实例（由 _read_line() 维护）。
        # 标记：进入阻塞输入前，raw key listener 当时是否处于活跃状态。
        # 用于 _enter_input_mode()/_exit_input_mode() 配对地暂停/恢复它——
        # 详见 _enter_input_mode() 里的说明。
        self._key_listener_was_active: bool = False

        # SIGWINCH 的 debounce-settle 回调用它来跨线程触发强制重绘。
        # 详见 _read_line() 和 _on_sigwinch_settled() 的说明。
        self._active_ptk_app = None

        # ── 远端忙碌标志（daemon connected 多客户端输入互斥） ─────────────
        # set 表示"同一 session 里，另一个客户端正有一个 turn 在处理中"。
        # 由 daemon.py 的 observer 线程在收到别的客户端的 turn_start/
        # turn_done 事件时调用 request_input_lock(True/False) 维护。
        # 语义：
        #   - 为 True 期间，_read_line() 不会开启新的 .prompt() 调用
        #     （避免和正在打印的旁观输出抢屏幕、看起来"能输入"但其实
        #     内容马上就要被冲掉）。
        #   - 如果为 True 的那一刻本端已经在阻塞的 .prompt() 里，会用
        #     app.exit(exception=...) 把它强制打断，抛出
        #     _RemoteTurnInterrupt，交给上层 REPL 循环处理成"等待中"
        #     展示，而不是让用户对着一个随时可能被打断的输入行打字。
        self._remote_busy = threading.Event()

        # ── 阻塞输入期间的"定时补打印"线程 ──────────────────────────────
        # 背景：daemon connected 多客户端场景下，本端正停留在
        # prompt_toolkit 的 .prompt()（"You ❯" 提示符）等待用户输入时，
        # 同一 session 里其他客户端（另一个命令行 / web demo）触发的
        # agent 输出，之前的行为是全部缓冲进 _pending_during_input，
        # 一直等到本端自己提交了一次输入（_exit_input_mode()）才会统一
        # 补打印——效果就是"必须自己发一条消息，才能看到别人的内容"，
        # 不是想要的"实时同步"。
        #
        # 修复：不再只在退出输入模式时才 flush，而是用一个低频定时器
        # （_PTK_FLUSH_INTERVAL）周期性检查 _pending_during_input，只要
        # 有积压内容、且当前真的处于 prompt_toolkit 的阻塞 .prompt() 里
        # （self._active_ptk_app is not None——sys.stdin.readline() 降级
        # 路径下这个值恒为 None，不会触发，那条路径没有安全的"打印到
        # 输入行上方"手段，只能继续沿用旧的"退出时补打印"行为），就用
        # prompt_toolkit 官方提供的 run_in_terminal() 把积压内容打印出来：
        # 它会先暂停当前 Application 的渲染、执行回调、再重新绘制输入行，
        # 不会撕裂正在输入的那一行——这正是 ptk 文档里说明的"在阻塞的
        # prompt 上方安全打印"的标准做法，不是我们自己拿 stdout 硬写。
        #
        # 放在 self._active_ptk_app 赋值之后再启动这个线程，避免线程刚
        # 启动的一瞬间就去读一个还不存在的属性（虽然 wait() 里有
        # _PTK_FLUSH_INTERVAL 秒缓冲，实际不会触发，但没必要留这个隐患）。
        self._PTK_FLUSH_INTERVAL = 0.6  # 秒；够快到接近"实时"，又不会频繁到打扰输入
        self._ptk_flush_stop = threading.Event()
        self._ptk_flush_thread = threading.Thread(
            target=self._ptk_flush_loop, daemon=True, name="terminal-ptk-flush"
        )
        self._ptk_flush_thread.start()

        # ── SIGWINCH debounce（resize 抖动 / 尺寸尚未稳定问题）──────────────
        # 背景（Termux 等移动端终端模拟器上更容易复现）：应用切到后台再
        # 切回前台时，触发的 SIGWINCH 可能出现以下任一情况：
        #   - 短时间内连续触发多次（动画式的尺寸过渡），中间几次的尺寸是
        #     "过渡态"，不是最终稳定值；
        #   - 信号送达的时刻，底层 pty 的 ioctl(TIOCGWINSZ) 可能还没完全
        #     更新到最终尺寸（取决于 Termux 自身从 Android 窗口尺寸到 pty
        #     winsize 的同步实现，这一步发生在我们的进程之外，无法直接
        #     控制其时序）。
        # 如果我们在 SIGWINCH 处理函数里"立即"用当时查到的尺寸重绘，
        # 拿到的很可能是这个过渡态/未稳定值，重绘结果本身就是错的，
        # 之后如果没有再来一次 SIGWINCH（比如最终尺寸和触发动画前的
        # 尺寸恰好相同，某些实现不会为此再发一次信号），就再也没有
        # 机会用正确尺寸纠正了。
        #
        # 解决思路：不在信号处理函数里直接做"最终"重绘，而是用一个
        # 短延时（_SIGWINCH_DEBOUNCE_SECONDS）的 debounce 定时器——每次
        # 收到新的 SIGWINCH 就取消旧定时器、重新计时；只有"安静"超过
        # 这个时长之后，才认为尺寸已经稳定，此时才真正触发重绘。重绘时
        # 不使用信号触发瞬间缓存的任何尺寸值，而是让 rich Console 和
        # prompt_toolkit 在各自渲染时重新查询操作系统当前的真实尺寸
        # （二者的 get_size() 实现都不缓存，每次都是新查询），从根本上
        # 避免"用到尚未更新好的旧数据"。
        self._SIGWINCH_DEBOUNCE_SECONDS = 0.15
        self._sigwinch_debounce_timer: Optional[threading.Timer] = None
        self._sigwinch_debounce_lock = threading.Lock()

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
        #
        # ★★★ 关键陷阱（曾经导致此修复"上线后第一轮对话就失效"）★★★
        # prompt_toolkit 的 PromptSession.prompt() 内部通过
        # asyncio_loop.add_signal_handler(SIGWINCH, ...) 接管 resize 信号。
        # asyncio 的实现（见 unix_events.py）是直接 signal.signal(SIGWINCH,
        # 一个内部 noop 桩函数) 来覆盖"当前"的 OS 级 handler，且只把回调
        # 记在它自己的 loop._signal_handlers 字典里——它既不知道、也不会
        # 调用我们在这里注册的 _on_sigwinch；prompt() 返回时，asyncio 的
        # remove_signal_handler() 还会把 OS 级 handler 显式复位为 SIG_DFL。
        # 也就是说：只要用户提交过一次输入（调用过一次 prompt_toolkit 的
        # .prompt()），我们这里注册的 handler 就会被**永久**替换为 SIG_DFL，
        # 此后整个进程生命周期内的任何 resize（无论发生在 agent 运行期间、
        # 状态栏刷新期间，还是下一次进入输入等待前的极短间隙）都不会再触发
        # 上面的 1/2/3 步修复，_bar_drawn 和 Console 宽度缓存从此失真，
        # 直接导致下一次 _enter_input_mode() 里的 _erase_bar_direct() 用
        # 错误的行数擦除——这正是 "You ❯" 在 resize 后闪烁/消失的真正根因
        # （比"ptk 自己重绘不及时"更深一层，且完全不依赖具体终端模拟器）。
        #
        # 修复方式：把 handler 做成可重新挂载的实例方法（_on_sigwinch /
        # _rearm_sigwinch），并在每次离开阻塞输入（_exit_input_mode，
        # 同时覆盖 prompt_user() 的 ptk 路径和 confirm() 的 readline 路径）
        # 时主动重新 signal.signal() 挂回去。ptk 在【活跃 prompt() 期间】
        # 仍然会临时接管 SIGWINCH（这是期望行为——交给 ptk 自己重绘输入
        # 行），但只要 prompt() 一返回，我们就立刻把 handler 抢回来，确保
        # 后续任何时刻的 resize 都不会再悄悄变成"没人处理"的 SIG_DFL。
        import signal as _signal
        self._signal_mod = _signal
        if hasattr(_signal, "SIGWINCH"):
            self._prev_sigwinch = _signal.getsignal(_signal.SIGWINCH)
            self._rearm_sigwinch()
        else:
            self._prev_sigwinch = None

    def _schedule_sigwinch_settle(self) -> None:
        """
        (重新) 启动一个短延时定时器：如果在 _SIGWINCH_DEBOUNCE_SECONDS 内
        没有再收到新的 SIGWINCH，就认为尺寸已经稳定，执行
        _on_sigwinch_settled() 做一次"确认性"的最终重绘。每次新信号
        到达都会取消旧定时器、重新计时——这样无论一次 resize 触发了
        多少次连续的 SIGWINCH（典型如尺寸过渡动画），最终只会在"安静期"
        之后真正重绘一次，且这一次用到的是当时（延迟之后）查询到的
        最新尺寸，不是信号触发瞬间可能还没更新好的旧值。
        """
        with self._sigwinch_debounce_lock:
            if self._sigwinch_debounce_timer is not None:
                self._sigwinch_debounce_timer.cancel()
            timer = threading.Timer(
                self._SIGWINCH_DEBOUNCE_SECONDS, self._on_sigwinch_settled
            )
            timer.daemon = True
            self._sigwinch_debounce_timer = timer
            timer.start()

    def _on_sigwinch_settled(self) -> None:
        """
        在 resize "安静期"过后（debounce 计时结束）真正执行的确认性重绘。
        运行在定时器自己的线程上，**不是**主线程，也不是渲染线程——
        因此这里只能调用明确线程安全的接口，不能直接操作 stdout 或
        ptk 内部渲染状态：
          - rich Console 的 _width/_height 缓存清空：纯属性赋值，安全。
          - 投递 _Msg 到 self._q：queue.Queue 本身是线程安全的。
          - Application.invalidate()：prompt_toolkit 文档里明确标注为
            "线程安全的重绘触发方式"，专门设计给外部线程调用；它只是
            把一个回调通过 call_soon_threadsafe 扔给 ptk 自己的事件循环，
            真正的重绘（包括重新查询当前终端尺寸）仍然发生在 ptk 自己的
            线程上，不存在跨线程直接操作渲染状态的问题。

        ★★★ 真实复现过的严重问题（比"状态栏堆叠"更严重）★★★
        本方法不再走 "redraw"（_erase_bar() + _draw_bar()，相对上移
        self._bar_drawn 行）的常规重绘路径，而是投递专门的
        "_resize_settled" 消息。原因：

        _bar_drawn 记录的是"画状态栏那一刻、按当时终端宽度计算出的
        物理行数"。这个数字在没有 resize 发生的整段时间里始终可信，
        因为画和擦用的是同一份终端宽度。但 resize（尤其是 Termux
        切后台/前台引发的、往往伴随尺寸剧烈跳变的那种）会触发一个
        我们完全无法控制、也无法事后查询的副作用：**终端模拟器自己
        会把当前屏幕缓冲区里"已经显示"的内容按新列宽重新折行
        （reflow）**。也就是说，旧状态栏那几行在 resize 完成之后，
        屏幕上真实占用的物理行数已经悄悄变了——可能变多（变窄时），
        也可能变少（变宽时）——而 _bar_drawn 里存的还是 resize 前
        按旧宽度算出的数字，对"现在"的屏幕状态已经失真，且没有任何
        escape sequence 能让我们查询"reflow 之后实际变成了几行"。

        继续相信这个失真的 _bar_drawn 去做 \x1b[1A\x1b[2K 循环擦除，
        后果分两种：
          - 终端变窄（reflow 后行数变多）：擦的行数比真实少，旧内容
            擦不干净，新内容叠加在残留之下——表现为状态栏反复堆叠。
          - 终端变宽（reflow 后行数变少）：擦的行数比真实多，多移动
            的那几次 \x1b[1A\x1b[2K 会直接越过状态栏的物理边界，
            擦进状态栏上方"正常历史输出"的区域——表现为之前的输出
            内容被莫名其妙地擦掉（比堆叠更具破坏性，且不可恢复，
            因为被擦的内容已经不在任何缓冲区里了）。

        两种情况的根本原因相同：基于"记住画了多少行，下次原地擦除
        多少行"的相对擦除策略，前提是"记的时候"和"擦的时候"屏幕
        上的物理布局必须没有被外部因素改变过；resize 恰恰打破了
        这个前提，而且没有办法事后修正（reflow 后的真实行数对我们
        的进程不可见）。

        修复策略：resize 安静期过后，不再尝试"原地擦除旧状态栏"，
        而是主动放弃对旧状态栏内容的维护——换一行，把 _bar_drawn
        归零（这次归零是安全的：我们没有谎称"已经擦掉了"，旧内容
        确实还留在屏幕历史里，只是不再尝试覆盖它），从新的一行
        开始重新画状态栏。代价是用户会在历史里多看到一份"过期"的
        旧状态栏文字（良性、最多有点视觉冗余），但绝不会出现内容
        被错误吃掉，也不会无限堆叠——因为新画的这一份，_bar_drawn
        会被 _draw_bar() 重新正确赋值为按当前（新）宽度算出的真实
        物理行数，只要后续没有新的 resize，这个记账立刻恢复可信，
        正常的原地擦除重绘会照常工作。

        ★ self._resize_unsettled 标志的清除时机（第二版修复新增，
        关键细节）★：本方法运行在独立的 Timer 线程上，**不**在这里
        把 self._resize_unsettled 清回 False。原因是竞态：如果在这
        里清除，标志变为 False 的时刻，和 render_thread 真正处理
        "_resize_settled" 消息、完成"换行+归零+重画"的时刻之间存在
        一个时间窗口——这段窗口里如果先插队处理了一条别的消息（如
        正常的 "_refresh"），它会看到标志已经是 False（"认为已经
        安全了"），于是照常调用 _erase_bar() 用旧的、尚未被归零的
        _bar_drawn 做相对擦除——等于完全绕过了本次修复，回到了
        "用旧记账做相对擦除"的老问题。

        正确做法：标志的清除职责转移给 render_thread 自己，在它真正
        完成 "_resize_settled" 消息的处理（旧内容已经被放弃、新内容
        已经按新宽度重新画出、_bar_drawn 已经刷新为可信值）之后才
        清除——确保"标志变为 False"和"_bar_drawn 重新可信"这两件
        事严格在同一时刻发生，不存在窗口期不一致。

        例外：app is not None（阻塞输入中）这条路径完全不经过我们
        的 _bar_drawn 记账体系（ptk 自己管理重绘），这里可以直接
        清除标志，不存在上述竞态。
        """
        try:
            self._console._width = None
            self._console._height = None
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
            pass

        app = self._active_ptk_app
        if app is not None:
            # 用户正阻塞在 prompt_toolkit 的 .prompt() 里（典型即"等待
            # 用户输入"场景）。直接让 ptk 用当前最新尺寸强制重绘一次，
            # 不依赖 ptk 自己内部的 resize-diff 判断（如果尺寸在动画
            # 过程中一度变化又变回原值，ptk 可能认为"没有变化"而跳过
            # 重绘，但屏幕实际内容已经因为 Android 切前台/后台被破坏，
            # 这种情况下仍然需要一次强制重绘）。
            # 注意：ptk 自己管理输入行的重绘，不经过我们的 _bar_drawn
            # 记账体系，这条路径本身不受上面 reflow 问题影响，可以
            # 直接清除不确定期标志（不存在竞态——没有 render_thread
            # 消息处理需要等待）。
            self._resize_unsettled = False
            try:
                app.invalidate()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
                pass
        elif not self._input_blocking and not self._refresh_paused.is_set():
            # 不在任何阻塞输入路径里（agent 正在运行/状态栏可见）。
            # 走专门的 "_resize_settled" 消息，由 render_thread 用
            # "换行 + 放弃旧内容"的安全方式处理，而不是常规的
            # "redraw"（相对擦除）路径——理由见上面的详细说明。
            #
            # 注意：这里不清除 self._resize_unsettled——它会在
            # render_thread 真正处理完这条消息（_bar_drawn 已刷新为
            # 可信值）之后才清除，避免竞态窗口期内插队的其他消息
            # 误以为"已经安全"而绕过保护。
            self._q.put(_Msg("_resize_settled", None))
        # else: 阻塞输入但没有活跃 ptk app（如 confirm() 的裸 readline
        # 路径）。这种场景下没有任何安全的时机去执行"换行+归零+重画"
        # （会撕裂正在显示的确认提示符），所以保持 self._resize_unsettled
        # 为 True 不清除——代价是直到下一次有效的 settle（例如用户
        # 完成这次输入、退出阻塞、后续再发生一次 resize 后才会被清除）
        # 之前，所有相对擦除路径会持续退化为安全模式（换行重画而非
        # 原地覆盖）。这只是偶尔多换几行的轻微视觉冗余，不是功能性
        # 问题，比"继续相信可能已经失真的 _bar_drawn"安全得多。

    def _on_sigwinch(self, signum, frame) -> None:
        """
        SIGWINCH 立即响应（信号触发的那一刻，可能在尺寸真正稳定之前）。

        ★ 与旧版本的关键差异 ★
        旧版本这里会立即投递一条 "redraw" 消息（= _erase_bar() 用旧的
        self._bar_drawn 做相对擦除，再 _draw_bar() 重画）。这在桌面
        终端上通常无害，因为：(a) resize 多为一次性的、尺寸瞬间稳定；
        (b) 列宽变化幅度较小，折行差异有限。

        但 Termux 切后台/切前台触发的 SIGWINCH 往往伴随尺寸的剧烈
        跳变和多次连续触发（过渡动画），信号触发的瞬间，终端的屏幕
        缓冲区可能还没有完成 reflow（把已显示内容按新列宽重新折行），
        也可能 ioctl 查到的尺寸还是过渡态。这时如果立即用旧的
        self._bar_drawn（按 resize 前的宽度算出的物理行数）去做
        \x1b[1A\x1b[2K 相对擦除，擦除的行数和屏幕当前真实占用的行数
        很可能已经不一致——擦少了会堆叠，擦多了会越界吃掉状态栏上方
        的正常历史输出（更严重，且不可恢复）。详细原理见
        _on_sigwinch_settled() 的文档。

        所以这里只做"安全"的部分：清空 Console 宽度缓存、转发旧
        handler、调度一次 debounce 之后的 settle。真正的重绘统一
        交给 _on_sigwinch_settled()（等尺寸稳定下来之后）用安全路径
        （"_resize_settled" 消息，换行 + 放弃旧内容，不做相对擦除）
        处理，不在信号触发的瞬间就贸然相信旧的行数记账。

        ★ 更重要的一步（第二版修复新增）★：把 self._resize_unsettled
        置为 True，进入"不确定期"。这会让在 settle 真正触发之前、
        期间任何一次（最典型是 _refresh_loop 每 0.25 秒一次的常规
        刷新）调用到的 _erase_bar()/_erase_bar_direct() 都自动退化为
        安全模式（不做相对擦除，换行放弃旧内容），不需要逐一修改每个
        调用点——见 self._resize_unsettled 声明处的详细说明。
        """
        # 0. 进入 resize 不确定期：在 settle 确认尺寸稳定之前，所有
        #    相对擦除路径统一退化为安全模式。必须在清空宽度缓存之前
        #    设置，确保没有任何窗口期内的擦除调用能"赶在"标志生效前
        #    用旧记账做相对擦除。
        self._resize_unsettled = True
        # 1. 让 rich Console 丢弃宽度缓存，下次渲染自动重测（仅影响走
        #    Console 渲染的 panel/markdown/syntax 等内容的换行宽度）。
        try:
            self._console._width = None
            self._console._height = None
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
            pass
        # 2. 转发给之前的 handler（如最初进程启动时已有的 handler）
        if callable(self._prev_sigwinch):
            try:
                self._prev_sigwinch(signum, frame)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
                pass
        # 3. 调度一次"稳定后再确认"的延迟重绘（debounce）。真正的重绘
        #    动作（以及对 _bar_drawn 的处理）全部放在 settle 阶段，
        #    见 _on_sigwinch_settled() 的详细说明——这里不直接投递
        #    "redraw"，避免在尺寸还未稳定、reflow 尚未完成时就用
        #    可能已经失真的 _bar_drawn 做有风险的相对擦除。
        self._schedule_sigwinch_settle()

    def _rearm_sigwinch(self) -> None:
        """
        (重新) 把 self._on_sigwinch 挂为 SIGWINCH 的 OS 级 handler。
        必须在每次退出阻塞输入（_exit_input_mode）时调用——见上方
        构造函数里的详细说明：prompt_toolkit 在 .prompt() 期间会把
        handler 偷走，返回时还会把它复位成 SIG_DFL，不会自动还给我们。
        只在主线程调用（SIGWINCH/CPython signal 限制决定了这一点，
        本项目里 _enter_input_mode/_exit_input_mode 也始终在主线程
        执行，与该限制天然一致）。
        """
        _signal = getattr(self, "_signal_mod", None)
        if _signal is None or not hasattr(_signal, "SIGWINCH"):
            return
        try:
            _signal.signal(_signal.SIGWINCH, self._on_sigwinch)
        except (ValueError, OSError):
            # 非主线程调用时 signal.signal 会抛 ValueError；静默忽略，
            # 不影响功能（下一次主线程路径仍会重新挂载）。
            pass

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

    # ── raw-output 控制 ──────────────────────────────────────────────────
    # 运行期切换是否跳过 <tool_use> 流式过滤、原样显示模型全部输出。
    # 典型用法同 set_simple_mode：app.py 解析完 CLI 参数后调用一次。
    # 切换时顺手重置过滤器内部状态（_suppress_stream / _pending_stream），
    # 避免切换前残留的"正处于标签内"状态影响切换后的行为。

    def set_raw_output(self, enabled: bool) -> None:
        self._raw_output = bool(enabled)
        self._stream_filter_reset()

    def is_raw_output(self) -> bool:
        return self._raw_output

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

    def get_width(self) -> int:
        """
        返回当前终端的真实列宽，供状态栏内容构建方（plan_display.py /
        status_bar.py）做「按显示宽度动态截断」，而不是写死字符数。

        背景：plan_display.py 里曾经把 goal/title 按固定字符数截断
        （如 [:40]、[:36]），这在桌面宽终端上没问题，但放到 Termux 等
        窄屏移动端（典型 30~45 列）上，配合中文字符/emoji 的双宽显示，
        仍然很容易让单条状态栏文字超出实际列宽，被终端自动折成多行
        物理行，与"以为只占 1 行"的记账假设不一致。
        提供这个方法让上层按当前真实宽度的比例动态截断，从源头减少
        折行概率（而不是仅靠 _physical_line_count() 事后纠正记账）。

        不可用 TTY（如管道、测试环境）时返回一个保守的默认值，
        不抛异常、不依赖 isatty。
        """
        try:
            return max(20, self._console.width)
        except Exception:
            return 80

    def focus_next_task(self) -> None:
        """切换到下一个 task（tab 列表循环）。"""
        self._q.put(_Msg("_focus_cycle", +1))

    def focus_prev_task(self) -> None:
        """切换到上一个 task（tab 列表循环）。"""
        self._q.put(_Msg("_focus_cycle", -1))

    # ═══════════════════════════════════════════════════════════════════════
    # 输入通道（阻塞，主线程调用）
    # ═══════════════════════════════════════════════════════════════════════

    def request_input_lock(self, locked: bool) -> None:
        """
        线程安全地设置/清除"远端忙碌"标志（供 daemon.py 的 observer 线程
        跨线程调用）。

        locked=True：
          1. 置位 self._remote_busy，此后 _read_line() 不会再开启新的
             阻塞输入。
          2. 如果本端此刻已经在阻塞的 ptk .prompt() 里，通过
             loop.call_soon_threadsafe() 把 app.exit(exception=...) 安全
             调度到 ptk 自己的事件循环线程上执行（不能跨线程直接调用
             app 的方法——同样的理由见 _flush_pending_during_input() 里
             对 run_in_terminal 的说明），强制其抛出 _RemoteTurnInterrupt，
             .prompt() 调用方（_read_line）会原样把这个异常继续往上抛，
             一路传到 daemon.py 的 REPL 主循环，由它改成展示"等待中"。

        locked=False：
          清除标志，允许 _read_line() 重新开启正常的阻塞输入。
        """
        if locked:
            self._remote_busy.set()
            app = self._active_ptk_app
            if app is not None:
                loop = getattr(app, "loop", None)
                if loop is not None and not loop.is_closed():
                    def _kick() -> None:
                        try:
                            if not getattr(app, "is_done", True):
                                app.exit(exception=_RemoteTurnInterrupt())
                        except Exception:
                            pass
                    try:
                        loop.call_soon_threadsafe(_kick)
                    except Exception:
                        pass
        else:
            self._remote_busy.clear()

    def is_input_locked(self) -> bool:
        """当前是否处于"远端忙碌"锁定态（见 request_input_lock()）。"""
        return self._remote_busy.is_set()

    def prompt_user(self, prompt_text: str = "") -> str:
        """
        REPL 用户输入。
        阻塞前确保屏幕上没有状态栏干扰，输入完成后恢复状态栏。

        daemon 适配：`self._capture_mode` 为 True 时，说明当前正在
        run_captured() 里执行一个由远程（daemon connected）客户端发来的
        slash 命令——这个调用栈跑在服务端 AgentRunner 线程上，没有真正
        连着的本地终端，盲目走下面的 `_read_line()` 会永久阻塞在一个
        不存在的输入上（此前 `/goal <目标>` 卡死就是这个原因，且不止
        `/goal` 一个命令会这样：任何 slash 命令内部只要调用
        `prompt_user()` 都会中招）。这里统一改为走
        `mini_agent.interaction.ask()`，把请求转发给远程客户端，
        由它来回答，而不是假设有一个可读的本地终端。
        """
        if self._capture_mode:
            return self._remote_prompt(prompt_text)
        self._enter_input_mode()
        try:
            return self._read_line(prompt_text)
        finally:
            self._exit_input_mode()

    def _remote_prompt(self, prompt_text: str = "") -> str:
        """`prompt_user()` 在 capture_mode 下的实现：通过 interaction 网关
        向远程客户端要一行输入，而不是读本地 stdin。"""
        try:
            from mini_agent import interaction
        except Exception:
            return ""

        def _local_read(interrupt_event):
            # capture_mode 下这个调用是代表"远程客户端"在等输入，不应该被
            # daemon 操作者自己的本地终端抢答，所以这里只是阻塞等
            # interrupt_event（即等 HTTP 那一路真正给出答案），而不是立刻
            # 返回 None——立刻返回会被 interaction.ask() 误判成"本地没有
            # 答案"从而过早强制结束等待，HTTP 端还没来得及回答就超时。
            interrupt_event.wait()
            return None

        result = interaction.ask(
            "repl_prompt", {"prompt_text": prompt_text}, _local_read,
        )
        return (result or {}).get("answer") or ""

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
                # ── 可中断模式 ─────────────────────────────────────────
                # [BUGFIX] 见 _wait_stdin_readable() 的详细说明：不再为每次
                # 调用起一条新的 stdin 读取线程（那条线程在被 HTTP 端抢先
                # 打断后无法被终止，会变成僵尸线程，日后真实吃掉用户下一次
                # 输入，表现为"看到提示符却输入不进去"）。POSIX 下改用
                # select() 轮询（不消费数据，不留线程）；Windows 因为
                # select() 不支持 stdin 这类文件对象，退化回旧的线程实现
                # （Windows 场景下这个 bug 概率较低，暂不做等价修复）。
                if sys.platform != "win32":
                    while True:
                        ready = _wait_stdin_readable(0.2)
                        if ready:
                            break
                        if interrupt_event.is_set():
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                            try:
                                from mini_agent.permissions import _InterruptedByHTTP
                            except ImportError:
                                class _InterruptedByHTTP(Exception): pass
                            raise _InterruptedByHTTP()
                    try:
                        line = sys.stdin.readline()
                    except (EOFError, KeyboardInterrupt):
                        line = ""
                else:
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

    def interruptible_prompt(
        self,
        prompt_text: str = "\n> ",
        interrupt_event=None,
    ) -> Optional[str]:
        """
        自由文本、可中断的本地输入读取——与 confirm() 共享同一套
        _enter_input_mode()/_exit_input_mode() 双路协调机制，但不像
        confirm() 那样把结果限制成单字符选项，而是原样返回整行文本
        （用于 /goal 协商这类需要用户输入任意修改意见的场景）。

        背景（真实复现过的 bug）：goal_mode_cmd.py 之前直接
        sys.stdout.write("\\n> ") + 独立读 stdin，完全绕过了
        Terminal 的 _enter_input_mode()/_refresh_paused 机制——状态栏
        刷新线程（每 250ms）仍在正常运行，会不断擦除/重绘底部状态栏，
        而这次"裸写"的提示符和用户输入内容完全不在 Terminal 的
        _bar_drawn 记账范围内，于是被状态栏的 erase/redraw 循环反复
        覆盖，表现为"看不到提示符""输入内容一闪而过被冲掉"。

        interrupt_event: 可选 threading.Event。为 None 时退化为普通阻塞
                         读取；不为 None 时若该 Event 被外部 set()
                         （典型是 HTTP 端已经先给出答案），读取会被中断，
                         返回 None，调用方应把这理解为"本地未能给出答案"。

        返回：用户输入的原始一行（已去掉尾部换行，不做 strip/lower），
              或者 None（被中断 / EOF）。
        """
        import threading as _threading

        self._enter_input_mode()
        try:
            sys.stdout.write(prompt_text)
            sys.stdout.flush()

            if interrupt_event is not None:
                # [BUGFIX] 和 confirm() 同样的根因修复：不再起新线程读 stdin，
                # 详见 _wait_stdin_readable() 的说明。
                if sys.platform != "win32":
                    while True:
                        ready = _wait_stdin_readable(0.2)
                        if ready:
                            break
                        if interrupt_event.is_set():
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                            return None
                    try:
                        line = sys.stdin.readline()
                    except (EOFError, KeyboardInterrupt):
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        return None
                else:
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

                    while True:
                        if stdin_done.wait(timeout=0.2):
                            break
                        if interrupt_event.is_set():
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                            return None

                    line = result_holder[0] if result_holder else ""
            else:
                try:
                    line = sys.stdin.readline()
                except (EOFError, KeyboardInterrupt):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return None

            if not line:
                return None  # EOF
            if not line.endswith("\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
            return line.rstrip("\n")
        finally:
            self._exit_input_mode()

    # ── 输入模式管理 ──────────────────────────────────────────────────────

    def run_captured(self, fn: Callable[[], None]) -> str:
        """
        在\"影子控制台\"里执行 fn()，把它触发的所有 term.print()/rule()/panel()/
        syntax()/markdown() 输出捕获成纯文本返回，不触碰本地真实屏幕（不擦、不画
        状态栏、不影响本地终端正在展示的任何内容）。

        用途：daemon HTTP 端远程执行 slash 命令（如 web/其他 CLI 客户端发来的
        "/skills"、"/stats" 等）时，命令处理函数（cli/commands.py 里那些
        handle_xxx）内部一律调用 term.print() 之类的方法——完全不需要为了
        "捕获输出返回给远程调用方" 而去改这些函数本身，只需要在调用前后临时
        把 self._console 换成一个写内存缓冲区的 rich Console，并置位
        self._capture_mode（见 _handle() 里对这个标志的检查）。

        实现要点：
          - fn() 内部对 term.print() 等的调用只是把消息放进队列（self._q），
            真正的渲染发生在渲染线程里、异步执行。所以 fn() 调用完之后必须
            用"哨兵消息 + join()"确认渲染线程已经把这批消息全部处理完，
            才能安全地读取缓冲区内容——否则会读到不完整的输出。
          - 前后各投一次哨兵：前一次是为了确保开始捕获之前，队列里不会有
            残留的、本该被当作"正常内容"处理的旧消息混进这次捕获里；
            后一次才是真正等待 fn() 产生的消息处理完毕。
          - 用 try/finally 确保即使 fn() 抛异常，self._console 和
            self._capture_mode 也一定会被正确恢复，不会让本地终端从此
            永久性地"哑掉"（后续所有输出都被无声吞进一个没人读的缓冲区）。
        """
        import io
        from rich.console import Console as _RichConsole

        self._q.put(_Msg("_noop", None))
        self._q.join()

        old_console = self._console
        old_capture = self._capture_mode
        buf = io.StringIO()
        try:
            width = old_console.size.width
        except Exception:
            width = 100
        self._console = _RichConsole(
            file=buf, force_terminal=False, color_system=None,
            highlight=False, width=max(width, 60),
        )
        self._capture_mode = True
        try:
            fn()
        finally:
            self._q.put(_Msg("_noop", None))
            self._q.join()
            self._console = old_console
            self._capture_mode = old_capture
        captured_text = buf.getvalue()

        # ★ 镜像写回真实控制台（daemon 场景下即 daemon.log）───────────────
        # 背景：daemon 进程以 --daemon-mode 后台运行时，stdout/stderr 被
        # 重定向到 .agent/daemon.log，但这个进程本身没有真正的交互式
        # 终端在读输入——它显示什么、不显示什么，只对"事后能不能从
        # daemon.log 追溯排查"这一件事有意义。
        #
        # 之前的实现：run_captured() 期间 self._console 被整个换成一个
        # 只写内存缓冲区的 rich Console，fn() 内部所有 term.print() 调用
        # 都进了这个缓冲区，只在函数返回时作为一整段字符串交给调用方
        # （api/server.py 里当 turn_done 的 text 发出去）。这段内容确实
        # 会通过 _install_output_hook() 打的 print_info/print_warning 等
        # 补丁转发成 SSE 事件，所以已连接的客户端（daemon connected CLI、
        # web 看板）能实时看到；但 daemon 进程自己的 stdout/daemon.log
        # 完全没有拿到任何一份拷贝——对于 /goal 这种会话式协商（多轮
        # ask/confirm，中间夹杂 tool_call/info 等大量事件）尤其致命：
        # 出问题以后翻 daemon.log，完全看不到 agent 当时在跟谁商量什么、
        # 执行了什么，只能靠已连接客户端当时有没有截图。
        #
        # 修复：fn() 执行完、_console 已经换回真实控制台之后，把整段
        # 捕获到的文本重新回放一次（用 Text() 包装成"已知安全的纯文本"，
        # 不会被 rich 当作 markup 二次解析——captured_text 本身已经是
        # rich 渲染过的最终文本，理由和 renderer.py::print_tool_result()
        # 里对不可信内容用 Text() 包装一致）。加一行来源分隔线，方便在
        # daemon.log 里区分"这是哪一次远程 slash 命令触发的输出"。
        #
        # 权衡：这是"执行完之后一次性补录"，不是逐条实时镜像——run_captured()
        # 本来就设计成\"完全不接触真实屏幕\"，改成实时双写需要更大改动
        # （teeing 两个 Console 各自的渲染状态），而 daemon 场景下这里
        # 只是为了留痕排查，不要求实时性，delay 到 fn() 结束后一次性写
        # 完全够用，风险也更小。
        if captured_text.strip() and not self._capture_mode:
            try:
                from rich.text import Text as _CapturedText
                self.print("[dim]── run_captured output (daemon local trace) ──[/dim]")
                for line in captured_text.splitlines():
                    self.print(_CapturedText(line))
                self.print("[dim]── end ──[/dim]")
            except Exception:
                pass

        return captured_text

    def _enter_input_mode(self) -> None:
        """
        进入阻塞输入前的准备：
        0. ★ 若 raw key listener（方向键/Ctrl+C 监听器，由 repl.py 在
           run_turn() 期间启动）当前处于活跃状态，先把它停掉。

           背景（真实复现过的 bug：" 用户确认的时候看不到用户输入"）：
           raw_key_listener 为了监听方向键/Ctrl+C 而用 termios 把终端
           设成 cbreak 模式，其中显式关闭了 ECHO（见 raw_key_listener.py
           的 _setup()：`new_attrs[3] &= ~(termios.ECHO | ICANON | ISIG)`）。
           它的生命周期是整个 run_turn()——但 ask_user_confirm /
           ask_user / ask_user_choice 这几个工具，以及工具执行权限的
           confirm() 审批提示，都是在 run_turn() *内部*被调用的（agent
           在工具调用过程中触发）。也就是说，这些工具走到这里阻塞等待
           用户输入时，raw key listener 还在跑、终端的 ECHO 依然是关闭
           状态——用户这时候敲的字符，终端不会回显，我们自己的代码也
           没有显式把输入内容打印回去，于是用户看不到自己刚刚输入了
           什么，只会在按下回车后突然看到"User confirmed"之类的结果，
           体验上就是"输入凭空消失"。

           修复：每次进入阻塞输入前，如果 listener 正活跃，就先停掉它
           （_teardown() 会把 termios 设置还原成进入前的状态，包括重新
           打开 ECHO），让 sys.stdin.readline()/input() 在正常的、有
           回显的终端模式下读取；同时记录"它之前是活跃的"这个事实
           （self._key_listener_was_active），供 _exit_input_mode() 在
           恢复阻塞输入流程的最后一步重新把它启动回去——run_turn() 的
           其余部分（用户回答之后，agent 还要继续跑）仍然需要方向键/
           Ctrl+C 监听，不能永久关掉。

           对 prompt_user()（轮次之间，在 run_turn() 返回之后才会被
           调用）这条路径而言，listener 这时候本来就是停着的（repl.py
           的 finally 块已经在 run_turn() 结束时调用过 listener.stop()）
           ——这里的 .active 检查会正确判定为 False，不会做任何事，
           也不会在 _exit_input_mode() 时意外把它重新启动起来。
        1. 设置暂停标志（_refresh_paused），通知 refresh_thread 和 status_bar
           push_loop 停止向队列投递新消息
        2. 投双重哨兵 + join，彻底排空队列（含提示文字 + pause 前可能已入队的
           残余 _refresh / redraw 消息）。注意：此时 _input_blocking 仍是
           False——这是必须的，排空阶段里可能还混着"上一轮对话遗留、
           本就该正常显示"的 stream/print 消息（比如 run_turn() 刚
           结束但渲染线程还没来得及处理完最后几条），这些必须被正常
           处理写到屏幕上，不能被误判为"阻塞期消息"缓冲起来推迟显示。
        3. ★ 双重 join 确认队列真正空闲的那一刻，立刻置位
           _input_blocking（在 _erase_bar_direct() 之前）。

           这是修复一个真实复现过的竞态 bug 的关键改动：如果像旧版本
           那样把置位动作放在 _erase_bar_direct() 之后，中间就会出现
           一个"队列已确认排空，但 _input_blocking 还是 False"的窗口
           ——这个窗口跨越了整个 _erase_bar_direct() 的执行时间（一次
           真实的 stdout 写操作，不是几个字节码指令那么短）。后台线程
           （典型如会话摘要生成 mini-agent-summary 线程）调用
           term.print() 恰好落在这个窗口里时，_handle() 的缓冲判断
           （if self._input_blocking and kind in (...)）会判定为
           "不在阻塞期"，于是这条消息被当作普通消息正常处理——渲染
           线程会直接写 stdout（print 内容 + 可能的状态栏重绘），这个
           写动作和主线程紧接着即将启动的 prompt_toolkit 输入行渲染
           几乎同时发生，二者对同一个 stdout 的写入没有任何协调，会
           交织出文字粘连、状态栏在输入提示符旁边冒出来等乱码画面
           ——这正是实际环境里复现过的 bug（"刚好触发 session memory
           生成的时候会导致无法输入，输入的文字会被立刻擦除"）。

           把置位动作提前到 _erase_bar_direct() 之前（而不是之后），
           把这个竞态窗口从"一整次 stdout 写操作的时长"压缩到几乎为
           零（只剩一行属性赋值，CPython 里这种简单赋值在 GIL 保护下
           是原子的），同时又不会影响上面第 2 步排空阶段的正确性
           （那一步本就需要 _input_blocking 为 False）。
        4. 直接擦除状态栏（此时渲染线程空闲，无任何后台线程会写屏幕，
           安全直接操作；_input_blocking 已经是 True，从此刻起任何
           新到达的后台线程消息都会被正确缓冲）

        双重哨兵原因：
          设置 _refresh_paused 后，status_bar._push_loop 可能正处于
          sleep 结束、检查标志之前的窗口，已向队列投入 update_statusbar +
          redraw_statusbar 两条消息。第一个哨兵消费完这些残余消息后，
          push_loop 在下一轮 sleep 结束前不会再投新消息；但若 push_loop 恰好
          在第一个哨兵入队前完成了检查（标志已 set，但消息已在队列），
          第二个哨兵确保渲染线程真正空闲、无残余 redraw 待处理。
        """
        # 0. 暂停 raw key listener（若活跃），恢复正常回显，供下面的
        #    sys.stdin.readline()/input() 使用——见上方详细说明
        self._key_listener_was_active = False
        try:
            from mini_agent.ui.raw_key_listener import get_listener as _get_key_listener
            _listener = _get_key_listener()
            if _listener.active:
                _listener.stop()
                self._key_listener_was_active = True
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
            pass
        # 1. 告知所有后台线程停止向队列投递消息
        self._refresh_paused.set()
        # 2a. 第一个哨兵：排空 pause 前可能已入队的残余消息（含提示文字）
        self._q.put(_Msg("_noop", None))
        self._q.join()
        # 2b. 第二个哨兵：确认渲染线程在处理完所有残余 redraw 后真正空闲
        self._q.put(_Msg("_noop", None))
        self._q.join()
        # 3. 排空确认完成的瞬间立刻置位，把竞态窗口压缩到最小
        self._input_blocking = True
        self._input_blocking_since = time.monotonic()
        # 4. 此时渲染线程空闲，且新消息已能被正确缓冲，安全直接操作
        self._erase_bar_direct()

    def _exit_input_mode(self) -> None:
        """
        输入完成后：
        1. 重新挂载 SIGWINCH handler（见构造函数注释：prompt_toolkit 的
           .prompt() 在 prompt_user() 路径下会偷走并复位这个 handler，
           confirm() 的 readline 路径虽然自己不偷，但如果在它之前已经
           走过一次 prompt_user()，handler 也早被复位成 SIG_DFL 了——
           所以这里两条路径都统一重新挂载，不区分调用来源）
        2. 取出阻塞期间积压的消息，连同一条 redraw 重新入队（此时
           _input_blocking 仍是 True——见下方详细说明，这是有意为之）
        3. 最后才清除 _input_blocking / _refresh_paused

        为什么"重新入队"和"清除标志"的顺序很重要：
          如果像早期版本那样先清除 _input_blocking、再循环把积压消息
          入队，中间会有一个短暂窗口——这段时间里如果恰好有另一条
          后台线程消息到达（比如积压消息本身还没入队完，又有新的
          term.print() 调用），_handle() 会因为 _input_blocking 已经
          是 False 而把它当作"正常"消息立刻处理，从而抢在本该更早
          产生的积压消息前面被渲染——造成时间顺序错乱的画面（新内容
          先出现，旧的积压内容后出现）。把清除标志的时间点推到最后
          （积压消息 + redraw 已经全部入队之后），可以保证：即使这段
          极短窗口里又冒出新消息，它也会被正确缓冲到（已重新置空的）
          _pending_during_input 里，而不会越过已经入队的积压内容、
          造成乱序。
        4. 最后，若 _enter_input_mode() 时暂停过 raw key listener
           （self._key_listener_was_active 为 True），把它重新启动
           回去——run_turn() 在用户回答之后还要继续跑，方向键/Ctrl+C
           监听不能永久关掉。对 prompt_user() 这条路径（listener 进入
           前本来就是停着的）而言，这里什么都不会做，不会意外把它
           启动起来。
        """
        self._rearm_sigwinch()
        pending, self._pending_during_input = self._pending_during_input, []
        for msg in pending:
            self._q.put(msg)
        # 重绘通过队列（让渲染线程来画）
        self._q.put(_Msg("redraw", None))
        self._refresh_paused.clear()
        self._input_blocking = False
        self._input_blocking_since = 0.0
        # 4. 恢复 raw key listener（如果进入输入模式前它是活跃的）
        if self._key_listener_was_active:
            self._key_listener_was_active = False
            try:
                from mini_agent.ui.raw_key_listener import get_listener as _get_key_listener
                _get_key_listener().start()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
                pass

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
        if not self._capture_mode and self._input_blocking and kind in (
            "print", "rule", "panel", "syntax", "markdown",
            "stream", "stream_end", "_resize_settled",
        ):
            self._pending_during_input.append(msg)
            return

        if self._simple_mode and not self._capture_mode:
            self._handle_simple(msg)
            return

        if kind == "print":
            args, kwargs = msg.payload
            if not self._capture_mode:
                self._erase_bar()
            self._console.print(*args, **kwargs)
            if self._capture_mode:
                pass
            elif kwargs.get("end", "\n") == "":
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
            if not self._capture_mode:
                self._erase_bar()
            self._console.rule(title, **kwargs)
            if not self._capture_mode:
                self._bar_suspended = False
                self._draw_bar()

        elif kind == "panel":
            content, kwargs = msg.payload
            if not self._capture_mode:
                self._erase_bar()
            self._console.print(Panel(content, **kwargs))
            if not self._capture_mode:
                self._bar_suspended = False
                self._draw_bar()

        elif kind == "syntax":
            code, language, kwargs = msg.payload
            if not self._capture_mode:
                self._erase_bar()
            self._console.print(Syntax(code, language, **kwargs))
            if not self._capture_mode:
                self._bar_suspended = False
                self._draw_bar()

        elif kind == "markdown":
            if not self._capture_mode:
                self._erase_bar()
            self._console.print(Markdown(msg.payload))
            if not self._capture_mode:
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
                        #
                        # 改用逐行向上擦除：先擦掉 _bar_drawn 行状态栏，
                        # 再额外上移 1 行到 prefix 行，然后 \x1b[0J 清到底。
                        # 理由：单次 \x1b[NA 在 Termux / vterm 等实现里
                        # 遇到滚动边界会被截断，导致实际上移行数偏少，
                        # 旧状态栏行残留；逐行方案每次只上移 1 行，不受
                        # 滚动边界截断影响，可靠性更高。
                        #
                        # ★ resize 不确定期保护（同 _safe_erase_lines_up()
                        # 的理由）：这里"多上移 1 行到 prefix 行"这个
                        # 假设本身也建立在 self._bar_drawn 真实可信的
                        # 基础上——如果终端刚发生过 resize、已显示内容
                        # 被 reflow，self._bar_drawn 对"现在"屏幕状态
                        # 可能已经失真，继续按它做相对擦除（包括这里
                        # 额外的 +1 行）同样有越界吃掉历史输出的风险。
                        # 这里不能复用 _safe_erase_lines_up()（它只处理
                        # 状态栏本身，不知道还要多上移 1 行到 prefix），
                        # 所以单独处理：不确定期内，不管 self._bar_drawn
                        # 是多少，统一退化为"直接换行、放弃状态栏和
                        # prefix 行原有内容"，再调用 _replay_open_line()
                        # 在全新的、干净的当前光标位置重新打印 prefix——
                        # 该方法本身只是 console.print()，不做任何相对
                        # 定位假设，在任意干净的新行起点调用都是安全的。
                        if self._resize_unsettled:
                            sys.stdout.write("\r\n")
                            sys.stdout.flush()
                            self._bar_drawn = 0
                            self._resize_unsettled = False
                        else:
                            out = sys.stdout
                            for _ in range(self._bar_drawn if self._bar_drawn > 0 else 0):
                                out.write("\r\x1b[1A\x1b[2K")
                            out.write("\r\x1b[1A\x1b[0J")  # 额外上移 1 行到 prefix 行，清到屏底
                            out.flush()
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
                # 同样改用逐行向上擦除，理由见 stream 分支注释。
                #
                # ★ resize 不确定期保护（理由同 stream 分支的对应位置）：
                # 不确定期内不信任 self._bar_drawn，统一退化为直接换行
                # 放弃旧内容，再调用 _replay_open_line() 在干净的新行
                # 重新打印 prefix。
                if self._resize_unsettled:
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    self._bar_drawn = 0
                    self._resize_unsettled = False
                else:
                    out = sys.stdout
                    for _ in range(self._bar_drawn if self._bar_drawn > 0 else 0):
                        out.write("\r\x1b[1A\x1b[2K")
                    out.write("\r\x1b[1A\x1b[0J")  # 额外上移 1 行到 prefix 行，清到屏底
                    out.flush()
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

        elif kind == "_resize_settled":
            # resize 安静期过后的安全重绘——不走 "redraw" 的相对擦除
            # 路径。详细原理见 _on_sigwinch_settled() 的文档：resize
            # （尤其 Termux 切后台/前台）会让终端把已显示内容按新列宽
            # reflow，旧的 self._bar_drawn 记账对"现在"的屏幕状态已经
            # 失真，继续用它做 \x1b[1A\x1b[2K 相对擦除，擦多了会越界
            # 吃掉状态栏上方的正常历史输出，擦少了会堆叠残留。
            #
            # 实际的"换行放弃旧内容 + 归零 + 清除 self._resize_unsettled
            # 标志"全部由 _draw_bar() 内部调用的 _safe_erase_lines_up()
            # 统一处理（见该方法文档里"标志清除的关键设计决定"一节）。
            # 这里只需要直接调用 _draw_bar()，不需要重复手写擦除逻辑。
            #
            # ★★★ 关于标志为何不会再永久卡死（修复历史回归）★★★
            # 早期版本曾经把"清除标志"的职责放在这个消息分支里、且
            # 只在 not streaming and not bar_suspended 时才清除——一旦
            # resize 恰好发生在流式输出过程中或 bar_suspended 阶段，
            # 这个分支会被跳过，标志永久卡在 True，之后**所有**正常的
            # _refresh 心跳都会持续触发换行而不是原地刷新，表现为状态栏
            # 不断把历史内容向下推挤、看起来像在持续向上吞掉正常输出，
            # 且不会自行恢复。现在清除职责下沉到 _safe_erase_lines_up()
            # 自己（任何调用它的地方，只要真正执行过一次换行放弃，
            # 就会立即清除标志），不再依赖这条消息分支是否被执行、
            # 是否命中某个特定的状态组合，从根本上消除了"卡死"的可能。
            #
            # 流式输出中或 prefix 行尚未画状态栏（_bar_suspended）时，
            # 跳过：流式中插入换行会撕裂正在进行的文本；prefix 阶段
            # 本来就 _bar_drawn == 0、没有旧内容需要放弃。两种情况都
            # 交给各自原有的收尾逻辑处理（stream_end 分支本身会调用
            # _erase_bar()/_draw_bar()，同样会经过 _safe_erase_lines_up()
            # 的保护，不需要这里额外兜底）。
            if not self._streaming and not self._bar_suspended:
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
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
                pass

        elif kind == "_force_end_stream":
            # 强制结束流式状态（异常恢复时使用）
            if self._bar_below_prefix:
                # 同样不能依赖 \x1b[NA 回到 prefix 行尾的列位置
                # （原因见 stream 分支的详细注释）：改用逐行向上擦除，
                # 再额外上移 1 行到 prefix 行，清到屏底后重新打印 prefix。
                #
                # ★ resize 不确定期保护（理由同 stream 分支的对应位置）。
                if self._resize_unsettled:
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    self._bar_drawn = 0
                    self._resize_unsettled = False
                else:
                    out = sys.stdout
                    for _ in range(self._bar_drawn if self._bar_drawn > 0 else 0):
                        out.write("\r\x1b[1A\x1b[2K")
                    out.write("\r\x1b[1A\x1b[0J")
                    out.flush()
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

        elif kind == "_resize_settled":
            # simple-mode 下从不维护状态栏（_bar_drawn 恒为 0，_draw_bar
            # 也从不被调用），resize 安静期的"放弃旧内容重画"逻辑在这里
            # 无意义——没有旧内容、没有光标控制承诺可破坏。但仍需清除
            # self._resize_unsettled 标志，避免它被永久卡在 True（即使
            # simple-mode 下不会有可见副作用，卡死的标志本身是隐患，
            # 万一未来运行时从 simple-mode 切换回正常模式会暴露出来）。
            self._resize_unsettled = False

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
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
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

    def _physical_line_count(self, lines: list[str]) -> int:
        """
        计算给定的一组「逻辑行」字符串，写到当前终端宽度下，
        实际会占用多少「物理行」（即终端真正自动换行后的行数）。

        背景（真实复现过的 bug，桌面正常、Termux 手机端状态栏反复堆叠）：
        早期实现里 self._bar_drawn 直接取 len(self._statusbar_lines)，
        即「字符串条数」。这隐含了一个不成立的假设：每条逻辑行在终端
        渲染后必然恰好占 1 个物理行。

        但 plan/task 状态栏里大量使用中文字符和 emoji（📋 ⚡ ✍️ ✓ ○ ◉ 等），
        这些字符在绝大多数终端里按 2 列宽渲染，而 plan_display.py /
        status_bar.py 里的长度截断（如 title[:36]）全部按 Python 字符数
        计算，不是按显示宽度。结果是同一条"逻辑行"，其真实显示宽度
        （cell width）可能比字符数多出 30%~50%。
        在桌面终端（通常 80~120 列）这点差距大多不会触发自动换行，
        所以"电脑上正常"；但 Termux 等移动端终端典型只有 30~45 列宽，
        一条偏长的状态栏文字很容易被终端自动折成 2 行甚至更多物理行。

        如果 _bar_drawn 仍按"逻辑条数"记账，下次 _erase_bar() 用
        \x1b[1A 上移的次数会比真实物理行数少，擦除不完整，旧内容
        残留在屏幕上，新内容画在残留下方——表现为状态栏反复堆叠、
        无法清理干净。

        修复：不再假设 1 逻辑行 = 1 物理行，而是对每条逻辑行算出真实
        显示宽度，结合当前终端列宽计算它会折成几行，再累加得到真正
        应该记账的物理行数。

        ★★★ 真实复现过的第二个、更隐蔽的严重 bug ★★★
        （比上面的"折行记账"问题更深一层，且与 resize 完全无关，
        哪怕终端宽高从未变化过、状态栏第一次画出来开始就持续发生）

        最初的实现直接对原始字符串调用 rich.cells.cell_len(line)。
        但 status_bar.py 里几乎每一行都带有大量 ANSI 颜色转义序列
        （如 "\033[36m"、"\033[90m"、"\033[0m"，文件里明确标注
        "ANSI 着色"）。cell_len() 只是按 Unicode 东亚宽度属性逐字符
        累加宽度，**完全不识别 ANSI 转义序列**——会把转义码本身的
        每一个字符（如 "\033[36m" 这 5 个字符）也当作"普通可见字符"
        计入宽度，而这些转义码在终端里会被直接解释执行、根本不占用
        任何屏幕列位置、不会推进光标。

        实测：一条带颜色码的状态栏行，cell_len() 算出的宽度可能比
        其真实显示宽度高估 50%~70%（颜色码越多，高估越严重）。
        在 Termux 等窄终端（典型 30~45 列）下，这种系统性高估很容易
        让一条本来只占 1 个物理行的文字，被误判成占 2 行甚至更多——
        于是 self._bar_drawn 被设置成一个比屏幕上真实占用行数更大
        的值。下一次擦除时，按这个被高估的值做 \x1b[1A 相对上移，
        多移动的那些行会直接越过状态栏的真实边界，越界擦进状态栏
        上方"正常历史输出"的区域。

        这个 bug 不依赖任何 resize 事件触发——只要终端宽度不够宽、
        状态栏内容带 ANSI 颜色码（几乎总是如此），从状态栏第一次
        被画出来开始就会持续发生，且每一次刷新都会重新计算一次
        （同样被高估的）宽度，持续越界擦除一些历史输出，看起来像
        "状态栏在不断地向上吞掉正常内容"——这正是比 resize reflow
        问题更根本、更持续性的根因。

        修复：用 rich.text.Text.from_ansi() 先正确解析掉 ANSI 转义
        序列（保留它们携带的样式信息用于其他渲染场景，但参与宽度
        计算时只统计真正可见的字符），再取解析结果的 .cell_len，
        得到的是真实的、终端折行会依据的可见宽度。

        终端宽度来源与 self._console 保持一致（同一份 resize 缓存，
        见 _on_sigwinch() 的失效逻辑），避免口径不一致引入新的偏差。
        """
        try:
            width = max(1, self._console.width)
        except Exception:
            width = 80  # 极端兜底：拿不到宽度时退化为不折行估算
        total = 0
        for line in lines:
            try:
                # ★ 关键：用 Text.from_ansi() 解析掉 ANSI 转义序列，
                # 只统计真正可见字符的显示宽度——转义码本身不占用
                # 任何屏幕列位置，绝不能被计入折行判断。
                w = Text.from_ansi(line).cell_len
            except Exception:
                try:
                    w = cell_len(line)
                except Exception:
                    w = len(line)
            # 每条逻辑行至少占 1 个物理行；超过终端宽度时按列宽折行。
            # 注意：空行（w == 0）也至少占 1 行。
            total += max(1, -(-w // width))  # ceil(w / width)
        return total

    def _safe_erase_lines_up(self) -> bool:
        """
        统一的"擦除前安全检查"：在任何地方要执行
        `for _ in range(self._bar_drawn): out.write("\\x1b[1A\\x1b[2K")`
        这种相对光标位移擦除之前，必须先调用这个方法。

        返回 True 表示"已经处理过了，调用方不需要再发相对擦除序列"
        （包括"resize 不确定期，已经换行放弃旧内容"和"_bar_drawn 本来
        就是 0，没什么可擦"两种情况）；返回 False 表示"可以放心地按
        正常方式做相对擦除"。

        ★★★ 本次修复要堵住的真正漏洞 ★★★
        第一版修复只保护了 _on_sigwinch_settled() 投递的 "_resize_settled"
        消息这一条路径，但状态栏最高频的刷新——_refresh_loop 每
        _refresh_interval（默认 0.25 秒）投递一次的 "_refresh" 消息，
        会直接调用 _erase_bar()；而 _erase_bar() 和 _draw_bar() 内部
        **各自独立**都写着一段 `if self._bar_drawn > 0: for _ in
        range(self._bar_drawn): ...\\x1b[1A\\x1b[2K...` 的相对擦除逻辑
        ——第一版完全没有改动这两处，等于完全没有保护到。而
        _SIGWINCH_DEBOUNCE_SECONDS（0.15 秒）比 _refresh_interval
        （0.25 秒）更短，意味着几乎每次 resize 都会在 settle 真正
        触发之前，先撞上至少一次未受保护的 "_refresh" → _erase_bar()
        → 相对擦除——这正是"切后台再切回来，还是会向上擦除之前的
        历史输出"问题持续存在的真正原因。

        修复：把检查下沉到这一个统一入口，_draw_bar()/_erase_bar()/
        _erase_bar_direct() 三处但凡要做相对擦除，都先调用它。只要
        self._resize_unsettled 为 True（resize 已发生但尚未确认安静
        稳定，覆盖从信号触发到 debounce 结束的整段窗口），就完全不发
        任何 \\x1b[1A，转而换行放弃旧内容、把 _bar_drawn 归零——不可能
        越界，因为没有任何相对位移操作。

        ★★★ 标志清除的关键设计决定（修复一个曾经导致标志永久卡死、
        每次刷新持续向上吞掉历史输出的严重回归）★★★
        清除 self._resize_unsettled 的职责放在这个方法内部完成，
        不依赖任何外部调用方（包括 "_resize_settled" 消息分支）事后
        补一句"处理完了，可以清除了"。原因：

        本方法只会在 render_thread 自己的线程里被同步调用（_draw_bar/
        _erase_bar/_erase_bar_direct 都运行在 render_thread），执行
        "换行+归零" 和调用方紧接着"用当前最新宽度重新建立记账"
        （_draw_bar() 里的 self._bar_drawn = new_count）是同一次函数
        调用链内连续完成的，中间不会被其他消息打断——不存在跨线程/
        跨消息的竞态窗口。也就是说，只要这个方法被调用过一次、执行
        了换行放弃旧内容，"不确定期"就已经被妥善处理完毕，可以立刻
        安全地恢复正常模式，不需要等待任何特定的外部消息。

        早期版本把清除职责放在 "_resize_settled" 消息处理分支里、
        而不是这里，导致一个致命漏洞：_safe_erase_lines_up() 真正
        被高频调用的地方是 _refresh（每 250ms 一次的常规心跳，跟
        resize 是否已经投递过 "_resize_settled" 消息完全无关）——
        如果某次 resize 触发后 "_resize_settled" 消息因为流式输出
        中/_bar_suspended 而被跳过（或者根本在 _input_blocking 期间
        从未投递），标志就再也没有机会被清除，之后**所有**正常的
        _refresh 心跳都会持续命中这里的换行分支——不是因为还在
        "resize 不确定期"，而是因为标志本身被卡死、永远读不到
        False。表现正是：宽高早已不再变化，却仍然每次刷新都换一行、
        持续把内容向下推挤，看起来像"不断向上吃掉历史输出"。
        """
        if self._bar_drawn == 0:
            self._resize_unsettled = False
            return True
        if self._resize_unsettled:
            out = sys.stdout
            out.write("\r\n")
            out.flush()
            self._bar_drawn = 0
            self._resize_unsettled = False
            return True
        return False

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
        # ★ 关键修复：new_count 必须是「真实物理行数」，不是字符串条数。
        # 见 _physical_line_count() 的详细说明——中文/emoji 等宽字符会让
        # 一条逻辑行在窄终端（典型如 Termux 手机端）上被自动折成多行，
        # 如果继续用 len(self._statusbar_lines) 记账，下次擦除时上移的
        # 行数会比真实占用的物理行数少，导致旧内容残留、反复堆叠。
        new_count = self._physical_line_count(self._statusbar_lines)
        # ★ 第二版修复：擦旧内容之前先经过安全检查（见 _safe_erase_lines_up
        # 的详细说明）。resize 不确定期内，这里不会发出 \x1b[1A，已经在
        # 安全检查内部换行放弃了旧内容，_bar_drawn 也已归零——后面直接
        # 从新行写入即可，不需要、也不能再额外做相对擦除。
        if not self._safe_erase_lines_up() and self._bar_drawn > 0:
            # 逐行向上擦除旧内容，再回到起始行准备重写。
            # 比"一次 \x1b[NA\x1b[0J"更可靠：Termux / vterm 等实现里
            # \x1b[NA 在滚动边界附近会被截断，导致上移行数少于预期，
            # \x1b[0J 清除的起点偏低，旧的状态栏行残留在屏幕上不被
            # 擦除，下次绘制直接追加，形成反复堆叠的视觉 bug。
            # 逐行策略：每次只上移 1 行（\x1b[1A），然后 \x1b[2K 擦除
            # 当前整行，循环 _bar_drawn 次后光标已回到状态栏首行上方
            # 的最后一行正常内容处；最后 \x1b[0J 清除从此往下的余量
            # （兜底：防止行数缩减时旧的末尾行残留）。
            #
            # ★★★ 真正的根因（比之前所有假设都更基础、更隐蔽）★★★
            # \x1b[1A（CUU，光标上移）只改变行号，\x1b[2K（EL，清除整行）
            # 不改变光标列号——这两个控制序列都不会把列位置归零。而
            # 写入每一行内容时，代码只发送 "line + \n"（裸 LF），LF 在
            # VT100/ANSI 标准里只移动到下一行，并不像 CRLF 那样把列号
            # 归零。多数桌面终端"看起来正常"，是因为底层 tty 驱动开启
            # 了 termios 的 ONLCR 标志，会把输出流里的 LF 自动翻译成
            # CRLF——这是终端驱动层的隐式行为，不是我们代码本身保证的，
            # 一旦这个翻译没有发生（如 Termux 的 PTY 实现细节差异、
            # 或任何其他不保证 ONLCR 语义的环境），光标列位置会在每一
            # 行写完之后停留在"该行内容长度"对应的列，而不是列 0。
            #
            # 后果：下一轮 \x1b[1A 上移之后，光标行号正确归位了，但列号
            # 仍然停留在"上一次最后一行内容长度"对应的位置——重新写入
            # 新内容时，会从这个错误的列开始覆盖，导致：
            #   - 新内容里靠前的部分被写到了行中间，行首的旧内容残留
            #     没有被覆盖（\x1b[2K 虽然清过一次，但这次重写又从错误
            #     列开始写，相当于把刚清空的行又"部分性"地弄脏）；
            #   - 多行状态栏时，每一行残留的列偏移还会逐行累积、相互
            #     干扰，造成位置越写越偏、像是不断向某个方向"滚动堆叠"
            #     的视觉效果——这正是用户反馈的"擦除好像没生效、内容
            #     持续往上吃掉历史输出"现象的真正根源，与 resize 是否
            #     发生、_bar_drawn 计数是否精确都没有关系，是从状态栏
            #     机制最初实现起就存在的基础控制序列缺陷。
            #
            # 修复：不依赖 ONLCR 这种隐式的、依赖外部环境配置的翻译。
            # 每次 \x1b[1A 之前先发送 \r，显式把列位置归零，再上移、
            # 再清行——保证每一步操作开始时光标列位置都是已知的 0。
            for _ in range(self._bar_drawn):
                out.write("\r\x1b[1A\x1b[2K")
            out.write("\r\x1b[0J")
        # ★ 同样的根因，同样的修复：每条新行内容写入前先发 \r 归零列
        # 位置，再写内容，再发 \n 换行——不依赖 ONLCR 的隐式翻译，确保
        # 不管当前终端/PTY 是否真的把 LF 自动翻译成 CRLF，这里写出的
        # 每一行都从确定的列 0 开始，下一行也是如此。
        for line in self._statusbar_lines:
            out.write("\r" + line + "\n")
        out.flush()
        self._bar_drawn = new_count

    def _erase_bar(self) -> None:
        # 同上：防御性保护，simple-mode 下绝不发出擦除序列。
        if self._simple_mode:
            return
        if not _IS_TTY or self._bar_drawn == 0:
            return
        # ★ 第二版修复：先经过统一的安全检查。resize 不确定期内，这里
        # 直接返回（已经换行放弃了旧内容、_bar_drawn 已归零），不发
        # 任何 \x1b[1A——这正是堵住"_refresh 高频路径未受保护"漏洞的
        # 关键一步，见 _safe_erase_lines_up() 的详细说明。
        if self._safe_erase_lines_up():
            return
        # 逐行向上擦除，与 _draw_bar() 保持一致——理由见 _draw_bar() 注释
        # （★ 真正根因：裸 \n 不归位列，\x1b[1A 之前必须先 \r 显式归零）。
        out = sys.stdout
        for _ in range(self._bar_drawn):
            out.write("\r\x1b[1A\x1b[2K")
        out.write("\r\x1b[0J")
        out.flush()
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
        # ★ 第二版修复：同样先经过统一的安全检查，理由同 _erase_bar()。
        if self._safe_erase_lines_up():
            self._bar_suspended = False
            return
        # 逐行向上擦除，与 _draw_bar() 保持一致——理由见 _draw_bar() 注释
        # （★ 真正根因：裸 \n 不归位列，\x1b[1A 之前必须先 \r 显式归零）。
        out = sys.stdout
        for _ in range(self._bar_drawn):
            out.write("\r\x1b[1A\x1b[2K")
        out.write("\r\x1b[0J")
        out.flush()
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

        raw-output 开启时（self._raw_output=True）：直接原样返回 token，
        不做任何标签识别/缓冲——这是"显示模型所有原始输出"开关的核心
        实现位置，调用方（stream / stream_end 等四处分支）完全不需要
        改动，因为它们只是把这里的返回值写到 stdout。

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
        if self._raw_output:
            return token
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
                # 看门狗：仅用于兜底"_exit_input_mode() 因某个未预见的
                # 异常路径没有被正确调用，标志永久卡死"这一情况。
                #
                # 注意：_input_blocking 持续为 True 的时长 *不能* 简单
                # 等同于"人类按键间隔"——它覆盖的是用户读完提示、思考、
                # 打完一段完整回答（甚至中途切到后台再切回来）的整段
                # 等待，完全可能轻松超过几分钟，这是正常人类行为，不是
                # bug。_INPUT_BLOCKING_TIMEOUT 因此被设得很长（远超任何
                # 正常交互场景），只在"真的已经没人会回来"的极端情况下
                # 才介入；如果调低这个阈值，会导致看门狗在用户仍然合法
                # 停留在 prompt_toolkit 的 .prompt() 或 confirm() 的
                # readline() 里时就误判"卡死"、强制把 _refresh_paused
                # 清掉——refresh_thread 会从此重新开始按周期重绘状态栏，
                # 跟用户正在输入的那一行抢屏幕，造成"一直在刷新、看不到
                # 自己刚打的字"的画面（真实复现过的回归 bug，详见构造
                # 函数里 _INPUT_BLOCKING_TIMEOUT 定义处的完整说明）。
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
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
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

    def _ptk_flush_loop(self) -> None:
        """
        每 _PTK_FLUSH_INTERVAL 秒检查一次：如果本端正阻塞在
        prompt_toolkit 的 .prompt() 里、且有积压的旁观消息，就用
        run_in_terminal() 安全地把它们打印出来，不必等到本端自己提交
        输入才显示——见 __init__ 里这个线程启动处的详细说明。

        这个循环本身只做"判断要不要 flush"，真正的打印动作交给
        _flush_pending_during_input()（里面才会真正调用 run_in_terminal）。

        ★★★ 这里的"输入框是否为空"检查只是一个廉价的预过滤 ★★★
        在真正决定要不要 flush 之前，这里先做一次快速判断，避免明知道
        用户正在打字还去调度一次跨线程协程（有实际开销，也没必要）。
        但这个判断是从后台线程读 ptk 的 Buffer 状态，Buffer 本身不是
        线程安全的，而且从这里判断完到真正执行中间有调度延迟，判断结果
        可能过时——所以这里判断"看起来可以 flush"之后，真正权威、真正
        原子的判断在 `_flush_pending_during_input()` 调度到 ptk 事件循环
        线程里的 `_do_flush()` 协程里又做了一遍（那里才是安全的，因为
        跟 ptk 处理按键的逻辑跑在同一个线程/循环上，检查和动作之间没有
        任何 await 让出点，不会被"用户刚好在这中间按了回车"打断）。
        """
        while not self._ptk_flush_stop.is_set():
            self._ptk_flush_stop.wait(self._PTK_FLUSH_INTERVAL)
            if self._ptk_flush_stop.is_set():
                break
            if not self._input_blocking:
                continue
            app = self._active_ptk_app
            if app is None:
                # 降级路径（未安装 ptk，或 ptk 运行时异常后已降级到裸
                # readline）：没有"安全打印到输入行上方"的手段，只能
                # 维持旧行为——积压到 _exit_input_mode() 统一补打印。
                continue
            if not self._pending_during_input:
                continue
            try:
                buf = app.current_buffer
                if buf is not None and (buf.text or buf.complete_state is not None):
                    # 用户正在输入内容，或补全菜单正开着：不打扰，跳过
                    # 这一轮，下次输入框空了再 flush。
                    continue
            except Exception:
                # 拿不到当前 buffer 状态时，保守起见也跳过这一轮，
                # 不冒风险去 flush。
                continue
            self._flush_pending_during_input()

    def _flush_pending_during_input(self) -> None:
        """
        把 _pending_during_input 里积压的消息，通过 prompt_toolkit 的
        `in_terminal()` 异步上下文管理器安全地打印到当前输入行上方，
        然后让 ptk 自己重绘输入行。

        实现上复用渲染线程已经测试过的 _handle() 逻辑，而不是自己重新
        写一遍"怎么打印 print/stream/stream_end 等各种消息类型"——
        做法是：短暂把 _input_blocking 置回 False（这样 _handle() 不会
        再把这批消息当成"阻塞期消息"重新缓冲），把消息推回主队列，
        等渲染线程真正处理完，再把 _input_blocking 置回 True，继续
        缓冲这之后到达的新消息。这个"短暂置 False 再置回 True"的模式
        和 _exit_input_mode() 是同一个模式，唯一区别是这里不清除
        _refresh_paused、不重新挂载 SIGWINCH、不恢复 key listener——
        因为我们还没有真正退出输入模式，用户仍在 ptk 的 .prompt() 里，
        只是"顺便"把这段时间攒下的旁观消息打印出来。

        ★★★ 为什么不能直接调用 prompt_toolkit.application.run_in_terminal()
        ★★★
        `run_in_terminal(func)` 内部是 `ensure_future(run())`——它假定
        调用方当前就在 ptk 那个 Application 正在跑的 asyncio 事件循环
        线程里，直接 `ensure_future` 就能把协程挂到"当前线程的"事件
        循环上。但这个 flush 方法是从我们自己的后台线程
        （terminal-ptk-flush）调用的，这个线程根本没有正在运行的事件
        循环——`ensure_future` 拿到的要么是错误的循环、要么直接创建了
        一个从来没有被 run 过的协程对象，从而产生
        "coroutine 'run_in_terminal.<locals>.run' was never awaited"
        这个 RuntimeWarning，而且回调根本没有被真正执行到。

        正确做法：拿到 ptk Application 实际在跑的那个事件循环
        （`app.loop`，Application.run_async() 运行期间会把它设置好），
        用 `asyncio.run_coroutine_threadsafe()` 把协程真正提交到*那个*
        循环上执行，再阻塞等待结果——这才是官方文档里"从其他线程安全
        调度协程到指定事件循环"的标准写法。

        ★★★ 两个额外修的竞态（实测复现过：B 端提交的输入没有回显、
        C 端自己的回复内容错乱/缺字）★★★
        1. `self._q.join()` 是同步阻塞调用；如果直接在协程里调用它，
           等于在 ptk 的事件循环线程上执行一次阻塞 I/O 等待——asyncio
           是单线程协作式调度，这会把整个事件循环"冻住"，导致这段
           时间内 ptk 自己正在处理的按键事件（尤其是恰好在这个窗口
           按下的回车）被延后甚至处理异常，表现为提交后本该由 ptk
           自己保留在回滚区里的那行"You ❯ <文本>"回显丢失。
           修法：改成 `await loop.run_in_executor(None, self._q.join)`，
           把这个阻塞等待丢到线程池里去等，事件循环本身不被冻住。
        2. `finally` 里原来无条件把 `_input_blocking` 置回 True。但如果
           这次 flush 还没收尾，用户就已经按了回车提交——`_read_line()`
           会先把 `self._active_ptk_app` 置回 None，然后
           `_exit_input_mode()` 才会把 `_input_blocking` 置为 False（这
           才是"真正退出阻塞输入"的权威状态）。如果我们的 `finally` 在
           这之后才执行、还傻乎乎地把 `_input_blocking` 重新置为
           True，就会把"提交之后紧接着到来的、本该立刻显示"的内容
           （包括本端自己这一轮的回复）又错误地重新缓冲起来，之后跟
           下一次 flush 混在一起打印，就是实测里那种内容错乱、开头
           缺字的画面。
           修法：`finally` 里先检查 `self._active_ptk_app is app`——
           只有确认"我们还在同一次 .prompt() 调用里"，才把
           `_input_blocking` 置回 True；如果 App 已经变了（说明用户
           已经提交、真正的退出逻辑已经跑过了），就什么都不做，尊重
           `_exit_input_mode()` 已经设好的权威状态。
        """
        app = self._active_ptk_app
        if app is None:
            return
        loop = getattr(app, "loop", None)
        if loop is None or loop.is_closed():
            return

        try:
            from prompt_toolkit.application import in_terminal
        except ImportError:
            return

        async def _do_flush() -> None:
            # ★★★ 关键：这个"能不能 flush"的判断必须在这里、在协程里做，
            # 不能在 _ptk_flush_loop() 那个后台线程里做 ★★★
            # 之前是在后台线程里读 app.current_buffer.text 判断"输入框
            # 是否为空"，但 prompt_toolkit 的 Buffer 不是线程安全的，而且
            # 从后台线程判断完、到真正通过 run_coroutine_threadsafe 调度
            # 到这里执行，中间有真实的调度延迟——这段时间里用户完全可能
            # 已经按下了回车。也就是说后台线程看到的"输入框是空的"这个
            # 判断结果，到这里执行的时候可能已经过时了，且这里再重新拿到
            # 的可能是"提交那一刻的过渡态"。
            # 唯一真正安全的时机，是在这个协程里、拿到 app 之后立刻做这个
            # 判断——因为协程运行在 ptk 自己的事件循环线程上，只要这一段
            # 判断和后面的"取出 pending 并处理"之间不出现任何 await（没有
            # 让出控制权的点），就跟 ptk 处理按键的其它协程互斥，不可能
            # 被"回车刚好在这中间被处理"这种情况打断——这才是真正原子的
            # 检查点。这一版之前"只在输入框为空时才 flush"的判断放错了
            # 线程，等于没做到位，所以你还是复现到了"提交后自己的输入
            # 没有回显"。
            if self._active_ptk_app is not app:
                return
            try:
                # 除了 buffer 状态，再确认一下 Application 本身确实还在
                # 正常运行、没有处于退出过程中——用户按下回车之后，
                # accept-line 处理会先清空 buffer.text，但这时 Application
                # 可能还没有真正跑完退出流程；如果我们在这个过渡窗口里
                # 还去调用 in_terminal() 打断它，就可能跟它自己的退出/
                # 收尾渲染打架，表现为"提交后自己的输入没有回显"。
                # is_running=True 且 is_done=False，才是"确实还在稳定
                # 阻塞等待输入"的状态，此时 flush 才是安全的。
                if not getattr(app, "is_running", True) or getattr(app, "is_done", False):
                    return
                buf = app.current_buffer
                if buf is not None and (buf.text or buf.complete_state is not None):
                    return
            except Exception:
                return

            pending, self._pending_during_input = self._pending_during_input, []
            if not pending:
                return
            # 再确认一次：调度到真正执行之间可能已经过了一小段时间，
            # 用户完全可能已经提交了输入——这种情况下就不要再动
            # _input_blocking 了，把消息原样放回去，让正常的
            # _exit_input_mode() 补打印路径去处理，避免抢跑。
            if self._active_ptk_app is not app:
                self._pending_during_input = pending + self._pending_during_input
                return
            self._input_blocking = False
            try:
                for msg in pending:
                    self._q.put(msg)
                self._q.put(_Msg("redraw", None))
                await loop.run_in_executor(None, self._q.join)
            finally:
                if self._active_ptk_app is app:
                    self._input_blocking = True
                    self._input_blocking_since = time.monotonic()
                # else：用户已经提交、_exit_input_mode() 已经把
                # _input_blocking 设成了权威值 False，这里绝不能覆盖。

        async def _runner() -> None:
            async with in_terminal():
                await _do_flush()

        try:

            fut = asyncio.run_coroutine_threadsafe(_runner(), loop)
            # 有超时兜底：万一 ptk 那边恰好在退出/重建循环，不要把这个
            # 后台线程永久卡死——超时后消息仍留在 _pending_during_input
            # （_do_flush 还没机会清空它），下一个 flush 周期或
            # _exit_input_mode() 会重新尝试，不会丢失。
            fut.result(timeout=5.0)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.ui.terminal')
            pass

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

        self._active_ptk_app 的作用：
            记录当前正在阻塞等待输入的 ptk Application 实例。SIGWINCH 的
            "settle" 回调（见 _on_sigwinch_settled，跑在独立的 debounce
            定时器线程上，不是主线程）需要在 resize 尺寸真正稳定之后，
            强制 ptk 用最新尺寸重绘一次——但定时器线程不能直接操作 ptk
            内部状态（ptk 的渲染必须发生在它自己的事件循环线程）。
            `Application.invalidate()` 是 ptk 文档里明确标注的"线程安全"
            API，正是为这种"外部线程要求重绘"场景设计的，因此只需要
            持有 app 引用、跨线程调用 invalidate() 即可，不需要也不应该
            直接调用 renderer 内部方法。
        """
        # 远端忙碌（同一 session 里另一个客户端的 turn 正在处理）：不开启
        # 新的阻塞输入，直接抛出 _RemoteTurnInterrupt，交给上层 REPL 循环
        # 展示"等待中"，等对方 turn 结束、_remote_busy 被清除后再重新
        # 调用 prompt_user()。见 request_input_lock() 的说明。
        if self._remote_busy.is_set():
            raise _RemoteTurnInterrupt()

        if not getattr(self, "_ptk_failed", False):
            try:
                from prompt_toolkit.formatted_text import HTML

                if not hasattr(self, "_ptk_session"):
                    _completer = _build_slash_completer()
                    self._ptk_session = _build_ptk_session(_completer)

                html_prompt = prompt_text or HTML(
                    "<b><ansgreen>You</ansgreen></b><ansicyan> ❯ </ansicyan>"
                )
                self._active_ptk_app = self._ptk_session.app
                try:
                    result = self._ptk_session.prompt(html_prompt)
                    return (result or "").strip()
                except _RemoteTurnInterrupt:
                    # 由 request_input_lock(True) 通过 app.exit(exception=...)
                    # 触发：本端正打字的过程中，另一个客户端的 turn 开始了。
                    # 原样往上抛，不在这里吞掉——daemon.py 的主循环需要知道
                    # "这次没拿到真正的用户输入"，而不是把 None/空串当成
                    # 用户提交了空消息处理。
                    raise
                finally:
                    self._active_ptk_app = None
            except ImportError:
                pass  # 未安装 prompt_toolkit，直接降级
            except (KeyboardInterrupt, EOFError):
                raise  # 由上层处理
            except _RemoteTurnInterrupt:
                raise
            except Exception:
                # ptk 运行时异常（dumb terminal、Windows ConPTY 等），标记后降级
                self._ptk_failed = True

        # 降级：ANSI 提示符 + readline（无法跨线程安全打断阻塞的
        # sys.stdin.readline()，只能保证"开始前"检查一次 _remote_busy；
        # 已经在这里阻塞的情况下，只能等用户实际按回车提交后，由主循环
        # 在下一次调用 prompt_user() 前的检查里补上"等待中"展示）。
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
        # 0. 取消尚未触发的 SIGWINCH debounce 定时器（daemon=True，即使不取消
        #    也不会阻止进程退出，但显式取消更干净，避免它在解释器关闭过程中
        #    触发回调访问已经在被销毁的对象）。
        with self._sigwinch_debounce_lock:
            if self._sigwinch_debounce_timer is not None:
                self._sigwinch_debounce_timer.cancel()
                self._sigwinch_debounce_timer = None
        # 0.5 停"定时补打印"线程
        self._ptk_flush_stop.set()
        self._ptk_flush_thread.join(timeout=1.0)
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
# 子命令列表为空表示叶子命令。子命令列表里的每一项可以是：
#   "sub"                  → 叶子子命令，不再往下补全
#   ("sub", [...])         → 该子命令自己还有下一级子命令，可以任意深度嵌套
#                            （比如 /behavior browser stop --kill 这种三级）
# SubEntry = str | tuple[str, list[SubEntry]]

_COMMANDS: list[tuple[str, str, list]] = [
    ("/help",        "Show help",                                    []),
    ("/clear",       "Clear conversation history",                   []),
    ("/compact",     "Compress history into a summary",              []),
    ("/goal",        "Set a goal; agent auto-retries until done",    ["resume", "status", "list", "cancel"]),
    ("/memory",      "Generate/refresh session memory now",          []),
    ("/profile",     "Refresh user profile now",                     []),
    ("/stats",       "Show session statistics",                      []),
    ("/verbose",     "Toggle verbose tool output",                   []),
    ("/raw-output",  "Toggle raw model output (incl. <tool_use>)",   []),
    ("/reasoning",   "Toggle showing the model's reasoning/thinking process (default: on)", []),
    ("/turnjudge",   "Toggle TurnJudge (auto-detect real end-of-turn vs stall)", ["on", "off", "status"]),
    ("/prompts",     "List all managed prompt files",                []),
    ("/retry",       "Discard last response, regenerate with same input", []),
    ("/rollback",    "Undo entire last turn (input + response)",     []),
    ("/reload",      "Force hot-reload of skills and agent profiles", []),
    ("/skills",      "List all skills with status and token cost",   []),
    ("/skill",       "Manage skills",                                ["on", "off", "info", "stats", "reset"]),
    ("/model",       "Switch LLM model mid-session",                 []),   # 子命令在启动时动态注入
    ("/session",     "Session management",                           ["list", "new", "save", "resume", "delete", "dir", "search"]),
    ("/tasks",       "Task management",                              ["focus", "unfocus", "dashboard", "log", "cancel", "cancel-all", "workers"]),
    ("/plan",        "Plan management",                              ["show", "clear", "summary"]),
    ("/concurrency", "Concurrency settings",                         ["tasks", "llm"]),
    ("/cc",          "Concurrency alias (same as /concurrency)",     ["tasks", "llm"]),
    ("/ensemble",    "Best-of-N ensemble settings",                  ["status", "on", "off", "mode", "granularity", "n", "execution", "strategy"]),
    ("/provider",    "LLM provider settings",                        ["list", "models", "switch"]),
    ("/agents",      "Agent profile management",                     ["list", "show", "reload"]),
    ("/role",        "Roleplay persona: switch/exit agent's persona", ["list", "use", "show", "exit", "status", "stats", "reload"]),
    ("/hooks",       "Hook management",                              ["list", "reload"]),
    ("/platform",    "Platform/tag load policy for skill/agent/hook/tool", ["status", "filtered", "reload"]),
    ("/evolution",   "Self-evolution history",                       ["log", "show", "diff", "revert"]),
    ("/evolve",      "Spawn evolution-agent on qualifying lessons",  ["review", "list"]),
    ("/agent",       "Goal backlog & daemon management",             ["goals", "digest", "daemon"]),
    ("/goals",       "Shortcut for /agent goals",                    ["list", "add", "obj", "done", "abandon", "pause", "progress", "status"]),
    ("/digest",      "Show autonomous activity summary (last 24h)",  []),
    ("/debug",       "Print/export system prompt & history for debugging", ["system", "history", "all", "save"]),
    ("/cron",        "Manage periodic daemon tasks",                 ["list", "status", "enable", "disable", "run", "add", "remove", "set-schedule"]),
    ("/proxy",       "Proxy pool: subscriptions/validation/integration switches", ["status", "refresh", "sources", "integration"]),
    (
        "/behavior", "Behavior perception: desktop/browser/mobile activity, work & life daily report",
        [
            "status", "on", "off",
            ("enable", [
                "active_window", "idle", "browser_report", "mobile_report", "clipboard_meta",
                "cdp_browser", "git_activity", "terminal_command", "now_playing",
                "app_lifecycle", "daily_analysis",
            ]),
            ("disable", [
                "active_window", "idle", "browser_report", "mobile_report", "clipboard_meta",
                "cdp_browser", "git_activity", "terminal_command", "now_playing",
                "app_lifecycle", "daily_analysis",
            ]),
            "token", "recent", "clear",
            ("browser", ["start", ("stop", ["--kill"]), "status"]),
            ("git", ["install"]),
            ("terminal", ["show", "install"]),
            ("mobile", ["android", "ios"]),
            ("report", ["today"]),
        ],
    ),
    ("/exit",        "Exit mini-agent",                              []),
    ("/quit",        "Exit mini-agent",                              []),
]

# ── /model 子命令动态注入 ──────────────────────────────────────────────────────
# 在 REPL 启动时调用 prime_model_completions(pool)，将 fallback chain 中所有模型名
# 注入为 /model 的子命令候选，这样输入 "/model " 后 Tab 就能列出可用模型。

def prime_model_completions(pool: "LLMClientPool | None") -> None:
    """
    从 LLMClientPool 读取所有已配置模型，注入到 _COMMANDS 中 /model 条目的子命令列表。

    必须在 _ptk_session 首次创建之前调用（即在 REPL 主循环第一次 _read_line 之前）。
    若 pool 为 None 或读取失败，静默跳过，不影响正常启动。

    Args:
        pool: Agent 当前使用的 LLMClientPool 实例。
    """
    if pool is None:
        return
    try:
        snap = pool.snapshot()
        models: list[str] = []
        for entry in snap["entries"]:
            # label 格式为 "provider/model"，取 "/" 后半部分
            _, _, model = entry["label"].partition("/")
            if model and model not in models:
                models.append(model)
        if not models:
            return
        # 找到 _COMMANDS 中的 /model 条目并原地替换子命令列表
        for i, (name, desc, _subs) in enumerate(_COMMANDS):
            if name == "/model":
                _COMMANDS[i] = (name, desc, models)
                break
    except Exception:
        pass  # 静默降级，补全缺失不应影响主功能


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

    def _sub_name(entry):
        """子命令条目可能是 'sub' 或 ('sub', [children])，统一取出名字部分。"""
        return entry if isinstance(entry, str) else entry[0]

    def _sub_children(entry):
        """如果这个子命令条目还有下一级子命令，返回子列表；否则返回空列表（叶子）。"""
        return entry[1] if isinstance(entry, tuple) else []

    def _descend(tokens, level):
        """从某一级子命令列表出发，按已经完整输入的 tokens 逐级往下走。

        走到未知 token 时返回 None（说明输入的这一段跟任何已知子命令都不匹配，
        没有可补全的候选，直接不弹提示，而不是退回到上一级瞎补全）。
        """
        for tok in tokens:
            found = None
            for entry in level:
                if _sub_name(entry) == tok:
                    found = entry
                    break
            if found is None:
                return None
            level = _sub_children(found)
        return level

    class _SlashCompleter(Completer):
        """
        分层前缀补全，支持任意深度（不只是一二级）：
        1. 光标前的最后一个 token 以 "/" 开头 → 顶层命令前缀匹配
        2. 光标前已有完整命令 → 按已输入的每一级子命令逐级下钻，
           对当前正在输入的这一段做前缀匹配
           例如 "/behavior browser st" 会先下钻到 /behavior → browser 这一级，
           再对 "st" 做前缀匹配，弹出 "start"/"stop"
        """
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor

            # ── 阶段 2：子命令补全（支持任意深度）──────────────────────
            if " " in text:
                ends_with_space = text.endswith(" ")
                raw_parts = text.split()
                if not raw_parts:
                    return
                cmd = raw_parts[0].lower()

                if ends_with_space:
                    typed_tokens = [p.lower() for p in raw_parts[1:]]
                    prefix = ""
                else:
                    typed_tokens = [p.lower() for p in raw_parts[1:-1]]
                    prefix = raw_parts[-1].lower() if len(raw_parts) > 1 else ""

                for name, _desc, subs in _COMMANDS:
                    if name != cmd:
                        continue
                    if not subs:
                        return
                    level = _descend(typed_tokens, subs)
                    if level is None:
                        return  # 已输入的某一段子命令未知，没有可补全的候选
                    path_prefix = " ".join([name] + typed_tokens)
                    for entry in level:
                        sub_name = _sub_name(entry)
                        if not sub_name.startswith(prefix):
                            continue
                        children = _sub_children(entry)
                        hint = f"{path_prefix} {sub_name}"
                        if children:
                            hint += f"  [{' | '.join(_sub_name(c) for c in children)}]"
                        yield Completion(
                            sub_name,
                            start_position=-len(prefix),
                            display_meta=hint,
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
                        hint += f"  [{' | '.join(_sub_name(s) for s in subs)}]"
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
        # ★ 提交后由 ptk 自己擦除输入行（不再残留在屏幕上），改由调用方
        #   （daemon.py connected REPL）通过 term.print() 显式补打印一次
        #   "You ❯ <输入>"。原因：ptk 自己画的这行完全不受 Terminal 的
        #   _bar_drawn 记账管理，是唯一游离在统一渲染队列之外的屏幕内容——
        #   一旦任何相对擦除（状态栏 redraw / resize 结算等）算错了行数，
        #   没有任何账本记录它、也没有任何补偿机制，只会被无声吃掉。让
        #   ptk 自己先清掉，再由 Terminal 统一补打印一次，这行内容就和
        #   其它所有内容一样被正确记账，不会再被误伤（多终端 daemon
        #   connected 模式下曾复现过"发送方自己的 You ❯ 输入回显消失"）。
        erase_when_done=True,
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