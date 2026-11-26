"""启动期的兜底：pythonw 没有控制台，任何致命错误都必须落到日志并弹窗，否则只会"没反应"。

- guarded_main()：先初始化文件日志（不依赖 Qt），再运行真正的 main；任何未捕获异常
  记录完整 traceback 到 desktop.log，并弹出原生消息框（Windows 用 user32.MessageBoxW）。
- native_error()：不依赖 PySide6 的错误弹窗；非 Windows 退回 stderr。
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Callable, Optional

from mini_agent_desktop import logsetup

MB_OK = 0x0
MB_ICONERROR = 0x10


def native_error(title: str, text: str) -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, MB_OK | MB_ICONERROR)
            return
        except Exception:  # noqa: BLE001
            pass
    print(f"{title}: {text}", file=sys.stderr)


def guarded_main(run: Callable[[], Optional[int]], *, log_path: Optional[Path] = None) -> int:
    path = log_path or logsetup.default_log_path()
    logsetup.setup_logging(path)
    log = logging.getLogger("mini_agent_desktop.bootstrap")
    try:
        code = run()
        return 0 if code is None else int(code)
    except SystemExit as exc:
        return int(exc.code or 0)
    except BaseException:  # noqa: BLE001 - 这里就是最后的兜底
        tb = traceback.format_exc()
        log.error("桌面端启动失败：\n%s", tb)
        native_error("mini_agent 桌面端启动失败",
                     f"{tb.strip().splitlines()[-1]}\n\n详细信息见日志：{path}")
        return 1
