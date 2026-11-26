"""S4：登录自启（HKCU\\...\\Run）。

- 只写/删除自己的值 `mini_agent_desktop`，不碰用户的其它启动项；
- 启动命令用 pythonw（无控制台窗口）+ 仓库内的 launch_desktop.pyw 启动器，
  因为 Run 项的工作目录不可控，不能依赖 `-m mini_agent_desktop` 能被找到；
- 同步策略：启动时以 desktop.json 的 autostart 为准，写入或删除；
- 非 Windows 上 store 为 None，所有操作返回 "unsupported"，不抛异常。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Protocol

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "mini_agent_desktop"
LAUNCHER_NAME = "launch_desktop.pyw"


def launcher_path() -> Path:
    """仓库内启动器：把本目录加入 sys.path 后调用 app.main()。"""
    return Path(__file__).resolve().parent.parent / LAUNCHER_NAME


def pythonw_for(python_exe: str) -> str:
    """python.exe → pythonw.exe（同目录存在时），避免启动时弹控制台窗口。"""
    p = Path(python_exe)
    if p.name.lower() == "python.exe":
        candidate = p.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(p)


def build_command(python_exe: str, launcher: Path, project_root: Optional[Path]) -> str:
    parts = [f'"{pythonw_for(python_exe)}"', f'"{launcher}"']
    if project_root is not None:
        parts += ["--project-root", f'"{project_root}"']
    return " ".join(parts)


class RunStore(Protocol):
    def get(self) -> Optional[str]: ...
    def set(self, command: str) -> None: ...
    def delete(self) -> None: ...


class WinRegRunStore:  # pragma: no cover - 仅 Windows 实机验证
    def get(self) -> Optional[str]:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, VALUE_NAME)
                return str(value)
        except FileNotFoundError:
            return None

    def set(self, command: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)

    def delete(self) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass


def default_store() -> Optional[RunStore]:
    return WinRegRunStore() if os.name == "nt" else None


def sync(store: Optional[RunStore], enabled: bool, command: str) -> str:
    """把登录自启调整为 enabled 指定的状态。返回 enabled / disabled / unchanged / unsupported。"""
    if store is None:
        return "unsupported"
    current = store.get()
    if enabled:
        if current == command:
            return "unchanged"
        store.set(command)
        return "enabled"
    if current is None:
        return "unchanged"
    store.delete()
    return "disabled"
