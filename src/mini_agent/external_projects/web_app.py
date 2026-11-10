"""
external_projects/web_app.py — 外部项目 Streamlit 界面的探测 / 启动 / 停止 / 链接

设计依据：next_doc/external_projects_web_app_plan.md

关键约定：
  - 由**看板**（Streamlit 看板进程）直接调用本模块，不经过 daemon。
  - 被启动的 Streamlit 是**独立新进程**（POSIX 下新会话、Windows 下新进程组），
    看板或 daemon 重启都不会把它带走；下次看板启动时通过端口探测重新识别。
  - 看板只能停止"由看板启动的"进程：通过项目目录下 `.run/streamlit.pid`
    记录的 PID 判断归属。手动在终端启动的实例可以探测、可以打开，但不能由看板停止。
  - `web_app.host` 未填（或 localhost/127.0.0.1）= 同一台机器，可启停；
    填了远程 host = 远程部署，看板只做探测与打开链接。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from mini_agent.external_projects.manifest import (
    ProjectManifestError,
    WebAppSpec,
    load_manifest,
)
from mini_agent.external_projects.registry import ExternalProjectRegistry
from mini_agent.utils import win_subprocess

RUN_DIR_NAME = ".run"
PID_FILE_NAME = "streamlit.pid"
LOG_FILE_NAME = "streamlit.log"

# 看板等待新进程就绪的上限（秒）
START_READY_TIMEOUT_SEC = 30.0
# 停止时先 SIGTERM/taskkill，等待端口释放的上限（秒），超时则强杀
STOP_GRACE_SEC = 8.0

_WINDOWS = sys.platform == "win32"
_CREATE_NEW_PROCESS_GROUP = 0x00000200


# ── 状态 ───────────────────────────────────────────────────────────────

STATE_STOPPED = "stopped"            # 没有监听，也没有记录的进程
STATE_STARTING = "starting"          # 看板刚拉起，还没通过健康检查
STATE_RUNNING = "running"            # 健康，且由看板启动（可停止）
STATE_RUNNING_EXTERNAL = "running_external"  # 健康，但不是看板启动的（只能打开）
STATE_PORT_BUSY = "port_busy"        # 端口被非 Streamlit 程序占用
STATE_REMOTE_UP = "remote_up"        # 远程项目，健康
STATE_REMOTE_DOWN = "remote_down"    # 远程项目，不可达

STATE_LABELS = {
    STATE_STOPPED: "⚪ 未运行",
    STATE_STARTING: "🟡 启动中",
    STATE_RUNNING: "🟢 运行中（看板启动）",
    STATE_RUNNING_EXTERNAL: "🟢 运行中（外部启动）",
    STATE_PORT_BUSY: "🔴 端口被占用",
    STATE_REMOTE_UP: "🟢 远程可达",
    STATE_REMOTE_DOWN: "⚪ 远程不可达",
}


@dataclass
class WebAppStatus:
    name: str
    state: str
    port: int
    host: Optional[str]
    url: str
    pid: Optional[int] = None
    message: str = ""
    log_path: Optional[str] = None

    @property
    def is_up(self) -> bool:
        return self.state in (STATE_RUNNING, STATE_RUNNING_EXTERNAL, STATE_REMOTE_UP)

    @property
    def can_start(self) -> bool:
        return self.state == STATE_STOPPED

    @property
    def can_stop(self) -> bool:
        """由看板启动（或正在启动）的实例，看板可直接停止。"""
        return self.state in (STATE_RUNNING, STATE_STARTING)

    @property
    def can_force_stop(self) -> bool:
        """外部启动的 Streamlit 实例：看板可结束它，但必须用户确认（见 stop_external）。"""
        return self.state == STATE_RUNNING_EXTERNAL

    @property
    def can_restart(self) -> bool:
        return self.state in (STATE_RUNNING, STATE_RUNNING_EXTERNAL, STATE_STARTING)

    @property
    def label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)


# ── 地址 ───────────────────────────────────────────────────────────────

def probe_host(spec: WebAppSpec) -> str:
    """看板探测/连接时使用的主机名。

    远程（填了 host）用 host；本机启动时按实际监听地址探测：监听 0.0.0.0 或 127.0.0.1
    时用回环地址连接，监听某个具体网卡 IP 时用该 IP（否则会误判为未运行）。
    """
    if spec.host:
        return spec.host
    if spec.bind and spec.bind not in ("0.0.0.0", "::", "localhost"):
        return spec.bind
    return "127.0.0.1"


def web_app_url(spec: WebAppSpec) -> str:
    """浏览器打开用的地址：未填 host 时用 localhost（本机访问）。"""
    return f"http://{spec.host or 'localhost'}:{spec.port}"


def detect_lan_ip() -> Optional[str]:
    """探测本机出口网卡的局域网 IP（不真正发包，只让内核选路）。失败返回 None。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            ip = sock.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def lan_url(spec: WebAppSpec) -> Optional[str]:
    """局域网可访问地址：仅当监听非回环地址时返回。

    bind 为具体 IP 时直接用它；bind 为 0.0.0.0 时探测本机局域网 IP，探测不到返回 None。
    """
    if not spec.listens_beyond_loopback:
        return None
    host = spec.bind if spec.bind not in ("0.0.0.0", "::") else detect_lan_ip()
    if not host:
        return None
    return f"http://{host}:{spec.port}"


# ── 探测 ───────────────────────────────────────────────────────────────

def port_listening(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def streamlit_healthy(host: str, port: int, health_path: str, timeout: float = 1.0) -> bool:
    """Streamlit 的 `/_stcore/health` 返回 200 且正文为 ok，才认为是 Streamlit 应用。"""
    url = f"http://{host}:{port}{health_path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200 and b"ok" in resp.read(64)
    except Exception:
        return False


# ── PID 记录 ───────────────────────────────────────────────────────────

def _run_dir(root: Path) -> Path:
    return root / RUN_DIR_NAME


def pid_file(root: Path) -> Path:
    return _run_dir(root) / PID_FILE_NAME


def log_file(root: Path) -> Path:
    return _run_dir(root) / LOG_FILE_NAME


def read_pid_record(root: Path) -> Optional[dict]:
    path = pid_file(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("pid"), int):
            return data
    except (OSError, ValueError):
        pass
    return None


def _write_pid_record(root: Path, record: dict) -> None:
    _run_dir(root).mkdir(parents=True, exist_ok=True)
    pid_file(root).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _remove_pid_record(root: Path) -> None:
    try:
        pid_file(root).unlink()
    except FileNotFoundError:
        pass


def pid_alive(pid: int) -> bool:
    """判断进程是否存活；Linux 下僵尸进程视为已退出。"""
    if _WINDOWS:
        try:
            out = win_subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"], text=True, timeout=10
            )
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            # 格式: pid (comm) state ...；comm 可能含括号，取最后一个 ')' 之后的字段
            state = stat_path.read_text().rsplit(")", 1)[1].split()[0]
            if state in ("Z", "X"):
                return False
        except (OSError, IndexError):
            pass
    return True


# ── 命令构造 ───────────────────────────────────────────────────────────

def _resolve_python(root: Path, spec: WebAppSpec) -> str:
    if not spec.python:
        return sys.executable
    candidate = Path(spec.python).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    return str(candidate)


def build_command(root: Path, spec: WebAppSpec) -> List[str]:
    """返回用于 Popen 的 argv 列表（不经过 shell）。

    - 缺省：`<python> -m streamlit run <entry> --server.port <port> ...`
    - 填了 `command`：按 shell 规则切分；其中可用占位符 `{python}` `{entry}` `{port}`。
    """
    python = _resolve_python(root, spec)
    if spec.command:
        text = spec.command.format(python=python, entry=spec.entry, port=spec.port)
        return shlex.split(text, posix=not _WINDOWS)
    return [
        python, "-m", "streamlit", "run", spec.entry,
        "--server.port", str(spec.port),
        "--server.address", spec.bind_address,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]


# ── 状态聚合 ───────────────────────────────────────────────────────────

def get_status(name: str, root: Path, spec: WebAppSpec) -> WebAppStatus:
    host = probe_host(spec)
    url = web_app_url(spec)
    base = dict(name=name, port=spec.port, host=spec.host, url=url)

    if spec.is_remote:
        up = streamlit_healthy(host, spec.port, spec.health_path, timeout=2.0)
        return WebAppStatus(state=STATE_REMOTE_UP if up else STATE_REMOTE_DOWN,
                            message="远程项目：看板只探测与打开，不负责启停", **base)

    record = read_pid_record(root)
    owned_pid = None
    if record is not None:
        if record.get("port") == spec.port and pid_alive(record["pid"]):
            owned_pid = record["pid"]
        elif not pid_alive(record["pid"]):
            _remove_pid_record(root)  # 进程已不在，清理陈旧记录
            record = None
    log_path = str(log_file(root))

    listening = port_listening(host, spec.port)
    healthy = listening and streamlit_healthy(host, spec.port, spec.health_path)

    if healthy:
        if owned_pid is not None:
            return WebAppStatus(state=STATE_RUNNING, pid=owned_pid, log_path=log_path,
                                message="由看板启动", **base)
        return WebAppStatus(state=STATE_RUNNING_EXTERNAL, log_path=log_path,
                            message="不是由看板启动，看板无法停止它", **base)
    if listening:
        return WebAppStatus(state=STATE_PORT_BUSY, log_path=log_path,
                            message=f"端口 {spec.port} 已被其他程序占用（非 Streamlit 应用）", **base)
    if owned_pid is not None:
        return WebAppStatus(state=STATE_STARTING, pid=owned_pid, log_path=log_path,
                            message="进程已拉起，等待就绪", **base)
    return WebAppStatus(state=STATE_STOPPED, log_path=log_path, message="未运行", **base)


def wait_until_ready(name: str, root: Path, spec: WebAppSpec,
                     timeout: float = START_READY_TIMEOUT_SEC) -> WebAppStatus:
    deadline = time.monotonic() + timeout
    status = get_status(name, root, spec)
    while status.state == STATE_STARTING and time.monotonic() < deadline:
        time.sleep(0.5)
        status = get_status(name, root, spec)
    return status


# ── 启动 / 停止 ────────────────────────────────────────────────────────

def start(name: str, root: Path, spec: WebAppSpec) -> Tuple[bool, str]:
    if spec.is_remote:
        return False, "远程项目不能由看板启动，请在远程主机上启动。"
    status = get_status(name, root, spec)
    if status.state != STATE_STOPPED:
        return False, f"当前状态为 {status.label}，无需启动。"

    entry_path = (root / spec.entry)
    if spec.command is None and not entry_path.exists():
        return False, f"入口文件不存在：{spec.entry}"
    if spec.python and not Path(_resolve_python(root, spec)).exists():
        return False, f"指定的解释器不存在：{spec.python}"

    argv = build_command(root, spec)
    _run_dir(root).mkdir(parents=True, exist_ok=True)
    log = open(log_file(root), "ab")
    try:
        header = f"\n===== {datetime.now().isoformat(timespec='seconds')} start: {' '.join(argv)}\n"
        log.write(header.encode("utf-8"))
        log.flush()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        kwargs = dict(cwd=str(root), stdin=subprocess.DEVNULL, stdout=log,
                      stderr=subprocess.STDOUT, env=env)
        if _WINDOWS:
            kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = win_subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        log.close()
        return False, f"启动失败：{exc}"
    log.close()

    _write_pid_record(root, {
        "name": name,
        "pid": proc.pid,
        "port": spec.port,
        "command": argv,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    })
    return True, f"已拉起进程 PID {proc.pid}，等待就绪…"


def stop(name: str, root: Path, spec: WebAppSpec) -> Tuple[bool, str]:
    if spec.is_remote:
        return False, "远程项目不能由看板停止。"
    record = read_pid_record(root)
    if record is None or record.get("port") != spec.port or not pid_alive(record["pid"]):
        _remove_pid_record(root)
        status = get_status(name, root, spec)
        if status.state == STATE_RUNNING_EXTERNAL:
            return False, "该实例不是由看板启动的，请到启动它的终端中关闭。"
        return False, "没有由看板记录的运行进程。"

    pid = record["pid"]
    _terminate(pid)
    deadline = time.monotonic() + STOP_GRACE_SEC
    while time.monotonic() < deadline and pid_alive(pid):
        time.sleep(0.2)
    if pid_alive(pid):
        _kill(pid)
        time.sleep(0.5)
    _reap(pid)
    _remove_pid_record(root)
    return True, f"已停止 PID {pid}"


def process_cmdline(pid: int) -> str:
    """进程的完整命令行（取不到时返回空串）。用于确认端口上的进程确实是 Streamlit。"""
    if _WINDOWS:
        try:
            out = win_subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                text=True, timeout=15,
            )
            return out.strip()
        except Exception:
            return ""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        pass
    try:
        out = subprocess.check_output(["ps", "-o", "args=", "-p", str(pid)], text=True, timeout=10)
        return out.strip()
    except Exception:
        return ""


def find_listening_pid(port: int) -> Optional[int]:
    """找出监听本机指定端口的进程 PID；找不到或无法判断时返回 None。"""
    if _WINDOWS:
        try:
            out = win_subprocess.check_output(["netstat", "-ano", "-p", "TCP"], text=True, timeout=15)
        except Exception:
            return None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
                try:
                    return int(parts[4])
                except ValueError:
                    return None
        return None

    # Linux：/proc/net/tcp* 里找 LISTEN 状态（0A）的 inode，再到 /proc/<pid>/fd 里找对应 socket
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            local_port = int(fields[1].rsplit(":", 1)[1], 16)
            if local_port == port:
                inodes.add(fields[9])
    if inodes:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            fd_dir = proc / "fd"
            try:
                for fd in fd_dir.iterdir():
                    try:
                        target = os.readlink(fd)
                    except OSError:
                        continue
                    if target.startswith("socket:[") and target[8:-1] in inodes:
                        return int(proc.name)
            except OSError:
                continue
        return None

    # 其他平台（如 macOS）：lsof
    lsof = shutil.which("lsof")
    if lsof:
        try:
            out = subprocess.check_output([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                          text=True, timeout=15)
            first = out.split()
            return int(first[0]) if first else None
        except Exception:
            return None
    return None


def _wait_port_free(host: str, port: int, timeout: float = STOP_GRACE_SEC) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_listening(host, port):
            return True
        time.sleep(0.2)
    return not port_listening(host, port)


def stop_external(name: str, root: Path, spec: WebAppSpec) -> Tuple[bool, str]:
    """结束外部启动的 Streamlit 实例（不是看板启动的）。调用方必须先经过用户确认。

    安全约束：只结束「监听该端口」且「命令行里含 streamlit」的进程；否则拒绝，不做任何操作。
    """
    if spec.is_remote:
        return False, "远程项目不能由看板结束。"
    pid = find_listening_pid(spec.port)
    if pid is None:
        return False, f"没有找到监听端口 {spec.port} 的本机进程（或当前平台无法识别）。"
    cmd = process_cmdline(pid)
    if "streamlit" not in cmd.lower():
        return False, (f"端口 {spec.port} 上的进程（PID {pid}）不是 Streamlit，已拒绝结束。"
                       f"命令行：{cmd[:160] or '（无法读取）'}")
    _terminate(pid)
    if not _wait_port_free(probe_host(spec), spec.port):
        _kill(pid)
        _wait_port_free(probe_host(spec), spec.port, timeout=3.0)
    _reap(pid)
    if port_listening(probe_host(spec), spec.port):
        return False, f"已发送结束信号，但端口 {spec.port} 仍被占用（PID {pid}）。"
    return True, f"已结束外部启动的 Streamlit（PID {pid}）"


def restart(name: str, root: Path, spec: WebAppSpec) -> Tuple[bool, str]:
    """重启：先结束当前实例（看板启动的直接停；外部启动的需要调用方已确认），再拉起并等待就绪。

    远程项目不支持。返回 (是否就绪, 说明)。
    """
    if spec.is_remote:
        return False, "远程项目不能由看板重启。"
    status = get_status(name, root, spec)
    if status.state == STATE_RUNNING:
        ok, msg = stop(name, root, spec)
        if not ok:
            return False, f"停止失败：{msg}"
    elif status.state == STATE_RUNNING_EXTERNAL:
        ok, msg = stop_external(name, root, spec)
        if not ok:
            return False, f"结束外部进程失败：{msg}"
    elif status.state == STATE_STARTING:
        stop(name, root, spec)
    if not _wait_port_free(probe_host(spec), spec.port):
        return False, f"端口 {spec.port} 释放超时，无法重启。"
    ok, msg = start(name, root, spec)
    if not ok:
        return False, msg
    ready = wait_until_ready(name, root, spec)
    if ready.is_up:
        return True, f"已重启，{msg.split('，')[0]}，已就绪"
    return False, f"已拉起但未在超时内就绪，请查看日志。{msg}"


def _terminate(pid: int) -> None:
    if _WINDOWS:
        win_subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, timeout=15)
        return
    try:
        os.killpg(pid, signal.SIGTERM)  # 新会话组长，pgid == pid
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _kill(pid: int) -> None:
    if _WINDOWS:
        win_subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _reap(pid: int) -> None:
    """若该进程是本进程的子进程，回收僵尸，避免 pid_alive 误判。"""
    if _WINDOWS:
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


# ── 注册表读取（看板侧，不依赖 daemon） ───────────────────────────────

@dataclass
class WebAppEntry:
    name: str
    root: Path
    spec: Optional[WebAppSpec] = None
    error: Optional[str] = None


def list_web_app_entries(store_path: Optional[Path] = None) -> List[WebAppEntry]:
    """读取本机外部项目注册表，返回所有声明了 `web_app` 的项目（含加载失败的项目）。

    不经过 daemon：注册表是本地 JSON 文件，看板直接读取即可。
    """
    entries: List[WebAppEntry] = []
    for record in ExternalProjectRegistry(store_path).list():
        root = Path(record.path)
        try:
            manifest = load_manifest(root)
        except (ProjectManifestError, OSError) as exc:
            entries.append(WebAppEntry(name=record.name, root=root, error=str(exc)))
            continue
        if manifest.web_app is not None:
            entries.append(WebAppEntry(name=record.name, root=root, spec=manifest.web_app))
    return _mark_port_conflicts(entries)


def _mark_port_conflicts(entries: List[WebAppEntry]) -> List[WebAppEntry]:
    """同一端口被多个项目声明时，全部标记错误，要求使用者修改 project.yaml。"""
    by_port: Dict[Tuple[Optional[str], int], List[WebAppEntry]] = {}
    for e in entries:
        if e.spec is not None:
            by_port.setdefault((None if not e.spec.is_remote else e.spec.host, e.spec.port), []).append(e)
    for (_host, port), group in by_port.items():
        if len(group) > 1:
            names = "、".join(e.name for e in group)
            for e in group:
                e.error = f"端口 {port} 与其他项目冲突（{names}），请修改各自 project.yaml 的 web_app.port"
    return entries
