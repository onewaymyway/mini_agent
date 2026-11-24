"""entrypoints/_common.py — 入口共用引导：项目根与 mini_agent 的 src 加入 sys.path，统一 UTF-8 输出。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MINI_AGENT_SRC = None
for _base in [PROJECT_ROOT, *PROJECT_ROOT.parents]:
    if (_base / "src" / "mini_agent").is_dir():
        MINI_AGENT_SRC = _base / "src"
        break
for _p in (PROJECT_ROOT, MINI_AGENT_SRC):
    if _p is not None and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
