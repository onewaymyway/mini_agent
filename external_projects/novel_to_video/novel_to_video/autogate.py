"""autogate.py — 按项目挡位判断某个素材检查点是否自动通过（asset_text / asset_portrait 等）。

挡位策略见 gates.py；这里只负责读取项目当前挡位，供各步骤判断。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import gates


def project_gear(project_dir: Path) -> str:
    try:
        cfg = json.loads((Path(project_dir) / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return gates.normalize_gear(cfg.get("gear") or "G0")


def is_auto(project_dir: Path, checkpoint: str) -> bool:
    return gates.is_auto(project_gear(project_dir), checkpoint)
