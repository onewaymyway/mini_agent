"""world_forge/paths.py — 项目路径与 mini_agent 根目录定位。

数据目录可用环境变量 WORLD_FORGE_DATA_DIR 覆盖（测试用临时目录）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_root() -> Path:
    env = os.environ.get("WORLD_FORGE_DATA_DIR", "").strip()
    return Path(env).expanduser() if env else PROJECT_ROOT / "data"


def mini_agent_root() -> Optional[Path]:
    """找到 mini_agent 仓库根（含 src/mini_agent）。找不到返回 None。"""
    env = os.environ.get("MINI_AGENT_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    for base in [PROJECT_ROOT, *PROJECT_ROOT.parents]:
        if (base / "src" / "mini_agent").is_dir():
            return base
    return None
