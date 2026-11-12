"""接收服务（python -m server.feedback_receiver）的进程管理：启动、停止、状态、端口。

- 由看板启动的服务会记录 pid 到 data/state/receiver_process.json，并把输出写入 data/logs/receiver.log。
- 端口优先取看板保存的运行时配置（data/state/runtime_config.json 的 receiver.port），否则用 settings.yaml。
- 服务也可能由用户在终端启动；状态检测以 /v1/ping 为准，停止时只能停止由看板启动的进程。
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from core.dedicated_browser import DEFAULT_RECEIVER_PORT
from core.settings import Settings, read_runtime_config
from core.store import atomic_write_json, read_json

MIN_PORT = 1024
MAX_PORT = 65535
START_TIMEOUT = 8.0
STOP_TIMEOUT = 5.0


class ServiceError(ValueError):
    pass


def effective_port(settings: Settings) -> int:
    rt = read_runtime_config(settings.paths.state_dir / "runtime_config.json")
    value = (rt.get("receiver") or {}).get("port")
    if isinstance(value, int) and MIN_PORT <= value <= MAX_PORT:
        return value
    from core.dedicated_browser import load_config
    return int(load_config(settings.paths.root).get("receiver_port", DEFAULT_RECEIVER_PORT))


def set_port(settings: Settings, port: Any) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError):
        raise ServiceError("端口必须是数字")
    if not MIN_PORT <= value <= MAX_PORT:
        raise ServiceError(f"端口需在 {MIN_PORT}–{MAX_PORT} 之间")
    path = settings.paths.state_dir / "runtime_config.json"
    data = read_runtime_config(path)
    data["receiver"] = {"port": value}
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    atomic_write_json(path, data)
    return value


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def ping(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/ping", timeout=timeout) as resp:
            return resp.status == 200 and bool(json.loads(resp.read().decode("utf-8")).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _state_path(settings: Settings) -> Path:
    return settings.paths.state_dir / "receiver_process.json"


def log_path(settings: Settings) -> Path:
    return settings.paths.data_dir / "logs" / "receiver.log"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _tail(path: Path, lines: int = 20) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def status(settings: Settings, port: Optional[int] = None) -> dict[str, Any]:
    port = port if port is not None else effective_port(settings)
    state = read_json(_state_path(settings), default={}) or {}
    managed_pid = int(state.get("pid") or 0)
    managed = bool(managed_pid) and _pid_alive(managed_pid) and state.get("port") == port
    running = ping(port)
    if running:
        mode = "managed" if managed else "external"
    else:
        mode = "stopped"
    return {"port": port, "running": running, "mode": mode,
            "pid": managed_pid if managed else None,
            "port_free": port_free(port) if not running else False,
            "log_tail": _tail(log_path(settings))}


def start(settings: Settings, port: Optional[int] = None) -> dict[str, Any]:
    port = port if port is not None else effective_port(settings)
    if ping(port):
        raise ServiceError(f"端口 {port} 上已有接收服务在运行，无需重复启动")
    if not port_free(port):
        raise ServiceError(f"端口 {port} 已被其他程序占用，请换一个端口后再启动")
    log_path(settings).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    code_dir = str(Path(__file__).resolve().parents[1])  # 包含 server 包的 info_hub 目录
    env = {**os.environ, "INFO_HUB_ROOT": str(settings.paths.root),
           "PYTHONPATH": os.pathsep.join(filter(None, [code_dir, os.environ.get("PYTHONPATH", "")]))}
    with open(log_path(settings), "a", encoding="utf-8") as log:  # 子进程继承句柄，父进程可安全关闭
        proc = subprocess.Popen([sys.executable, "-m", "server.feedback_receiver", str(port)],
                                cwd=str(settings.paths.root), env=env, stdout=log,
                                stderr=subprocess.STDOUT, **kwargs)
    atomic_write_json(_state_path(settings), {"pid": proc.pid, "port": port,
                                              "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if ping(port, timeout=0.5):
            return status(settings, port)
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    _kill(proc.pid)
    _clear_state(settings)
    raise ServiceError("接收服务启动失败，最近日志：\n" + "\n".join(_tail(log_path(settings), 8)))


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def _clear_state(settings: Settings) -> None:
    try:
        _state_path(settings).unlink()
    except OSError:
        pass


def stop(settings: Settings) -> dict[str, Any]:
    state = read_json(_state_path(settings), default={}) or {}
    pid = int(state.get("pid") or 0)
    port = int(state.get("port") or effective_port(settings))
    if not pid or not _pid_alive(pid):
        if ping(effective_port(settings)):
            raise ServiceError("接收服务不是由看板启动的，请在启动它的终端中停止")
        _clear_state(settings)
        raise ServiceError("接收服务当前没有在运行")
    _kill(pid)
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    _clear_state(settings)
    return status(settings, port)
