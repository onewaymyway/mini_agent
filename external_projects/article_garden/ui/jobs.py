"""ui/jobs.py — 看板启动后台任务（子进程）的公共函数；任务进度与错误见「▶ 运行日志」。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def spawn(entry: str, *args: str) -> None:
    subprocess.Popen([sys.executable, str(PROJECT_DIR / "entrypoints" / entry), *args],
                     cwd=str(PROJECT_DIR), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
