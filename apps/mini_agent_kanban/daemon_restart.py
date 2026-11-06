"""daemon_restart.py — 看板“重启 daemon”功能的纯逻辑（不依赖 Streamlit，便于单测）。

权限：仅在开启登录门禁（--require-login）且当前用户是管理员时允许。
执行：在看板所在机器上，以分离进程运行 `python main.py daemon restart --project <root>`；
      该命令内部完成“停止 daemon+supervisor → 等待退出 → 按记录参数重新后台启动”。
进度：读取 <root>/.agent/daemon_restart_status.json。
审计：每次点击（无论成功与否）追加到 <root>/.agent/daemon_restart_audit.log。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

IN_PROGRESS_STAGES = ("stopping", "waiting", "starting")
_STATUS_REL = Path(".agent") / "daemon_restart_status.json"
_LOG_REL = Path(".agent") / "daemon_restart.log"
_AUDIT_REL = Path(".agent") / "daemon_restart_audit.log"


def can_restart(require_login: bool, username: str, is_admin: bool) -> bool:
    """只有“登录门禁已开启 + 已登录 + 管理员”才允许重启。未开启登录门禁时一律不允许。"""
    return bool(require_login) and bool(username) and bool(is_admin)


def status_path(root: Path) -> Path:
    return root / _STATUS_REL


def read_status(root: Path) -> Optional[dict]:
    try:
        data = json.loads(status_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_busy(status: Optional[dict], max_age_seconds: float = 600.0) -> bool:
    """是否有重启正在进行（按钮需置灰）。超过 max_age 视为已结束（防止异常残留状态锁死按钮）。"""
    if not status or status.get("stage") not in IN_PROGRESS_STAGES:
        return False
    return time.time() - float(status.get("updated_at") or 0) <= max_age_seconds


def append_audit(root: Path, username: str, result: str) -> None:
    path = root / _AUDIT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "user": username,
                       "action": "daemon_restart", "result": result}, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _detached_popen_kwargs() -> dict:
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        return {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def launch_restart(root: Path, username: str, python_exe: Optional[str] = None) -> tuple[bool, str]:
    """以分离进程启动 `daemon restart`。返回 (是否已提交, 说明)。调用方须先做权限校验。"""
    main_py = root / "main.py"
    if not main_py.is_file():
        msg = f"找不到 {main_py}，无法执行重启（请确认看板的项目根目录正确）"
        append_audit(root, username, f"failed: {msg}")
        return False, msg
    if is_busy(read_status(root)):
        return False, "已有一次重启正在进行中，请等待其完成。"

    log_path = root / _LOG_REL
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MINI_AGENT_RESTART_REQUESTED_BY"] = username
    cmd = [python_exe or sys.executable, str(main_py), "daemon", "restart", "--project", str(root)]
    try:
        with log_path.open("ab") as log:
            subprocess.Popen(cmd, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT, close_fds=True,
                             **_detached_popen_kwargs())
    except OSError as exc:
        msg = f"启动重启进程失败：{exc}"
        append_audit(root, username, f"failed: {msg}")
        return False, msg

    append_audit(root, username, "submitted")
    return True, "已提交重启请求，正在停止旧进程并启动新进程（约需 10–60 秒）。"
