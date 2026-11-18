"""entrypoints/_common.py — 入口脚本共用引导：把项目根加入 sys.path，统一 UTF-8 输出。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def force_utf8_stdio() -> None:
    """Windows 控制台默认代码页可能无法输出中文/emoji，统一切到 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
