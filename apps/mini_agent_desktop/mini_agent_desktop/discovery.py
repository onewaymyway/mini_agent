"""发现本机 mini_agent daemon：端口、token、崩溃记录，以及（可选）拉起它。

daemon 与项目根目录绑定，状态都在 <project_root>/.agent/ 下：
  - daemon_info.json            端口/pid（daemon 运行时存在）
  - agent_api.key               单用户模式 token
  - users/tokens/owner.key      多用户模式 owner token
  - notification/daemon_crash_alerts.jsonl   崩溃告警（supervisor 写入）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_TOKEN_CANDIDATES = (
    Path(".agent") / "agent_api.key",
    Path(".agent") / "users" / "tokens" / "owner.key",
)


@dataclass(frozen=True)
class Endpoint:
    base_url: str          # 例如 http://127.0.0.1:8765
    token: Optional[str]
    port: int


def read_token(project_root: Path) -> Optional[str]:
    for rel in _TOKEN_CANDIDATES:
        try:
            token = (project_root / rel).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if token:
            return token
    return None


def read_daemon_info(project_root: Path) -> Optional[dict]:
    try:
        data = json.loads((project_root / ".agent" / "daemon_info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def resolve_endpoint(project_root: Path, default_port: int = 8765) -> Endpoint:
    """端口以 daemon_info.json 为准（可能是上次非默认端口启动的），否则用配置端口。
    只绑定 127.0.0.1，不做远程发现。"""
    info = read_daemon_info(project_root) or {}
    port = info.get("http_port")
    if not isinstance(port, int) or isinstance(port, bool) or not (0 < port < 65536):
        port = default_port
    return Endpoint(base_url=f"http://127.0.0.1:{port}", token=read_token(project_root), port=port)


def count_unacked_crash_alerts(project_root: Path) -> int:
    """直接读崩溃告警文件统计未确认条数。daemon 已死时 HTTP 不可用，
    这是离线状态下发现"刚刚崩溃过"的唯一办法。"""
    p = project_root / ".agent" / "notification" / "daemon_crash_alerts.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    n = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and not rec.get("acknowledged"):
            n += 1
    return n


def build_daemon_command(port: int, python: Optional[str] = None) -> list[str]:
    return [python or sys.executable, "-m", "mini_agent", "daemon", "start",
            "--detach", "--http-port", str(port)]


def spawn_daemon(project_root: Path, port: int, python: Optional[str] = None) -> subprocess.Popen:
    """拉起后台 daemon（`daemon start --detach` 自己会脱离控制台并启 supervisor）。
    这里只是触发命令；调用方随后靠 health 轮询判断是否真的起来了。"""
    kwargs: dict = {
        "cwd": str(project_root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        # 不要为这个一次性启动命令闪出黑色控制台窗口
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.Popen(build_daemon_command(port, python), **kwargs)
