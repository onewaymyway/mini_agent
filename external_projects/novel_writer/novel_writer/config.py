"""novel_writer/config.py — 项目路径常量 + LLM 配置加载入口。

与 `world_simulator/config.py` 同样的取舍：本项目**不实现任何 LLM 调用/
重试/降级逻辑**，所有 LLM 能力来自 mini_agent；这里只负责"把主项目配置
传对"（`load_llm_cfg()`），真正的 provider/api_key/fallback 完全交给宿主
`mini_agent.config.load_config()`。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 环境变量 NOVEL_WRITER_DATA_DIR 可整体改数据根（测试隔离 / 把所有小说放到别的盘）
DATA_DIR = Path(os.environ.get("NOVEL_WRITER_DATA_DIR") or (PROJECT_ROOT / "data"))
# 每本小说的默认工作目录根：data/novels/<书名>_<时间戳>/（创建时可改为任意绝对路径）
NOVELS_DIR = DATA_DIR / "novels"
# 书架索引：记录放在默认目录之外的工作目录位置
SHELF_INDEX_FILE = DATA_DIR / "shelf.json"
SKILLS_DIR = PROJECT_ROOT / "skills"
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"
REPORTS_DIR = PROJECT_ROOT / "reports"

_MAIN_PROJECT_ROOT_ENV = "MINI_AGENT_MAIN_PROJECT_ROOT"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NOVELS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _detect_in_place_main_project_root() -> Path | None:
    """本项目挂在 `<主项目根>/external_projects/novel_writer/` 下时，主项目根
    就是 `PROJECT_ROOT.parent.parent`（开发期便利兜底，优先级低于环境变量/
    注册表；与 world_simulator 同一写法）。"""
    candidate = PROJECT_ROOT.parent.parent
    if PROJECT_ROOT.parent.name != "external_projects":
        return None
    if (candidate / "src" / "mini_agent").is_dir() or (candidate / "agent_config.json").exists():
        return candidate
    return None


def ensure_main_project_root_env() -> None:
    """调用 `mini_agent.config.load_config()` 之前补齐
    `MINI_AGENT_MAIN_PROJECT_ROOT`，让它能继承主项目的 LLM 配置（含 API key）。"""
    if os.environ.get(_MAIN_PROJECT_ROOT_ENV):
        return
    detected = _detect_in_place_main_project_root()
    if detected is not None:
        os.environ[_MAIN_PROJECT_ROOT_ENV] = str(detected)


def load_llm_cfg(project_root: Path | None = None):
    """所有需要 LLM 的代码路径（看板、worker、entrypoint）统一的 `AppConfig`
    加载入口。找不到 API key 时不在这里报错，而是在真正发起 LLM 调用时由
    宿主报"缺 api_key"——本项目的职责是把配置传对，不是帮用户配 key。"""
    from mini_agent.config import load_config

    ensure_main_project_root_env()
    return load_config(project_root=project_root or PROJECT_ROOT)
