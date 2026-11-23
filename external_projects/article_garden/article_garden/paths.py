"""paths.py — 项目目录与数据根目录定位。

数据根目录（data root）：环境变量 ARTICLE_GARDEN_HOME 优先，否则 <项目>/data。
  data/domains.json        文章库注册表
  data/settings.yaml       全局默认设置（只含可全局覆盖的分组，见 settings.py）
  data/domains/<id>/       每个文章库一个完全独立的工作空间
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent  # external_projects/article_garden


def data_root() -> Path:
    env = os.environ.get("ARTICLE_GARDEN_HOME", "").strip()
    return Path(env).expanduser() if env else PROJECT_DIR / "data"


def domains_root() -> Path:
    return data_root() / "domains"


def registry_path() -> Path:
    return data_root() / "domains.json"


def global_settings_path() -> Path:
    return data_root() / "settings.yaml"


def mini_agent_root() -> Path:
    """向上查找含 src/mini_agent 的仓库根目录（外部项目位于 <repo>/external_projects/<名>）。"""
    for parent in PROJECT_DIR.parents:
        if (parent / "src" / "mini_agent").is_dir():
            return parent
    return PROJECT_DIR.parents[1]
