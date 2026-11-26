"""mini_agent 项目根目录的查找（纯逻辑，不依赖 Qt）。

优先级（靠前的优先，找到即停）：
  1. 命令行 --project-root              （显式，不校验标记，原样采用）
  2. desktop.json 的 project_root       （显式，不校验标记）
  3. 环境变量 MINI_AGENT_PROJECT_ROOT   （显式，不校验标记）
  4. 从当前工作目录向上查找             （校验标记）
  5. 从桌面端自身所在目录向上查找       （校验标记）
  6. 都没有 → 返回 None，由调用方给出明确提示

"项目根目录"的标记：存在 src/mini_agent/__init__.py。
桌面端位于 <项目根>/apps/mini_agent_desktop/，所以第 5 步在仓库内任何位置都能找到项目。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MARKER = Path("src") / "mini_agent" / "__init__.py"
ENV_VAR = "MINI_AGENT_PROJECT_ROOT"
MAX_LEVELS = 10

SOURCE_CLI = "命令行 --project-root"
SOURCE_CONFIG = "desktop.json"
SOURCE_ENV = f"环境变量 {ENV_VAR}"
SOURCE_CWD = "当前目录向上查找"
SOURCE_APP = "桌面端安装位置向上查找"


@dataclass(frozen=True)
class Resolved:
    path: Path
    source: str


def is_project_root(path: Path) -> bool:
    return (path / MARKER).is_file()


def find_upwards(start: Path, max_levels: int = MAX_LEVELS) -> Optional[Path]:
    """从 start（含）开始逐级向上找项目根；找不到返回 None。"""
    current = start.resolve()
    for _ in range(max_levels + 1):
        if is_project_root(current):
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def resolve_project_root(
    *,
    cli_value: str = "",
    config_value: str = "",
    env: Optional[dict] = None,
    cwd: Optional[Path] = None,
    app_location: Optional[Path] = None,
) -> Optional[Resolved]:
    env = os.environ if env is None else env
    for value, source in ((cli_value, SOURCE_CLI), (config_value, SOURCE_CONFIG),
                          (env.get(ENV_VAR, ""), SOURCE_ENV)):
        if value:
            return Resolved(Path(value).expanduser().resolve(), source)

    found = find_upwards(cwd or Path.cwd())
    if found is not None:
        return Resolved(found, SOURCE_CWD)

    here = app_location or Path(__file__)
    found = find_upwards(here.resolve().parent)
    if found is not None:
        return Resolved(found, SOURCE_APP)
    return None
