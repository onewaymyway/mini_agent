"""paths.py — 仓库内路径定位（外部项目根目录、mini_agent 根目录）。"""

from __future__ import annotations

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent  # external_projects/novel_to_video


def mini_agent_root() -> Path:
    """向上查找含 src/mini_agent 的目录（外部项目位于 <repo>/external_projects/<name>）。"""
    for parent in PROJECT_DIR.parents:
        if (parent / "src" / "mini_agent").is_dir():
            return parent
    return PROJECT_DIR.parents[1]
