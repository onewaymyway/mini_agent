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


def _write_pid(
    project_root: Path,
    pid: int,
    http_port: int,
    agent_name: Optional[str] = None,
) -> None:
    pid_path = _pid_file(project_root)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    info: dict = {"pid": pid, "http_port": http_port, "started_at": time.time()}
    if agent_name:
        info["agent_name"] = agent_name
    # 写 pid 文件
    pid_path.write_text(str(pid))
    # 写 info 文件（含端口 + agent_name，CLI 连接时读取）
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
        订阅 /v1/stream/{turn_id} SSE 端点，直到该轮 turn_done 事件到来。

        使用 per-turn 端点（而非全局 /v1/stream）有两个好处：
          1. 服务端已做 turn_id 过滤，客户端无需自行过滤
          2. turn_done 后服务端不再推送该轮事件，便于客户端检测结束

        SSE 帧格式（来自 api/models.py AgentEvent.sse_format）：
            id: <int>
            event: <EventType>        # "token" / "turn_done" / "turn_start" / ...
            data: {"turn_id": "...", "text": "...", ...}
        """
        try:
            import urllib.request
            url = f"{self.base_url}/v1/stream/{turn_id}"
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=300) as resp:
                buffer = b""
                while True:
                    chunk = resp.read(1024)
                    if not chunk:
                        break
                    buffer += chunk
                    # SSE 帧以 \n\n 分隔
                    while b"\n\n" in buffer:
                        frame_bytes, buffer = buffer.split(b"\n\n", 1)
                        frame = frame_bytes.decode("utf-8", errors="replace")
                        done = self._handle_sse_frame(frame, on_token, on_done)
                        if done:
                            return  # turn_done 已触发，退出循环
        except Exception as e:
            err = str(e)
            if "timed out" not in err.lower() and "RemoteDisconnected" not in err:
                print(f"[daemon-client] stream error: {e}", file=sys.stderr)

    def _handle_sse_frame(self, frame: str, on_token, on_done) -> bool:
        """
        解析单条 SSE 帧，返回 True 表示该轮已结束（turn_done）。

        帧结构：
            id: <n>
            event: <type>
            data: <json>
        """
        evt_type = ""
        data_line = ""
        for line in frame.splitlines():
            if line.startswith("event:"):
                evt_type = line[6:].strip()
            elif line.startswith("data:"):
                data_line = line[5:].strip()

        if not data_line:
            return False
        try:
            payload = json.loads(data_line)
        except Exception:
            return False

        if evt_type == "token":
            # data: {"turn_id": "...", "text": "...", ...}
            text = payload.get("text", "")
            if text and on_token:
                on_token(text)
        elif evt_type == "turn_done":
            text = payload.get("text", "")
            if on_done:
                on_done(text)
            return True  # 通知调用方退出
        # 其他事件（turn_start / tool_call / info / replay_done 等）忽略

        return False


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

    流程：
      1. health_check 确认 HTTP 服务就绪
      2. get_status 获取 agent_name 用于显示提示符
      3. 每轮：POST /v1/chat → 拿 turn_id → GET /v1/stream/{turn_id} 流式接收
      4. turn_done 事件触发后回到输入等待
      5. exit/quit 或 Ctrl-C：断开连接，daemon 继续运行
    """
    import threading

    port = daemon_info["http_port"]
    pid  = daemon_info["pid"]
    client = DaemonClient(port)

    # ── 等待 HTTP 就绪（daemon 刚启动时可能有短暂延迟）─────────────────────
    print(f"[daemon] Connecting to daemon (PID={pid}, port={port})...", flush=True)
    for _attempt in range(10):
        if client.health_check():
            break
        time.sleep(0.5)
    else:
        print("[daemon] Error: daemon HTTP service not responding. "
              "Try 'mini-agent daemon status'.", file=sys.stderr)
        return

    # ── 获取提示符标签（StatusResponse 无 agent_name，用端口区分多 daemon）──
    # 未来可在 daemon_info.json 里写入 agent_name 后在此读取
    agent_name = daemon_info.get("agent_name") or f"daemon:{port}"

    print(f"[daemon] Connected  \u2713  (PID={pid}, port={port})")
    print("[daemon] 'exit' or Ctrl-C to disconnect \u2014 daemon keeps running\n",
          flush=True)

    prompt = f"{agent_name} (connected) ❯ "

    try:
        while True:
            # ── 读取用户输入 ───────────────────────────────────────────────
            try:
                user_input = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[daemon] Disconnected (daemon continues running)")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
                print("[daemon] Disconnected (daemon continues running)")
                break

            # ── 提交消息到 daemon ──────────────────────────────────────────
            turn_id = client.send_message(user_input)
            if not turn_id:
                print("[error] Failed to send message to daemon. "
                      "Is it still running? Try 'mini-agent daemon status'.",
                      file=sys.stderr)
                # 重新检查存活性
                if not client.health_check():
                    print("[daemon] Daemon appears to have stopped. Exiting.",
                          file=sys.stderr)
                    break
                continue

            # ── 流式接收该轮输出 ───────────────────────────────────────────
            done_event = threading.Event()
            printed_any = False

            def on_token(text, _ref={"printed": False}):
                nonlocal printed_any
                print(text, end="", flush=True)
                printed_any = True

            def on_done(_text):
                # token 已流式打印完毕，只需换行
                if printed_any:
                    print()
                done_event.set()

            def stream_worker(_tid=turn_id):
                try:
                    client.stream_output(_tid, on_token=on_token, on_done=on_done)
                except Exception as e:
                    print(f"\n[daemon-client] stream error: {e}", file=sys.stderr)
                finally:
                    done_event.set()  # 无论如何保证 done_event 被 set

            t = threading.Thread(target=stream_worker, daemon=True)
            t.start()

            # 等待该轮完成（最多 10 分钟）
            if not done_event.wait(timeout=600):
                print("\n[daemon] Timed out waiting for response.")

    except KeyboardInterrupt:
        print("\n[daemon] Disconnected (daemon continues running)")


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
