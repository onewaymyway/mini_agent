"""
ui/raw_key_listener.py — agent 运行期间的 raw stdin 方向键监听器

设计背景
--------
prompt_toolkit 的 KeyBindings 只在 .prompt() 阻塞期间活跃（ptk 拥有终端）。
而用户需要在 agent.run_turn() 期间（task 并发执行时）按方向键切换 task 焦点。
此时 ptk 已退出，终端在 cooked 模式，ptk keybindings 完全睡着，任何绑在 ptk
上的快捷键都不会触发。

解决方案
--------
在 run_turn() 期间，用独立线程持有终端的 raw 模式，通过 select() + read(4)
解析 ANSI 方向键序列，直接调用 Terminal 的焦点 API。

生命周期
--------
- start()：保存 termios 状态、设置 raw 模式、启动监听线程
- stop()：设置停止标志、恢复 termios、join 线程
- 由 repl.py 在 agent.run_turn() 前后调用

键位映射
--------
  →  (ESC [ C)   focus_next_task
  ←  (ESC [ D)   focus_prev_task
  ↑  (ESC [ A)   focus_prev_task（备选）
  ↓  (ESC [ B)   focus_next_task（备选）
  ESC             set_task_focus(None) 退出焦点

注意事项
--------
1. 仅在 sys.stdin.isatty() 为 True 时启用，重定向/管道场景自动跳过
2. read() 超时用 select(0.1s)，保证 stop() 能在 <200ms 内响应
3. tty.setraw 只在这个线程里操作，ptk 和 readline 各自独立管理 termios，
   不会互相干扰——我们在 start 时保存快照，stop 时精确恢复
4. 日志写到 /tmp/mini_agent_keys.log，便于调试（生产时可关掉 DEBUG_LOG）
"""

from __future__ import annotations

import os
import sys
import select
import threading
import logging

logger = logging.getLogger(__name__)

# 开启后把每一个收到的字节序列写到日志，方便排查终端兼容性问题
DEBUG_LOG = os.environ.get("MINI_AGENT_KEY_DEBUG", "0") == "1"
_LOG_FILE = "./mini_agent_keys.log"

# ANSI 方向键序列
_SEQ_UP    = b"\x1b[A"
_SEQ_DOWN  = b"\x1b[B"
_SEQ_RIGHT = b"\x1b[C"
_SEQ_LEFT  = b"\x1b[D"
_SEQ_ESC   = b"\x1b"

# 部分终端会发送 SS3 序列（例如 xterm 的 application cursor keys 模式）
_SEQ_UP_SS3    = b"\x1bOA"
_SEQ_DOWN_SS3  = b"\x1bOB"
_SEQ_RIGHT_SS3 = b"\x1bOC"
_SEQ_LEFT_SS3  = b"\x1bOD"


def _log_key(seq: bytes) -> None:
    if not DEBUG_LOG:
        return
    try:
        with open(_LOG_FILE, "a") as f:
            f.write(f"key: {seq!r}\n")
    except Exception:
        pass


class RawKeyListener:
    """
    在 agent.run_turn() 期间持有 raw 终端，监听方向键。

    用法::

        listener = RawKeyListener()
        listener.start()
        try:
            agent.run_turn(user_input)
        finally:
            listener.stop()
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._old_termios: "list | None" = None
        self._active = False

    def start(self) -> None:
        """保存 termios、设 raw 模式、启动监听线程。"""
        if not sys.stdin.isatty():
            logger.debug("RawKeyListener: stdin is not a tty, skipping")
            _log_key(b"[SKIP: not a tty]")
            return

        try:
            import tty, termios
            fd = sys.stdin.fileno()
            self._old_termios = termios.tcgetattr(fd)
            tty.setraw(fd)
            _log_key(b"[RAW MODE ON]")
        except Exception as e:
            logger.debug("RawKeyListener: tty.setraw failed: %s", e)
            self._old_termios = None
            return

        self._stop_event.clear()
        self._active = True
        self._thread = threading.Thread(
            target=self._listen_loop,
            name="mini-agent-keylistener",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """停止监听线程、恢复 termios。"""
        self._stop_event.set()
        self._active = False

        # 恢复终端状态（必须在 join 之前，否则 read() 可能卡住）
        if self._old_termios is not None:
            try:
                import termios
                fd = sys.stdin.fileno()
                termios.tcsetattr(fd, termios.TCSADRAIN, self._old_termios)
                _log_key(b"[TERMIOS RESTORED]")
            except Exception as e:
                logger.debug("RawKeyListener: termios restore failed: %s", e)
            self._old_termios = None

        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

    def _listen_loop(self) -> None:
        """监听线程主体：select + read + 序列解析。"""
        fd = sys.stdin.fileno()

        while not self._stop_event.is_set():
            try:
                r, _, _ = select.select([fd], [], [], 0.1)
            except Exception:
                break

            if not r:
                continue

            try:
                seq = os.read(fd, 1)
            except Exception:
                break

            if not seq:
                continue

            # ESC 开头：等最多 50ms 看后面是否有 [ + 字母（方向键序列）
            if seq == b"\x1b":
                try:
                    r2, _, _ = select.select([fd], [], [], 0.05)
                    if r2:
                        rest = os.read(fd, 7)
                        seq = seq + rest
                    # 若 50ms 内无后续字节 → 单独 ESC
                except Exception:
                    pass

            _log_key(seq)
            self._dispatch(seq)

    def _dispatch(self, seq: bytes) -> None:
        """根据字节序列触发对应操作。"""

        # raw 模式下 Ctrl+C 不再自动产生 SIGINT，需要手动发送信号
        if seq == b"\x03":
            _log_key(b"[ACTION: SIGINT]")
            import os, signal
            os.kill(os.getpid(), signal.SIGINT)
            return

        try:
            from mini_agent.ui.terminal import get_terminal
            t = get_terminal()
        except Exception:
            return

        if seq in (_SEQ_RIGHT, _SEQ_DOWN, _SEQ_RIGHT_SS3, _SEQ_DOWN_SS3):
            _log_key(b"[ACTION: focus_next]")
            t.focus_next_task()

        elif seq in (_SEQ_LEFT, _SEQ_UP, _SEQ_LEFT_SS3, _SEQ_UP_SS3):
            _log_key(b"[ACTION: focus_prev]")
            if t.get_task_focus() is None:
                t.focus_next_task()
            else:
                t.focus_prev_task()

        elif seq == _SEQ_ESC:
            # 纯 ESC：退出焦点
            _log_key(b"[ACTION: focus_clear]")
            if t.get_task_focus() is not None:
                t.set_task_focus(None)

        # 其他按键（字母、数字等）在 raw 模式下被吞掉了——这是 raw 模式的代价。
        # 由于我们只在 agent.run_turn() 期间激活（不是在用户输入期间），
        # 用户不会输入文字时被 raw 模式影响。


# 模块级单例，由 repl.py 使用
_listener = RawKeyListener()


def get_listener() -> RawKeyListener:
    return _listener
