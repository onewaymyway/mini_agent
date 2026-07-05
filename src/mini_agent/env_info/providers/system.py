"""
env_info/providers/system.py — 操作系统基础信息

采集：
  - OS 类型和版本（Windows / macOS / Linux + 发行版）
  - CPU 架构（x86_64 / arm64 / …）
  - 主机名（可选，默认关闭，隐私敏感）
  - 用户名（可选，默认关闭，隐私敏感）
"""

from __future__ import annotations

import platform
import sys
from typing import TYPE_CHECKING

from mini_agent.env_info.base import EnvInfoProvider

if TYPE_CHECKING:
    pass


def _os_friendly_name() -> str:
    """返回友好的 OS 描述字符串。"""
    system = platform.system()

    if system == "Darwin":
        # macOS：取版本号
        mac_ver = platform.mac_ver()[0]
        version_str = f" {mac_ver}" if mac_ver else ""
        return f"macOS{version_str}"

    elif system == "Windows":
        win_ver = platform.version()
        release = platform.release()
        return f"Windows {release} ({win_ver})"

    elif system == "Linux":
        # 尝试读取发行版信息
        try:
            import distro  # type: ignore
            name = distro.name(pretty=True)
            if name:
                return f"Linux ({name})"
        except ImportError:
            pass
        # 回退：读 /etc/os-release
        try:
            with open("/etc/os-release", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        pretty = line.split("=", 1)[1].strip().strip('"')
                        return f"Linux ({pretty})"
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.env_info.providers.system')
            pass
        return f"Linux ({platform.release()})"

    else:
        return system or "Unknown OS"


class SystemInfoProvider(EnvInfoProvider):
    """采集操作系统、架构等基础系统信息。"""

    name = "builtin.system"

    def __init__(
        self,
        include_hostname: bool = False,
        include_username: bool = False,
    ) -> None:
        self._include_hostname = include_hostname
        self._include_username = include_username

    def collect(self) -> dict[str, str]:
        info: dict[str, str] = {}

        # OS 类型
        info["OS"] = _os_friendly_name()

        # CPU 架构
        machine = platform.machine()
        if machine:
            # 统一别名
            arch_aliases = {"AMD64": "x86_64", "aarch64": "arm64"}
            info["Arch"] = arch_aliases.get(machine, machine)

        # 主机名（可选）
        if self._include_hostname:
            try:
                import socket
                hostname = socket.gethostname()
                if hostname:
                    info["Hostname"] = hostname
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.env_info.providers.system')
                pass

        # 用户名（可选）
        if self._include_username:
            try:
                import getpass
                user = getpass.getuser()
                if user:
                    info["User"] = user
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.env_info.providers.system')
                pass

        return info
