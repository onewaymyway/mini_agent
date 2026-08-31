"""notification/hang_dump.py — daemon 卡死时的线程栈快照。

设计背景：daemon_supervisor.py 判定子进程卡死、准备 SIGKILL 强杀之前，
如果什么都不做就直接杀掉，事后完全不知道它卡在哪一行——
`daemon_crash_history.jsonl` 里 `last_exception`/`log_tail` 对"卡死"这种
场景天生就是空的（进程没死、没抛异常，见 daemon.py::record_daemon_crash
里 hang_reason 分支的注释）。

这里用标准库 `faulthandler.register()` 实现一个"信号触发的全线程栈
转储"：daemon 子进程启动时注册一个 SIGUSR1 处理器（正常处理请求时完全
不会被触发，零开销），supervisor 判定卡死后、SIGKILL 之前，先发一次
SIGUSR1 触发转储、等一小段时间、读回转储文件内容，再继续强杀流程。

`faulthandler` 转储所有线程的做法是直接从信号处理器里用 `os.write()`
往文件描述符写 C 级别的帧信息，不需要拿到 GIL、不需要目标线程主动让出
控制权——这正是"事件循环被别的线程/别的同步调用卡住"这种场景下仍然能
拿到东西的关键（见 next_doc 相关分析：GIL 被某个线程长期占用、或跨
线程死锁时，常规"等它自己把日志打出来"完全指望不上）。

已知限制：
- Windows 不支持 `faulthandler.register()` 的自定义信号回调（标准库
  文档明确写了 "Not available on Windows"），这个功能在 Windows 上
  直接跳过，`capture_hang_stack_dump()` 返回一段说明性文本而不是 None，
  避免调用方误以为是"没查到"而是"这个平台压根不支持"。
- 如果子进程因为某种原因没走到注册这行代码就已经卡死（比如卡在
  import 阶段），SIGUSR1 送过去后 Python 对未注册信号的默认处置是
  终止进程——效果上等价于提前触发了紧接着的 SIGKILL，不会有副作用
  （反正马上就要强杀），只是转储自然拿不到内容。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

# 保持对 faulthandler 打开的文件对象的引用，防止被 GC 关闭——
# faulthandler.register() 只是把 fd 记下来，不会自己持有 Python 层的
# file 对象引用；对象被回收关闭后，信号触发时的写入会失败。
_dump_file = None

# 单次转储读回上限，避免线程/协程数量异常多时把 crash history 文件
# 撑得过大（history 本身也有整体大小/条数的轮转限制，这里再单独兜底
# 一层，两者互不影响）。
_MAX_DUMP_CHARS = 40_000


def dump_file_path(project_root: Path) -> Path:
    return project_root / ".agent" / "daemon_hang_dump.txt"


def register_hang_dump_handler(project_root: Path) -> bool:
    """daemon 子进程启动时调用一次。返回是否成功注册；Windows / 任何
    异常都返回 False 并按"这个功能不可用"处理，不阻断正常启动——诊断
    能力的缺失不应该反过来影响 daemon 本身的可用性。"""
    global _dump_file
    if sys.platform == "win32":
        return False
    try:
        import faulthandler
        import signal

        path = dump_file_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        # "w" 截断：每次进程启动都是全新的一份，避免上一条生命周期的
        # 转储混进来误导下一次排查。
        _dump_file = open(path, "w", encoding="utf-8", errors="replace")
        faulthandler.register(
            signal.SIGUSR1, file=_dump_file, all_threads=True, chain=False
        )
        return True
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(
            exc,
            where="mini_agent.notification.hang_dump.register_hang_dump_handler",
        )
        return False


def capture_hang_stack_dump(
    pid: int, project_root: Path, wait_seconds: float = 3.0
) -> Optional[str]:
    """supervisor 侧调用：向 pid 发 SIGUSR1，等待并读回转储文件内容。

    调用方负责在这之后继续原有的强杀流程——这个函数只负责"尽力拿到
    诊断信息"，任何失败/超时都不应该阻断或明显延误强杀（`wait_seconds`
    应该保持较小，默认 3s）。返回值恒为字符串（成功拿到栈内容，或一段
    以 `[未获取到栈快照]` 开头的说明性文字），不返回 None，方便调用方
    统一处理、写进崩溃记录时也不需要额外判空分支。"""
    if sys.platform == "win32":
        return "[未获取到栈快照] Windows 平台不支持 faulthandler 的信号栈转储"

    path = dump_file_path(project_root)

    try:
        import os
        import signal
        os.kill(pid, signal.SIGUSR1)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        return f"[未获取到栈快照] 发送 SIGUSR1 失败：{exc}"

    deadline = time.time() + max(0.5, wait_seconds)
    content = ""
    while time.time() < deadline:
        try:
            if path.exists() and path.stat().st_size > 0:
                # 留一点余量，避免读到刚开始写的半截内容——faulthandler
                # 的 dump 本身是信号处理器里同步一次写完的，这里的停顿
                # 只是防御信号送达与文件系统可见性之间极短的时间差。
                time.sleep(0.15)
                content = path.read_text(encoding="utf-8", errors="replace")
                if content.strip():
                    break
        except OSError:
            pass
        time.sleep(0.1)

    if not content.strip():
        return (
            "[未获取到栈快照] 等待超时或转储文件为空"
            "（进程可能没有走到注册处理器那一行就已经卡死，"
            "或者在收到 SIGUSR1 之前就已经被系统终止）"
        )

    if len(content) > _MAX_DUMP_CHARS:
        content = content[:_MAX_DUMP_CHARS] + f"\n...[截断，完整内容见 {path}]"
    return content
