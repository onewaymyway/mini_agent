"""
env_info/providers/runtime.py — Python 运行时信息

采集：
  - Python 版本
  - 当前工作目录（取目录名，不展示完整路径，减少噪音）
  - 虚拟环境名（若在 venv / conda env 中）
  - 可执行文件路径（可选）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mini_agent.env_info.base import EnvInfoProvider


def _detect_venv() -> str | None:
    """检测当前是否在虚拟环境中，返回友好名称或 None。"""
    # conda env
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    if conda_env and conda_env != "base":
        return f"conda:{conda_env}"

    # venv / virtualenv
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        return Path(venv).name  # 只取目录名，如 ".venv"

    # pyenv
    pyenv_version = os.environ.get("PYENV_VERSION")
    if pyenv_version and pyenv_version != "system":
        return f"pyenv:{pyenv_version}"

    return None


class RuntimeInfoProvider(EnvInfoProvider):
    """采集 Python 运行时环境信息。"""

    name = "builtin.runtime"

    def __init__(self, include_executable: bool = False) -> None:
        self._include_executable = include_executable

    def collect(self) -> dict[str, str]:
        info: dict[str, str] = {}

        # Python 版本
        ver = sys.version_info
        info["Python"] = f"{ver.major}.{ver.minor}.{ver.micro}"

        # 虚拟环境
        venv = _detect_venv()
        if venv:
            info["Venv"] = venv

        # 当前工作目录（只取最后两级，保持简洁）
        try:
            cwd = Path.cwd()
            parts = cwd.parts
            if len(parts) >= 3:
                friendly = f"…/{parts[-2]}/{parts[-1]}"
            else:
                friendly = str(cwd)
            info["CWD"] = friendly
        except Exception:
            pass

        # 可执行文件（可选，一般不需要）
        if self._include_executable:
            try:
                info["Python bin"] = sys.executable
            except Exception:
                pass

        return info
