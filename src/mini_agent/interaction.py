"""
interaction.py — 通用交互式提问的双路适配层。

背景（排查记录）：
  daemon 模式下，之前只有"工具调用权限审批"（permissions.py 的
  _prompt_with_http）正确适配了 CLI + HTTP 双路交互；而
    1. ask_user / ask_user_confirm / ask_user_choice 三个工具
    2. /goal 目标协商子对话（cli/commands/goal_mode_cmd.py）
    3. 任意 slash 命令内部残留的 term.prompt_user()/term.confirm() 调用
  都是直接读本地 stdin/终端，daemon 进程本身通常没有真正连着的
  终端（后台运行），导致这些交互在 daemon 模式下要么永久阻塞、要么
  立刻 EOF 拿到空答案——remote 客户端完全看不到问题、也回答不了。

本模块提供一个统一的 `ask()` 函数，行为与 permissions.py 的双路审批完全
对称：
  - 如果 HTTP bridge 已启动（daemon / 内嵌 http 服务），把问题广播成
    INTERACTION_REQ 事件，daemon connected 的远程客户端可以看到并回答；
  - 如果当前进程还连着本地终端（前台运行、或非 daemon 模式），本地终端
    也可以直接回答；
  - 两边谁先给出答案就用谁的，另一边随之失效。
  - 都没有时（纯 HTTP-only、无本地终端，例如真正 daemonize 的后台进程），
    退化为"只等 HTTP"。
"""

from __future__ import annotations

import sys
import threading
import uuid
from typing import Any, Callable, Optional


# ── HTTP bridge 懒加载辅助（避免 interaction <-> api 循环依赖）─────────────────

def _get_http_gate():
    """懒加载 HttpInteractionGate 单例。只有 HTTP 服务真正启动后才返回非 None。"""
    try:
        from mini_agent.api.bridge import get_bridge
        bridge = get_bridge()
        if bridge.broadcaster._loop is not None:
            return bridge
    except Exception:
        return None
    return None


def _get_current_turn_id() -> str:
    try:
        from mini_agent.api.bridge import get_bridge
        bridge = get_bridge()
        if bridge.agent:
            return getattr(bridge.agent, "_http_turn_id", "")
    except Exception:
        pass
    return ""


def _has_local_terminal() -> bool:
    """判断当前进程是否有一个"值得去读"的本地终端。

    daemon 以后台方式启动时 stdin 通常被重定向到 /dev/null 或直接关闭，
    这时候盲目去 sys.stdin.readline() 只会立刻拿到 "" 或永久阻塞在一个
    没有任何人会去写的管道上——这两种情况都应该直接跳过"本地"这一路，
    只走 HTTP，而不是去抢一个不存在的输入。
    """
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def interruptible_readline(interrupt_event: threading.Event, timeout: Optional[float] = None) -> Optional[str]:
    """
    可被 interrupt_event 中断的 sys.stdin.readline()。

    用独立线程读 stdin，主线程等"读到一行"或"interrupt_event 被 set()"
    两者之一——与 ui/terminal.py::Terminal.confirm() 里的可中断读取实现
    同一个模式，这里抽出来给 ask_user/ask_user_choice 复用（它们不是
    y/n 确认，不能直接用 term.confirm()）。

    返回读到的字符串（已 strip 尾部换行），或者 None（被中断 / EOF）。
    """
    result_holder: list = []
    stdin_done = threading.Event()

    def _read_stdin():
        try:
            line = sys.stdin.readline()
            result_holder.append(line)
        except Exception:
            result_holder.append("")
        finally:
            stdin_done.set()

    reader = threading.Thread(target=_read_stdin, daemon=True)
    reader.start()

    waited = 0.0
    poll = 0.2
    while True:
        if stdin_done.wait(timeout=poll):
            break
        if interrupt_event.is_set():
            return None
        waited += poll
        if timeout is not None and waited >= timeout:
            return None

    if not result_holder:
        return None
    line = result_holder[0]
    if not line:
        return None  # EOF
    return line.rstrip("\n")


def ask(
    kind: str,
    display_data: dict,
    local_read: Callable[[threading.Event], Optional[dict]],
    *,
    turn_id: Optional[str] = None,
) -> Optional[dict]:
    """
    发起一次通用交互式提问，双路（本地终端 + HTTP 远程客户端）等待回答。

    kind:          "ask_user" | "ask_user_confirm" | "ask_user_choice" |
                   "goal_negotiation" | "repl_prompt"
    display_data:  展示给用户的内容（question/hint/options/prompt_text 等），
                   会原样放进 INTERACTION_REQ 事件的 data 里给远程客户端渲染。
    local_read:    本地读取函数，接收一个 threading.Event（HTTP 端先回答时
                   会被 set()，用于提前中断本地阻塞读取），返回一个 answer
                   dict，或者在被中断 / 无本地终端时返回 None。
    返回：answer dict（字段含义随 kind 而定，见 bridge.py 里 _PendingInteraction
          的注释），或者 None（双路都没有给出答案，例如超时）。
    """
    bridge = _get_http_gate()
    tid = turn_id if turn_id is not None else _get_current_turn_id()

    if bridge is None:
        # 没有 HTTP 服务在跑（纯本地 REPL，非 daemon/非内嵌 http 模式）：
        # 直接走本地读取，行为和改造前完全一致。
        return local_read(threading.Event())

    gate = bridge.interaction_gate
    req_id = str(uuid.uuid4())
    pending = gate.register_pending(req_id, kind, display_data, tid)
    decided_event = pending.event

    try:
        bridge.set_state("waiting_permission")  # 复用现成的"等待用户"状态展示
    except Exception:
        pass

    result_holder: dict = {"answer": None, "source": ""}
    result_lock = threading.Lock()

    def _http_watcher() -> None:
        responded = decided_event.wait(timeout=gate._timeout)
        with result_lock:
            if result_holder["source"]:
                return
            if responded:
                result_holder["answer"] = pending.answer
                result_holder["source"] = "http"
            else:
                result_holder["source"] = "timeout"
        decided_event.set()

    http_thread = threading.Thread(target=_http_watcher, daemon=True)
    http_thread.start()

    if _has_local_terminal():
        # local_read 自己负责阻塞等待本地输入，并且要在收到 decided_event
        # （代表 HTTP 端已经先回答）时尽快中断返回 None——这是它的职责，
        # 与 permissions.py::confirm(interrupt_event=...) 的约定完全一致。
        try:
            local_answer = local_read(decided_event)
        except Exception:
            local_answer = None

        if local_answer is not None and not decided_event.is_set():
            with result_lock:
                if not result_holder["source"]:
                    result_holder["answer"] = local_answer
                    result_holder["source"] = "cli"
            decided_event.set()
    else:
        # 没有本地终端可读（真正 daemonize 的后台进程）：只能等 HTTP 那一路，
        # 不能替它做决定，否则永远拿到空答案。
        decided_event.wait(timeout=gate._timeout)

    # 唤醒 _http_watcher（如果还没被 respond() 或上面的分支唤醒），
    # 并 join() 确保读取 result_holder 时它一定已经写完——避免
    # "decided_event 刚被 set()，watcher 还没来得及写 result_holder"
    # 这种竞态。
    decided_event.set()
    http_thread.join(timeout=5.0)

    with gate._lock:
        gate._pending.pop(req_id, None)

    with result_lock:
        source = result_holder["source"] or "timeout"
        answer = result_holder["answer"]

    gate.broadcast_done(req_id, answer or {}, source, tid)
    try:
        if bridge._state == "waiting_permission":
            bridge.set_state("running")
    except Exception:
        pass

    return answer
