"""
ui/raw_key_listener.py — 跨平台方向键监听器

背景
----
prompt_toolkit 的 KeyBindings 只在 .prompt() 阻塞时活跃（ptk 持有终端）。
agent.run_turn() 期间 ptk 已退出，任何绑在 ptk 上的快捷键均不生效。
本模块在 run_turn() 期间独立监听键盘，实现 task 焦点切换。

平台实现
--------
Unix (Linux / macOS)  →  _UnixKeyReader
  - 优先打开 /dev/tty（控制终端设备，不受 stdin/stdout 重定向影响）
  - Fallback：找第一个 os.isatty(fd) 为 True 的标准 fd (0/1/2)
  - tty.setraw() + select() + os.read() 解析 ANSI ESC 序列
  - stop() 时精确 termios.tcsetattr 还原，不影响 prompt_toolkit

Windows  →  _WindowsKeyReader
  - msvcrt.kbhit() 轮询 + msvcrt.getwch() 读字符（标准库，无需额外依赖）
  - 方向键协议：0xe0/0x00 前缀 + 方向码 (H/P/M/K)
  - Ctrl+C (0x03) 手动发 SIGINT（msvcrt 默认不产生 SIGINT）
  - 轮询间隔 50ms，CPU 占用可忽略

键位映射（两平台统一）
---------------------
  →  或  ↓   focus_next_task()
  ←  或  ↑   focus_prev_task()（无焦点时进入第一个）
  ESC         set_task_focus(None) 退出焦点

调试
----
  MINI_AGENT_KEY_DEBUG=1 mini-agent
  tail -f /tmp/mini_agent_keys.log   # Unix
  type \\tmp\\mini_agent_keys.log    # Windows (PowerShell: Get-Content -Wait)
"""

from __future__ import annotations

import os
import sys
import platform
import signal
import threading
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

DEBUG_LOG: bool = os.environ.get("MINI_AGENT_KEY_DEBUG", "0") == "1"
# DEBUG_LOG=True
_LOG_FILE: str = ".agent/mini_agent_keys.log"

def _log(msg: "bytes | str") -> None:
    if not DEBUG_LOG:
        return
    try:
        text = msg if isinstance(msg, str) else repr(msg)
        with open(_LOG_FILE, "a") as f:
            f.write(text + "\n")
    except Exception:
        print("log fail:")
        import traceback
        traceback.print_exc()
        pass


# ── 抽象基类 ─────────────────────────────────────────────────────────────────

class _BaseKeyReader(ABC):
    """平台无关的按键读取器接口。"""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: "threading.Thread | None" = None

    def start(self) -> None:
        if not self._setup():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="mini-agent-keylistener",
            daemon=True,
        )
        self._thread.start()
        _log(f"[{self.__class__.__name__} started]")

    def stop(self) -> None:
        _log(f"[{self.__class__.__name__} stop()]")
        self._stop_event.set()
        self._teardown()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

    @abstractmethod
    def _setup(self) -> bool:
        """初始化终端，返回是否成功。"""

    @abstractmethod
    def _teardown(self) -> None:
        """还原终端状态。"""

    @abstractmethod
    def _loop(self) -> None:
        """监听线程主体。"""

    @staticmethod
    def _dispatch(action: str) -> None:
        """把解析出的动作派发到 Terminal。"""
        try:
            from mini_agent.ui.terminal import get_terminal
            t = get_terminal()
        except Exception as e:
            _log(f"[get_terminal error: {e}]")
            return

        _log(f"[ACTION: {action}]")
        if action == "next":
            t.focus_next_task()
        elif action == "prev":
            if t.get_task_focus() is None:
                t.focus_next_task()
            else:
                t.focus_prev_task()
        elif action == "clear":
            if t.get_task_focus() is not None:
                t.set_task_focus(None)
        elif action == "sigint":
            os.kill(os.getpid(), signal.SIGINT)


# ── Unix 实现 ─────────────────────────────────────────────────────────────────

class _UnixKeyReader(_BaseKeyReader):
    """
    Unix (Linux / macOS) 实现。

    fd 获取策略（按优先级）：
      1. /dev/tty  — 控制终端，不受 stdin/stdout 重定向影响，最可靠
      2. stderr fd — 通常不被重定向
      3. stdout fd — 次之
      4. stdin fd  — 最后选择

    tty.setraw() 让内核不缓冲、不回显，
    select() 非阻塞轮询保证 stop() 能在 <200ms 内响应。
    """

    # ANSI CSI 方向键
    _CSI_UP    = b"\x1b[A"
    _CSI_DOWN  = b"\x1b[B"
    _CSI_RIGHT = b"\x1b[C"
    _CSI_LEFT  = b"\x1b[D"
    # SS3 变体（xterm application cursor keys 模式）
    _SS3_UP    = b"\x1bOA"
    _SS3_DOWN  = b"\x1bOB"
    _SS3_RIGHT = b"\x1bOC"
    _SS3_LEFT  = b"\x1bOD"

    def __init__(self) -> None:
        super().__init__()
        self._fd: "int | None" = None
        self._old_attrs: "list | None" = None
        self._fd_owned: bool = False   # 是否是我们自己 open 的（需要 close）

    def _setup(self) -> bool:
        import termios
        fd = self._find_tty_fd()
        if fd is None:
            _log("[Unix] no usable tty fd found, skipping")
            return False
        try:
            self._old_attrs = termios.tcgetattr(fd)

            # ── 为什么不用 tty.setraw(fd) ───────────────────────────────
            # tty.setraw() 会把 IFLAG/OFLAG/CFLAG/LFLAG 全部清成"裸"模式，
            # 其中 OFLAG 里的 OPOST 被清掉意味着内核不再把输出的 "\n"
            # 自动转换成 "\r\n"。
            #
            # 致命的是：termios 设置是**终端设备级别**的，不是 fd 级别的。
            # 这里打开的 fd 通常是 /dev/tty（控制终端），它和 sys.stdout
            # 指向的是**同一个底层设备**——哪怕是两个不同的文件描述符。
            # 一旦在这个 fd 上 setraw()，OPOST 在整个设备上都被关掉，
            # 后续任何线程往 sys.stdout 写的 "\n" 都不会再自动回到列首，
            # 造成 terminal.py 渲染线程输出的每一行依次比上一行更靠右、
            # 呈阶梯状错位——这正是用户反馈"simple-mode 不对"时观察到的
            # 现象，且与是否开启 simple-mode 无关（普通模式同样受影响，
            # 只是被状态栏的擦除/重绘操作部分掩盖了）。
            #
            # 这个监听器实际只需要"输入"侧的三个特性：
            #   - 不回显按键（ECHO off）
            #   - 不等行缓冲，按字节立即可读（ICANON off，VMIN=1/VTIME=0）
            #   - 不让内核对 Ctrl+C 自动发信号（ISIG off——因为下面
            #     _handle() 收到 b"\x03" 时会手动 os.kill(...SIGINT)，
            #     必须由我们自己识别这个字节，不能让内核抢先处理掉）
            # 这三点都只涉及 LFLAG，完全不需要触碰 OFLAG/IFLAG/CFLAG，
            # 因此手工只清 LFLAG 里这三个 bit，其余（包括 OPOST）原样保留。
            new_attrs = termios.tcgetattr(fd)
            new_attrs[3] = new_attrs[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)  # lflag
            new_attrs[6][termios.VMIN] = 1   # cc[VMIN]：至少读到 1 字节就返回
            new_attrs[6][termios.VTIME] = 0  # cc[VTIME]：不等待超时
            termios.tcsetattr(fd, termios.TCSANOW, new_attrs)

            self._fd = fd
            _log(f"[Unix] tty fd={fd}, cbreak mode on (OPOST preserved)")
            return True
        except Exception as e:
            _log(f"[Unix] termios setup failed on fd={fd}: {e}")
            if self._fd_owned and fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            return False

    def _find_tty_fd(self) -> "int | None":
        # 策略1：/dev/tty（最可靠，不受重定向影响）
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
            self._fd_owned = True
            _log(f"[Unix] opened /dev/tty as fd={fd}")
            return fd
        except Exception as e:
            _log(f"[Unix] /dev/tty failed: {e}")

        # 策略2：找第一个 isatty 为 True 的标准 fd
        for fd, name in [(2, "stderr"), (1, "stdout"), (0, "stdin")]:
            if os.isatty(fd):
                self._fd_owned = False
                _log(f"[Unix] using {name} fd={fd} as tty")
                return fd

        return None

    def _teardown(self) -> None:
        if self._fd is None:
            return
        import termios
        try:
            if self._old_attrs is not None:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)
                _log(f"[Unix] termios restored on fd={self._fd}")
        except Exception as e:
            _log(f"[Unix] termios restore error: {e}")
        if self._fd_owned:
            try:
                os.close(self._fd)
            except Exception:
                pass
        self._fd = None
        self._old_attrs = None

    def _loop(self) -> None:
        import select as _select
        fd = self._fd
        _log("[Unix] listen loop started")

        while not self._stop_event.is_set() and self._fd is not None:
            try:
                r, _, _ = _select.select([fd], [], [], 0.1)
            except Exception as e:
                _log(f"[Unix] select error: {e}")
                break

            if not r:
                continue

            try:
                first = os.read(fd, 1)
            except Exception as e:
                _log(f"[Unix] read error: {e}")
                break

            if not first:
                continue

            seq = first

            # ESC 开头：等 50ms 判断是单独 ESC 还是方向键序列
            if first == b"\x1b":
                try:
                    r2, _, _ = _select.select([fd], [], [], 0.05)
                    if r2:
                        rest = os.read(fd, 7)
                        seq = first + rest
                except Exception:
                    pass   # 50ms 超时 → 单独 ESC

            _log(seq)
            self._handle(seq)

        _log("[Unix] listen loop exited")

    def _handle(self, seq: bytes) -> None:
        if seq == b"\x03":
            self._dispatch("sigint")
        elif seq in (self._CSI_RIGHT, self._CSI_DOWN,
                     self._SS3_RIGHT, self._SS3_DOWN):
            self._dispatch("next")
        elif seq in (self._CSI_LEFT, self._CSI_UP,
                     self._SS3_LEFT, self._SS3_UP):
            self._dispatch("prev")
        elif seq == b"\x1b":
            self._dispatch("clear")
        else:
            _log(f"[Unix] ignored: {seq!r}")


# ── Windows 实现 ──────────────────────────────────────────────────────────────

class _WindowsKeyReader(_BaseKeyReader):
    """
    Windows 实现，使用标准库 msvcrt。

    方向键协议：
      第1字节  0xe0 (扩展键) 或 0x00 (功能键)
      第2字节  H=上  P=下  M=右  K=左

    msvcrt.getwch() 不回显、不缓冲，无需修改终端模式，
    所以 setup/teardown 极简，也不会与 prompt_toolkit 冲突。
    """

    def _setup(self) -> bool:
        try:
            import msvcrt  # noqa: F401
            _log("[Windows] msvcrt available")
            return True
        except ImportError:
            _log("[Windows] msvcrt not available (not Windows?)")
            return False

    def _teardown(self) -> None:
        pass   # msvcrt 不修改终端状态，无需还原

    def _loop(self) -> None:
        import msvcrt, time
        _log("[Windows] listen loop started")

        while not self._stop_event.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue

            ch = msvcrt.getwch()
            _log(f"[Windows] ch={repr(ch)}")

            if ch in ("\xe0", "\x00"):
                # 扩展键：读第二字节获得方向
                if not msvcrt.kbhit():
                    time.sleep(0.02)
                ch2 = msvcrt.getwch()
                _log(f"[Windows] ch2={repr(ch2)}")
                if ch2 in ("M", "P"):    # 右、下
                    self._dispatch("next")
                elif ch2 in ("K", "H"):  # 左、上
                    self._dispatch("prev")
                else:
                    _log(f"[Windows] ignored extended: {repr(ch2)}")
            elif ch == "\x1b":
                self._dispatch("clear")
            elif ch == "\x03":
                self._dispatch("sigint")
            else:
                _log(f"[Windows] ignored: {repr(ch)}")

        _log("[Windows] listen loop exited")


# ── 工厂 & 模块单例 ───────────────────────────────────────────────────────────

def _make_reader() -> _BaseKeyReader:
    if platform.system() == "Windows":
        _log("[factory] Windows -> _WindowsKeyReader")
        return _WindowsKeyReader()
    else:
        _log("[factory] Unix -> _UnixKeyReader")
        return _UnixKeyReader()


class RawKeyListener:
    """
    跨平台键盘监听器门面，由 repl.py 在 run_turn() 前后调用。

    用法::

        listener = get_listener()
        listener.start()
        try:
            agent.run_turn(user_input)
        finally:
            listener.stop()
    """

    def __init__(self) -> None:
        self._reader: "_BaseKeyReader | None" = None

    def start(self) -> None:
        _log("[RawKeyListener.start]")
        self._reader = _make_reader()
        self._reader.start()

    def stop(self) -> None:
        _log("[RawKeyListener.stop]")
        if self._reader is not None:
            self._reader.stop()
            self._reader = None

    @property
    def active(self) -> bool:
        return (self._reader is not None
                and self._reader._thread is not None
                and self._reader._thread.is_alive())


_listener = RawKeyListener()


def get_listener() -> RawKeyListener:
    return _listener