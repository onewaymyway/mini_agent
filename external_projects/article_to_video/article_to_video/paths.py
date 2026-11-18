"""paths.py — 项目目录与项目集合根目录定位。"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent  # external_projects/article_to_video


def projects_root() -> Path:
    """项目集合根目录：环境变量 ARTICLE_TO_VIDEO_HOME 优先，否则为 <项目>/projects。"""
    env = os.environ.get("ARTICLE_TO_VIDEO_HOME", "").strip()
    return Path(env).expanduser() if env else PROJECT_DIR / "projects"
