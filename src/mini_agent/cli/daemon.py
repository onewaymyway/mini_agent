"""
cli/daemon.py — Stage 9 守护进程管理

实现三条子命令：
  mini-agent daemon start [--http-port N] [--detach]
  mini-agent daemon stop
  mini-agent daemon status

以及 CLI 连接模式的客户端逻辑：
  - 当 daemon 已存在时，CLI 通过 HTTP API 连接，REPL 输入通过 InputQueue.enqueue() 提交
  - 复用现有 HTTP API（routes.py），CLI 也变成这个接口的一个使用者

设计原则（来自 stage9_plan.md 第三节）：
  - daemon 与 workdir 绑定（不是全局唯一 daemon）
  - PID 文件：<project_root>/.agent/daemon.pid
  - 默认行为：python -m mini_agent 时先检查 daemon，若存在则连接，若不存在则自动拉起（选项 A）
  - --no-daemon flag 回退到传统的进程内直接持有 Agent 行为
  - --prompt 单次模式不受影响
  - IPC 直接复用现有 HTTP API（不新增协议）
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


# ── PID 文件管理 ──────────────────────────────────────────────────────────────

def _pid_file(project_root: Path) -> Path:
    return project_root / ".agent" / "daemon.pid"


def _daemon_info_file(project_root: Path) -> Path:
    return project_root / ".agent" / "daemon_info.json"


def _write_pid(project_root: Path, pid: int, http_port: int) -> None:
    pid_path = _pid_file(project_root)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    info = {"pid": pid, "http_port": http_port, "started_at": time.time()}
    # 写 pid 文件
    pid_path.write_text(str(pid))
    # 写 info 文件（含端口）
    _daemon_info_file(project_root).write_text(
        json.dumps(info, indent=2)
    )


def _read_daemon_info(project_root: Path) -> Optional[dict]:
    """读取 daemon 信息。若 PID 文件不存在或进程已死，返回 None 并清理残留文件。"""
    pid_path = _pid_file(project_root)
    info_path = _daemon_info_file(project_root)

    if not pid_path.exists():
        return None

    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        _cleanup_pid_files(project_root)
        return None

    # 检查进程是否存活
    if not _is_process_alive(pid):
        # PID 文件残留，清理
        _cleanup_pid_files(project_root)
        return None

    # 读 info 文件获取端口
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text())
            return info
        except Exception:
            pass

    return {"pid": pid, "http_port": 8765, "started_at": 0.0}


def _cleanup_pid_files(project_root: Path) -> None:
    """清理 PID 和 info 文件（进程已死时调用）。"""
    for f in [_pid_file(project_root), _daemon_info_file(project_root)]:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def _is_process_alive(pid: int) -> bool:
    """检查进程是否存活（跨平台）。"""
    try:
        if sys.platform == "win32":
            import ctypes
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# ── HTTP 客户端：CLI 连接到已存在的 daemon ────────────────────────────────────

class DaemonClient:
    """
    CLI 连接模式的轻量 HTTP 客户端。
    把 REPL 用户输入通过 POST /v1/chat 提交，
    通过 GET /v1/stream SSE 接收流式输出。
    复用现有 HTTP API，不新增协议。
    """

    def __init__(self, http_port: int, token: Optional[str] = None) -> None:
        self.base_url = f"http://127.0.0.1:{http_port}"
        self.token = token
        self._session = None

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def health_check(self) -> bool:
        """检查 daemon HTTP 服务是否就绪。"""
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.base_url}/health",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_status(self) -> Optional[dict]:
        """获取 daemon 状态（/v1/status 端点）。"""
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.base_url}/v1/status",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    def send_message(self, message: str) -> Optional[str]:
        """提交一条用户消息，返回 turn_id。"""
        try:
            import urllib.request
            body = json.dumps({"message": message}).encode()
            req = urllib.request.Request(
                f"{self.base_url}/v1/chat",
                data=body,
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("turn_id")
        except Exception as e:
            print(f"[daemon-client] send_message failed: {e}", file=sys.stderr)
            return None

    def stream_output(self, turn_id: str, on_token=None, on_done=None) -> None:
        """
        订阅 SSE 流式输出直到该轮完成。
        on_token(text): 收到 token 时回调
        on_done(text): 轮结束时回调
        """
        try:
            import urllib.request
            url = f"{self.base_url}/v1/stream"
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=300) as resp:
                buffer = b""
                while True:
                    chunk = resp.read(1024)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n\n" in buffer:
                        msg_bytes, buffer = buffer.split(b"\n\n", 1)
                        msg = msg_bytes.decode("utf-8", errors="replace")
                        self._handle_sse_message(msg, turn_id, on_token, on_done)
        except Exception as e:
            if "timed out" not in str(e).lower():
                print(f"[daemon-client] stream error: {e}", file=sys.stderr)

    def _handle_sse_message(self, msg: str, turn_id: str, on_token, on_done) -> None:
        """解析单条 SSE 消息。"""
        data_line = None
        for line in msg.splitlines():
            if line.startswith("data:"):
                data_line = line[5:].strip()
        if not data_line:
            return
        try:
            event = json.loads(data_line)
        except Exception:
            return

        evt_type = event.get("type", "")
        evt_turn = event.get("turn_id", "")

        if evt_turn != turn_id:
            return  # 不是当前轮的事件

        if evt_type == "token" and on_token:
            on_token(event.get("data", {}).get("text", ""))
        elif evt_type == "turn_done" and on_done:
            on_done(event.get("data", {}).get("text", ""))


# ── daemon 子命令实现 ─────────────────────────────────────────────────────────

def cmd_daemon_start(
    project_root: Path,
    http_port: int = 8765,
    detach: bool = False,
    extra_argv: Optional[list] = None,
) -> int:
    """
    `mini-agent daemon start [--http-port N] [--detach]`

    不带 --detach：前台运行（Ctrl-C 停止）
    带 --detach：后台进程，写 PID 文件
    """
    # 检查是否已有存活的 daemon
    existing = _read_daemon_info(project_root)
    if existing:
        print(
            f"[daemon] Already running (PID={existing['pid']}, "
            f"port={existing['http_port']}). "
            f"Use 'mini-agent daemon stop' first."
        )
        return 1

    # 构建 daemon 启动命令：复用主入口，但带 --http --daemon-mode 标志
    python_exec = sys.executable
    base_cmd = [
        python_exec, "-m", "mini_agent",
        "--http", "--http-port", str(http_port),
        "--project", str(project_root),
        "--daemon-mode",  # 新标志：daemon 模式，不启动交互 REPL
    ]
    if extra_argv:
        base_cmd.extend(extra_argv)

    if not detach:
        # 前台运行：直接 exec
        print(f"[daemon] Starting in foreground on port {http_port}...")
        print(f"[daemon] Press Ctrl-C to stop.")
        try:
            os.execv(python_exec, base_cmd)
        except Exception as e:
            print(f"[daemon] Failed to exec: {e}", file=sys.stderr)
            return 1
    else:
        # 后台进程
        print(f"[daemon] Starting in background on port {http_port}...")
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform != "win32":
            kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(base_cmd, **kwargs)
        except Exception as e:
            print(f"[daemon] Failed to start: {e}", file=sys.stderr)
            return 1

        pid = proc.pid
        _write_pid(project_root, pid, http_port)
        print(f"[daemon] Started: PID={pid}, port={http_port}")
        print(f"[daemon] PID file: {_pid_file(project_root)}")

        # 等待 HTTP 服务就绪（最多 10 秒）
        client = DaemonClient(http_port)
        for _ in range(20):
            time.sleep(0.5)
            if client.health_check():
                print(f"[daemon] HTTP service ready at http://127.0.0.1:{http_port}")
                return 0

        print("[daemon] Warning: HTTP service did not respond within 10s, "
              "but daemon process is running.")
        return 0


def cmd_daemon_stop(project_root: Path) -> int:
    """
    `mini-agent daemon stop`
    向 daemon 进程发送 SIGTERM，等待其 graceful shutdown。
    """
    info = _read_daemon_info(project_root)
    if not info:
        print("[daemon] No running daemon found.")
        return 1

    pid = info["pid"]
    print(f"[daemon] Stopping daemon PID={pid}...")

    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.TerminateProcess(
                ctypes.windll.kernel32.OpenProcess(1, False, pid), 0
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("[daemon] Process already gone.")
        _cleanup_pid_files(project_root)
        return 0
    except Exception as e:
        print(f"[daemon] Error sending signal: {e}", file=sys.stderr)
        return 1

    # 等待进程退出（最多 10 秒）
    for _ in range(20):
        time.sleep(0.5)
        if not _is_process_alive(pid):
            _cleanup_pid_files(project_root)
            print("[daemon] Stopped.")
            return 0

    print("[daemon] Process did not exit in 10s, sending SIGKILL...")
    try:
        if sys.platform != "win32":
            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    _cleanup_pid_files(project_root)
    return 0


def cmd_daemon_status(project_root: Path) -> int:
    """
    `mini-agent daemon status`
    显示 daemon 存活状态 + autonomy_level + 上次 tick 时间 + 活跃任务数。
    """
    info = _read_daemon_info(project_root)
    if not info:
        print("[daemon] Not running.")
        return 1

    pid = info["pid"]
    port = info["http_port"]
    started = info.get("started_at", 0.0)
    uptime = time.time() - started if started else 0.0

    print(f"[daemon] Running: PID={pid}, HTTP port={port}")
    if uptime > 0:
        print(f"[daemon] Uptime: {_format_duration(uptime)}")

    # 尝试获取详细状态
    client = DaemonClient(port)
    status = client.get_status()
    if status:
        print(f"[daemon] Agent state: {status.get('state', 'unknown')}")
        print(f"[daemon] Queue depth: {status.get('queue_depth', 0)}")
        print(f"[daemon] Subscribers: {status.get('subscribers', 0)}")

        # 尝试从 self_profile 获取 autonomy_level
        autonomy = status.get("autonomy_level", "unknown")
        last_tick = status.get("last_autonomous_tick_at")
        print(f"[daemon] Autonomy level: {autonomy}")
        if last_tick:
            ago = time.time() - last_tick
            print(f"[daemon] Last autonomous tick: {_format_duration(ago)} ago")
    else:
        print("[daemon] (HTTP service not responding)")

    return 0


def _format_duration(seconds: float) -> str:
    """格式化秒数为可读字符串。"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    elif seconds < 86400:
        return f"{seconds/3600:.1f}h"
    else:
        return f"{seconds/86400:.1f}d"


# ── 主入口：处理 `mini-agent daemon` 子命令 ──────────────────────────────────

def run_daemon_cli(argv: list[str], project_root: Path) -> int:
    """
    处理 `mini-agent daemon <subcommand>` 的入口。
    返回退出码。
    """
    if not argv:
        print("Usage: mini-agent daemon <start|stop|status>")
        return 1

    subcmd = argv[0]
    rest = argv[1:]

    if subcmd == "start":
        import argparse
        p = argparse.ArgumentParser(prog="mini-agent daemon start")
        p.add_argument("--http-port", type=int, default=8765)
        p.add_argument("--detach", action="store_true",
                       help="Run in background (fork to daemon)")
        args = p.parse_args(rest)
        return cmd_daemon_start(
            project_root=project_root,
            http_port=args.http_port,
            detach=args.detach,
        )

    elif subcmd == "stop":
        return cmd_daemon_stop(project_root)

    elif subcmd == "status":
        return cmd_daemon_status(project_root)

    else:
        print(f"Unknown daemon subcommand: {subcmd!r}")
        print("Usage: mini-agent daemon <start|stop|status>")
        return 1


# ── CLI 连接模式：连接到已存在的 daemon ──────────────────────────────────────

def run_connected_repl(daemon_info: dict) -> None:
    """
    CLI 连接模式：连接到已存在的 daemon，REPL 输入通过 HTTP API 提交。
    与今天 Web 端的接入方式完全对称（都通过 POST /v1/chat + GET /v1/stream）。
    """
    import threading
    port = daemon_info["http_port"]
    pid = daemon_info["pid"]

    print(f"[daemon] Connected to running daemon (PID={pid}, port={port})")
    print("[daemon] Type your message, or 'exit' to disconnect (daemon keeps running)")
    print()

    client = DaemonClient(port)

    # 读取 API token（如果有）
    # 这里简化：不传 token（本地 127.0.0.1 通常不需要）

    try:
        while True:
            try:
                user_input = input("orzooo (connected) ❯ ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[daemon] Disconnected (daemon continues running)")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                print("[daemon] Disconnected (daemon continues running)")
                break

            # 提交消息
            turn_id = client.send_message(user_input)
            if not turn_id:
                print("[error] Failed to send message to daemon")
                continue

            # 流式接收输出
            done_event = threading.Event()
            response_parts = []

            def on_token(text):
                print(text, end="", flush=True)
                response_parts.append(text)

            def on_done(text):
                print()  # 换行
                done_event.set()

            # 在后台线程接收 SSE
            def stream_worker():
                client.stream_output(turn_id, on_token=on_token, on_done=on_done)
                done_event.set()  # 确保 done_event 被 set

            t = threading.Thread(target=stream_worker, daemon=True)
            t.start()

            # 等待完成（最多 5 分钟）
            done_event.wait(timeout=300)

    except KeyboardInterrupt:
        print("\n[daemon] Disconnected")


# ── 自动拉起 daemon（选项 A：默认行为）──────────────────────────────────────

def ensure_daemon_running(project_root: Path, http_port: int = 8765) -> Optional[dict]:
    """
    检查 daemon 是否存活，若不存在则自动拉起（选项 A，默认行为）。
    返回 daemon_info dict，或 None（拉起失败时）。
    """
    info = _read_daemon_info(project_root)
    if info:
        return info

    # 自动拉起
    print("[daemon] Auto-starting daemon in background...")
    result = cmd_daemon_start(
        project_root=project_root,
        http_port=http_port,
        detach=True,
    )
    if result != 0:
        print("[daemon] Failed to start daemon, falling back to direct mode")
        return None

    # 再次读取确认
    return _read_daemon_info(project_root)
