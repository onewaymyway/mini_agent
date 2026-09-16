"""world_simulator/config.py — 项目路径常量 + LLM 配置加载入口。

与 `stock_watch/config.py` 同样的风格：轻量常量 + 一个 `ensure_dirs()`，
不引入额外配置框架。

`load_llm_cfg()`：本项目所有 LLM 相关调用（`spec_generator.
generate_scenario` / `engine.advance` / `autopilot.run_autopilot_step`
等）统一走这一个入口获取 `AppConfig`，而不是各处各自
`from mini_agent.config import load_config`——不是要重新实现一套 LLM
调用/配置逻辑，恰恰相反：这里只做“在调用宿主的 `load_config()` 之前，
让它更容易找到主项目的 LLM 配置”这一件事，逻辑本身完全交给
`mini_agent.config.loader.load_config()`（见其
`_resolve_main_project_root()`）。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"

_MAIN_PROJECT_ROOT_ENV = "MINI_AGENT_MAIN_PROJECT_ROOT"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _detect_in_place_main_project_root() -> Path | None:
    """本项目按外部项目标准布局挂在
    `<主项目根>/external_projects/world_simulator/` 时，`main_project_root`
    其实就是 `PROJECT_ROOT.parent.parent`，不需要注册表或环境变量也能
    推出来——这只是"还没注册/没显式设置环境变量"时的**开发期便利兜底**，
    不改变 `mini_agent.config.loader._resolve_main_project_root()` 既有的
    优先级（环境变量 > 注册表）；本项目一旦被真正搬到独立路径（不再是
    `external_projects/` 下的子目录），这条判断自然不成立，`load_config()`
    照常退回"纯环境变量"的兜底路径，不会报错也不会误判。
    """
    candidate = PROJECT_ROOT.parent.parent
    if PROJECT_ROOT.parent.name != "external_projects":
        return None
    # 用"看起来像 mini_agent 主仓库根"的两个信号之一判断，避免误认一个
    # 无关的 `external_projects/` 同名目录：
    #   1. 开发环境（源码安装）：<root>/src/mini_agent 存在
    #   2. 打包/其它安装方式：<root>/agent_config.json 存在（主项目的
    #      配置文件约定就放在项目根）
    if (candidate / "src" / "mini_agent").is_dir() or (candidate / "agent_config.json").exists():
        return candidate
    return None


def ensure_main_project_root_env() -> None:
    """在调用 `mini_agent.config.load_config()` 之前调用一次：如果当前
    进程还没有 `MINI_AGENT_MAIN_PROJECT_ROOT` 环境变量、也没有走注册表
    （两者都是 `load_config()` 内部判断，这里不重复判断，只补环境变量
    这一条），且本项目当前就"原地"躺在某个 mini_agent 主仓库的
    `external_projects/` 下，就把环境变量设置好，让 `load_config()` 能
    直接继承主项目的 `agent_config.json`/`providers.json`（含 API key）
    ——这样本地开发/未注册进 daemon 时也不需要用户手动
    `export MINI_AGENT_MAIN_PROJECT_ROOT=...` 或额外维护一份
    `providers.json`，与方案 2.2 节"不新写一套调用/重试/降级逻辑，直接
    复用 LLMHelper 的统一入口"的取舍是同一个方向：这里补的只是"怎么把
    主项目配置传进去"这一步，不是另起一套 LLM 调用。
    """
    if os.environ.get(_MAIN_PROJECT_ROOT_ENV):
        return
    detected = _detect_in_place_main_project_root()
    if detected is not None:
        os.environ[_MAIN_PROJECT_ROOT_ENV] = str(detected)


def load_llm_cfg(project_root: Path | None = None):
    """本项目所有需要调用 LLM 的代码路径（看板 `_load_cfg()`、各
    entrypoint）的统一 `AppConfig` 加载入口。行为 = 先补齐
    `ensure_main_project_root_env()`，再调用宿主
    `mini_agent.config.load_config()`——真正的 provider/api_key/重试/
    fallback chain 逻辑完全由宿主实现，这里不重复、不绕过。

    找不到任何可用 LLM 配置（比如本地开发机确实没配过 API key）时，
    `load_config()` 本身不会报错——报错发生在真正发起 LLM 调用的那一刻
    （`LLMConfig.requires_api_key` 检查），错误信息里会指出"缺 api_key"，
    这属于预期行为：本项目的职责是"把配置传对"，不是"帮用户配好 key"。
    """
    from mini_agent.config import load_config

    ensure_main_project_root_env()
    return load_config(project_root=project_root or PROJECT_ROOT)
