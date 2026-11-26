"""S4：token 文件权限加固（Windows ACL，经 icacls）。

目标：把 `.agent/agent_api.key` 等 token 文件的访问权限收紧为"仅当前用户"，
去掉从父目录继承来的 ACE。只在用户于托盘菜单里明确点击时执行，不做后台自动改动。

注意：/grant:r 会替换该文件上已有的显式授权，因此只授予当前用户完全控制。
若 daemon 以别的账户运行，需要先手动调整。非 Windows 上返回 unsupported。
"""

from __future__ import annotations

import getpass
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

TOKEN_RELPATHS = (
    Path(".agent") / "agent_api.key",
    Path(".agent") / "users" / "tokens" / "owner.key",
)


@dataclass(frozen=True)
class HardenResult:
    path: str
    status: str            # hardened / missing / failed / unsupported
    message: str = ""


def token_files(project_root: Path) -> list[Path]:
    return [project_root / rel for rel in TOKEN_RELPATHS]


def current_user() -> str:
    return os.environ.get("USERNAME") or getpass.getuser()


def icacls_argv(path: Path, username: str) -> list[str]:
    return ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:F"]


Runner = Callable[[list[str]], "subprocess.CompletedProcess"]


def _default_runner(argv: list[str]) -> "subprocess.CompletedProcess":  # pragma: no cover
    return subprocess.run(argv, capture_output=True, text=True, timeout=20)


def harden_file(path: Path, *, username: Optional[str] = None,
                runner: Optional[Runner] = None, is_windows: Optional[bool] = None) -> HardenResult:
    windows = (os.name == "nt") if is_windows is None else is_windows
    if not windows:
        return HardenResult(str(path), "unsupported", "仅 Windows 支持 ACL 加固")
    if not path.exists():
        return HardenResult(str(path), "missing", "文件不存在")
    user = username or current_user()
    run = runner or _default_runner
    try:
        proc = run(icacls_argv(path, user))
    except (OSError, subprocess.SubprocessError) as exc:
        return HardenResult(str(path), "failed", f"icacls 无法执行：{exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return HardenResult(str(path), "failed", detail or f"icacls 退出码 {proc.returncode}")
    return HardenResult(str(path), "hardened", f"仅授予 {user} 访问权限")


def harden_project_tokens(project_root: Path, **kwargs) -> list[HardenResult]:
    return [harden_file(p, **kwargs) for p in token_files(project_root)]
