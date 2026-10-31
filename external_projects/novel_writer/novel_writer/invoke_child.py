"""novel_writer/invoke_child.py — 子进程里执行一次 skill 调用（供 FrameworkInvoker 使用）。

之所以放到独立进程：Python 线程无法被安全强杀，卡在一次很慢的 LLM/工具调用里的 agent
无法在进程内中止。放进子进程后，超时由父进程整树终止（procs.terminate_tree），真正做到「直接终止」。

用法（由 FrameworkInvoker 拉起，不是给人用的）：
    python -m novel_writer.invoke_child <payload.json> <result.json>
标准输出/错误经管道由父进程原样写入 run.log（见 outlog.py），所以运行中的中间输出照常可见。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

PARENT_POLL_SEC = 5.0


def _watch_parent(parent_pid: int) -> None:
    """父进程（worker）消失后自行退出，避免残留的孤儿子进程继续烧 token。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return
    while True:
        time.sleep(PARENT_POLL_SEC)
        if not psutil.pid_exists(parent_pid):
            os._exit(3)


def _install_full_result_hook() -> None:
    """让框架的工具返回内容完整打印：框架的 print_tool_result 默认 truncate=2000，这里强制不截断。

    其他输出（工具标题、入参 JSON、助手正文）保持框架原样，由父进程原样写入 run.log。
    """
    try:
        from mini_agent.ui import renderer as R
    except Exception:  # noqa: BLE001 - 框架不可用时由调用方报告
        return
    orig_result = R.print_tool_result

    def print_tool_result(tool_name, result, truncate=2000):
        return orig_result(tool_name, result, truncate=None)

    R.print_tool_result = print_tool_result
    try:
        R.R.print_tool_result = print_tool_result
    except AttributeError:
        pass


def main(argv: list) -> int:
    for stream in (sys.stdout, sys.stderr):   # 与 entrypoints/_common 一致：UTF-8、不因编码失败
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    payload_file, result_file = Path(argv[1]), Path(argv[2])
    payload = json.loads(payload_file.read_text(encoding="utf-8"))
    parent_pid = int(payload.get("parent_pid") or 0)
    if parent_pid:
        threading.Thread(target=_watch_parent, args=(parent_pid,), daemon=True).start()
    from novel_writer.runner import FrameworkInvoker

    _install_full_result_hook()

    result = FrameworkInvoker()._invoke_inprocess(
        skill=payload["skill"], prompt=payload["prompt"], export_dir=Path(payload["export_dir"]),
        step_id=payload["step_id"], max_turns=payload.get("max_turns"), timeout=payload.get("timeout"),
    )
    result_file.write_text(json.dumps({"ok": result.ok, "error": result.error}, ensure_ascii=False), encoding="utf-8")
    _drain_terminal()
    return 0


def _drain_terminal() -> None:
    """框架的终端渲染在后台守护线程里按队列输出；进程一退出队列里还没写的内容就丢了（尤其是最后一段），
    所以退出前等队列写完。"""
    try:
        from mini_agent.ui.terminal import term

        term._q.join()
    except Exception:  # noqa: BLE001 - 只是尽力而为
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
