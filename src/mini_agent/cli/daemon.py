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
    info: dict = {
        "pid": pid,
        "http_port": http_port,
        "started_at": time.time(),
        "project_root": str(project_root),  # DaemonClient 用于读取 token
    }
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
            import ctypes.wintypes
            # PROCESS_QUERY_LIMITED_INFORMATION 是最小权限，足以查询退出码，
            # 且不需要 SeDebugPrivilege；SYNCHRONIZE 在某些场景下会被拒绝。
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.wintypes.DWORD()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                if not ok:
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
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

    _TOKEN_FILE = ".agent/agent_api.key"  # 与 api/auth.py 保持一致

    def __init__(
        self,
        http_port: int,
        token: Optional[str] = None,
        project_root: Optional[Path] = None,
    ) -> None:
        self.base_url = f"http://127.0.0.1:{http_port}"
        self._session = None
        # token 优先级：显式传入 > project_root/.agent/agent_api.key > cwd/.agent/agent_api.key
        if token:
            self.token = token
        else:
            roots = []
            if project_root:
                roots.append(Path(project_root))
            roots.append(Path.cwd())
            self.token = None
            for root in roots:
                key_path = root / self._TOKEN_FILE
                if key_path.exists():
                    try:
                        self.token = key_path.read_text(encoding="utf-8").strip() or None
                    except Exception:
                        pass
                    if self.token:
                        break

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
                f"{self.base_url}/v1/health",
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

    def list_sessions(self, limit: int = 10) -> list[dict]:
        """获取 daemon 的 session 列表（GET /v1/sessions）。"""
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.base_url}/v1/sessions?limit={limit}",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return data.get("sessions", [])
        except Exception:
            return []

    def resume_session(self, session_id: str) -> bool:
        """切换到指定 session（POST /v1/sessions/{id}/resume）。"""
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.base_url}/v1/sessions/{session_id}/resume",
                data=b"{}",
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("ok", False)
        except Exception:
            return False

    def new_session(self) -> Optional[str]:
        """让 daemon 开始新 session（POST /v1/sessions/new），返回新 session_id。"""
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.base_url}/v1/sessions/new",
                data=b"{}",
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("session_id") if data.get("ok") else None
        except Exception:
            return None

    def send_message(self, message: str, session_id: Optional[str] = None) -> Optional[str]:
        """提交一条用户消息，返回 turn_id。"""
        try:
            import urllib.request
            payload: dict = {"message": message}
            if session_id:
                payload["session_id"] = session_id
            body = json.dumps(payload).encode()
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

    def stream_output(self, turn_id: str, on_token=None, on_done=None, on_error=None) -> None:
        """
        订阅 /v1/stream/{turn_id} SSE 端点，直到该轮 turn_done 事件到来。

        使用 per-turn 端点（而非全局 /v1/stream）有两个好处：
          1. 服务端已做 turn_id 过滤，客户端无需自行过滤
          2. turn_done 后服务端不再推送该轮事件，便于客户端检测结束

        SSE 帧格式（来自 api/models.py AgentEvent.sse_format）：
            id: <int>
            event: <EventType>        # "token" / "turn_done" / "turn_start" / ...
            data: {"turn_id": "...", "text": "...", ...}

        注意：服务端在 run_turn() 抛异常时，会先发 error 事件，
        紧接着也会发 turn_done（text 为空，meta 里带 error），
        所以本方法保证总能在合理时间内返回（不再需要单靠 error 事件收尾）。

        关键 bug 修复（曾经导致"回复很短时，回复后不出现下一个 You ❯ 提示符"）：
        之前用 resp.read(1024) 按固定字节数读取。/v1/stream 端点是 chunked + keep-alive，
        服务端在该轮结束后仍会保持连接打开（等待下一轮或发心跳），并不会关闭连接。
        而 http.client 对分块编码响应的 read(amt) 语义是"必须攒够 amt 字节才返回"
        （见 http.client.HTTPResponse._read_chunked），不会因为"当前已有数据但不足 amt"
        就提前返回。一旦本轮所有 SSE 帧加起来的字节数小于 1024（很短的回复，比如本例），
        read(1024) 就会一直阻塞等待凑够 1024 字节，而服务端在 turn_done 之后没有更多
        实时数据可发，于是永远卡住——表现出来就是：回复完全没显示，或者显示了一部分就停在
        那里，对应的 You ❯ 也永远等不到。
        而 SSE 协议本身是逐行的（每个字段一行，空行分隔帧），改用 resp.readline() 按行读取，
        每收到一行就立刻返回，不需要凑够任何字节数，从根本上避免了这个问题。
        """
        try:
            import urllib.request
            url = f"{self.base_url}/v1/stream/{turn_id}"
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=300) as resp:
                frame_lines: list[bytes] = []
                while True:
                    line = resp.readline()
                    if not line:
                        break  # 连接被服务端关闭（EOF）
                    if line in (b"\n", b"\r\n"):
                        # 空行 = 一帧的结束
                        if frame_lines:
                            frame = b"".join(frame_lines).decode("utf-8", errors="replace")
                            frame_lines = []
                            done = self._handle_sse_frame(frame, on_token, on_done, on_error)
                            if done:
                                return  # turn_done 已触发，退出循环
                        continue
                    frame_lines.append(line)
        except Exception as e:
            err = str(e)
            if "timed out" not in err.lower() and "RemoteDisconnected" not in err:
                print(f"[daemon-client] stream error: {e}", file=sys.stderr)

    def _handle_sse_frame(self, frame: str, on_token, on_done, on_error=None) -> bool:
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
        elif evt_type == "error":
            # data: {"turn_id": "...", "message": "...", ...}
            # 不在这里 return True：服务端紧接着会发 turn_done 来正式收尾这一轮，
            # 这里只是让调用方能尽快看到错误提示，不必等到 turn_done 才知道出错了。
            if on_error:
                on_error(payload.get("message", ""))
        elif evt_type == "turn_done":
            text = payload.get("text", "")
            if on_done:
                on_done(text, payload.get("error"))
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
        if sys.platform == "win32":
            # Windows 没有 start_new_session；必须用 creationflags 让子进程
            # 脱离当前控制台会话，否则父进程（powershell 命令）退出时子进程
            # 会随控制台会话一起被杀死。
            # DETACHED_PROCESS(0x8) 让子进程无控制台；
            # CREATE_NEW_PROCESS_GROUP(0x200) 防止 Ctrl-C 信号传播。
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            }
        else:
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "close_fds": True,
                "start_new_session": True,
            }

        try:
            proc = subprocess.Popen(base_cmd, **kwargs)
        except Exception as e:
            print(f"[daemon] Failed to start: {e}", file=sys.stderr)
            return 1

        pid = proc.pid
        print(f"[daemon] Started: PID={pid}, port={http_port}")
        print(f"[daemon] PID file: {_pid_file(project_root)}")

        # PID 文件由 daemon 子进程自身在 --daemon-mode 路径里写入
        # （app.py 的 daemon-mode 段调用 _write_pid(os.getpid(), ...)）。
        # 父进程这里不再写文件，避免两处写入竞争以及子进程还未来得及
        # 写文件就被误判"已死"的时序问题。
        #
        # 不过需要等子进程写完 PID 文件后，health_check 才有意义；
        # 因此下面的等待循环里顺便也等 PID 文件出现。

        # 等待 HTTP 服务就绪（最多 15 秒）
        # 同时等 PID 文件（由子进程自己写）出现，再做 health_check。
        client = DaemonClient(http_port, project_root=project_root)
        pid_path = _pid_file(project_root)
        for i in range(30):
            time.sleep(0.5)
            # 子进程可能已崩溃
            if not _is_process_alive(pid):
                print(
                    f"[daemon] Error: daemon process (PID={pid}) exited unexpectedly.",
                    file=sys.stderr,
                )
                _cleanup_pid_files(project_root)
                return 1
            if pid_path.exists() and client.health_check():
                print(f"[daemon] HTTP service ready at http://127.0.0.1:{http_port}")
                return 0

        print(
            "[daemon] Warning: HTTP service did not respond within 15s, "
            "but daemon process is running."
        )
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
    client = DaemonClient(port, project_root=project_root)
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
        # health_check 失败时分两种情况
        if client.health_check():
            # /v1/health 通但 /v1/status 需要 token
            print("[daemon] HTTP service is up but /v1/status failed "
                  "(token may be missing or wrong)")
        else:
            print("[daemon] HTTP service not responding")
            print(f"         Expected: http://127.0.0.1:{port}/v1/health")
            print("         Tip: daemon may still be starting up. "
                  "Try again in a few seconds.")

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
        # allow_abbrev=False：默认的 argparse 前缀匹配会把独立的 --http 当成
        # --http-port 的缩写去匹配（这个子解析器只认识 --http-port，没有定义
        # --http 本身），导致 `daemon start --http --http-port 19100` 这种
        # 完全合理的组合被错误解析、报"--http-port: expected one argument"。
        # 这是实测中发现的另一个真实 bug——和上面 parse_known_args 的修复
        # 同一批改动里一起发现的，因为之前从来没人真的同时传过
        # --http 和 --http-port 给这个子命令（多用户模式之前，没人需要显式
        # 加 --http，因为 cmd_daemon_start 自己的 base_cmd 已经默认带了
        # --http；现在为了同时开 --http-multi-user，文档建议的标准用法是
        # 显式写全 --http --http-multi-user，组合起来才暴露了这个 abbrev 坑）。
        p = argparse.ArgumentParser(prog="mini-agent daemon start", allow_abbrev=False)
        p.add_argument("--http-port", type=int, default=8765)
        p.add_argument("--detach", action="store_true",
                       help="Run in background (fork to daemon)")
        # 修复另一个真实存在的 bug：之前这里用 parse_args(rest)，任何这个子
        # 解析器不认识的参数（比如多用户架构 Phase 1 加的 --http-multi-user）
        # 都会被 argparse 直接报错拒绝，而不是转发给实际启动 daemon 子进程的
        # 命令行（cmd_daemon_start 早就支持 extra_argv 参数，只是这里从来没
        # 传过）。也就是说，`mini-agent daemon start --http-multi-user` 这个
        # 本该是"开启多用户模式启动 daemon"的标准用法，实际上从加上这个 flag
        # 那天起就从未真正可用过——只能用更底层的
        # `python -m mini_agent --http --http-multi-user --daemon-mode` 绕开
        # `daemon start` 这层包装才能用上。改成 parse_known_args()，把所有
        # 认不出来的参数原样转发给 cmd_daemon_start 的 extra_argv，由它继续
        # 转发给真正的 daemon 子进程命令行。
        args, unknown = p.parse_known_args(rest)
        return cmd_daemon_start(
            project_root=project_root,
            http_port=args.http_port,
            detach=args.detach,
            extra_argv=unknown,
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

# ── Session 选择界面 ──────────────────────────────────────────────────────────

def _pick_session(client: "DaemonClient") -> Optional[str]:
    """
    展示 session 选择菜单，返回：
      - session_id str  → 用户选择已有 session（需 resume）
      - ""              → 用户选择新建 session
      - None            → 用户取消（q 或 Ctrl-C）

    如果 daemon 没有任何历史 session，静默返回 ""（直接新建）。
    默认回车选最近一条 session（编号 1）。
    """
    sessions = client.list_sessions(limit=8)
    if not sessions:
        return ""  # 没有历史，静默新建

    status = client.get_status() or {}
    current_sid = status.get("session_id", "")

    print()
    print("  \033[1m最近的 sessions\033[0m")
    print("  " + "─" * 54)
    for i, s in enumerate(sessions, 1):
        sid   = s.get("id", "")
        title = (s.get("title") or "(untitled)")[:36]
        turns = s.get("turns", 0)
        age   = s.get("age_str") or (s.get("updated_at") or "")[:16]
        mark  = " \033[32m● active\033[0m" if sid == current_sid else ""
        print(f"  \033[36m[{i}]\033[0m {title:<36} {turns:>3}轮  {age}{mark}")
    print("  " + "─" * 54)
    print("  \033[36m[n]\033[0m 新建 session    \033[36m[q]\033[0m 退出")
    print()

    while True:
        try:
            raw = input("  选择 [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if raw == "" or raw == "1":
            return sessions[0]["id"]
        if raw.lower() == "n":
            return ""
        if raw.lower() == "q":
            return None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]["id"]
        print(f"  \033[33m请输入 1-{len(sessions)}、n 或 q\033[0m")


def _connected_print(text: str) -> None:
    """
    连接模式专用输出：清除当前行后输出，避免和 status_bar 残留交织。
    在调用前 status_bar 应已停止，这里只是做防御性清行。
    """
    import sys
    # \r\033[K = 回到行首 + 清除到行尾，再输出文本
    sys.stdout.write("\r[K" + text)
    sys.stdout.flush()


def _connected_print_token(text: str) -> None:
    """流式 token 输出：不清行，直接追加（token 是连续的）。"""
    import sys
    sys.stdout.write(text)
    sys.stdout.flush()


def run_connected_repl(daemon_info: dict) -> None:
    """
    CLI 连接模式：连接到已存在的 daemon，REPL 输入通过 HTTP API 提交。

    流程：
      1. health_check 确认 HTTP 服务就绪
      2. 展示 session 选择界面（最近 N 条 + 新建选项）
      3. 根据选择 resume 或 new_session
      4. REPL 循环：每轮 POST /v1/chat → turn_id → SSE 流式接收
      5. 内置命令：/session list  /session new  /session  exit
      6. exit/Ctrl-C：断开，daemon 继续运行

    注意：调用前必须已停止 status_bar（stop_status_bar()），
    否则 _refresh_loop 会干扰 input() 提示符和 SSE 输出。
    """
    import threading

    port = daemon_info["http_port"]
    pid  = daemon_info["pid"]
    _proj = daemon_info.get("project_root")
    client = DaemonClient(port, project_root=_proj)


    # ── 等待 HTTP 就绪 ────────────────────────────────────────────────────────
    import sys as _sys
    _sys.stdout.write(f"[daemon] Connecting to daemon (PID={pid}, port={port})...\n")
    _sys.stdout.flush()
    for _attempt in range(10):
        if client.health_check():
            break
        time.sleep(0.5)
    else:
        _sys.stderr.write("[daemon] Error: HTTP service not responding. "
                          "Try: mini-agent daemon status\n")
        return

    agent_name = daemon_info.get("agent_name") or f"daemon:{port}"
    _sys.stdout.write(f"[daemon] Connected \u2713  (PID={pid}, port={port})\n")
    _sys.stdout.flush()

    # ── Session 选择 ──────────────────────────────────────────────────────────
    chosen_sid = _pick_session(client)
    if chosen_sid is None:
        _sys.stdout.write("[daemon] Exited (daemon continues running)\n")
        return

    active_session_id: Optional[str] = None
    if chosen_sid == "":
        new_sid = client.new_session()
        active_session_id = new_sid
        label = f"new session {new_sid}" if new_sid else "new session"
    else:
        ok = client.resume_session(chosen_sid)
        active_session_id = chosen_sid
        label = f"session {chosen_sid}" if ok else f"session {chosen_sid} (resume may have failed)"

    _connected_print(f"[daemon] \u2713 {label}\n")
    _connected_print("[daemon] '/session list' \u5207\u6362\uff0c'/session new' \u65b0\u5efa\uff0c'exit' \u65ad\u5f00\n\n")

    # Bug 修复：原来这里用 agent_name 作为提示符标签（如 "orzooo ❯ "），
    # 但 daemon 连接模式下，这一行提示符是给"用户输入"用的，
    # 应该和普通 REPL（ui/terminal.py 的 "You ❯ "）保持一致的角色标签，
    # 否则用户会困惑——看起来像是 agent 在等待输入，而不是自己要输入。
    prompt = "\033[1;32mYou\033[0m\033[1;36m \u276f \033[0m"

    # ── REPL 主循环 ───────────────────────────────────────────────────────────
    # 使用 sys.stdout.write + sys.stdin.readline 代替 input()，
    # 避免 status_bar 残留 / Rich 控制字符干扰输入提示符
    try:
        while True:
            _sys.stdout.write(prompt)
            _sys.stdout.flush()
            try:
                line = _sys.stdin.readline()
                if line == "":   # EOF
                    raise EOFError
                user_input = line.rstrip("\n").strip()
            except (EOFError, KeyboardInterrupt):
                _sys.stdout.write("\n")
                _connected_print("[daemon] Disconnected (daemon continues running)\n")
                break

            if not user_input:
                continue

            # ── 内置命令 ──────────────────────────────────────────────────────
            cmd = user_input.lower()

            if cmd in ("exit", "quit", "/exit", "/quit"):
                _connected_print("[daemon] Disconnected (daemon continues running)\n")
                break

            if cmd in ("/session new", "/new"):
                new_sid = client.new_session()
                if new_sid:
                    active_session_id = new_sid
                    _connected_print(f"[daemon] \u2713 New session: {new_sid}\n")
                else:
                    _connected_print("[daemon] \u2717 Failed to create new session\n")
                continue

            if cmd in ("/session list", "/sessions", "/session ls"):
                chosen = _pick_session(client)
                if chosen is None:
                    continue
                if chosen == "":
                    new_sid = client.new_session()
                    if new_sid:
                        active_session_id = new_sid
                        _connected_print(f"[daemon] \u2713 New session: {new_sid}\n")
                    else:
                        _connected_print("[daemon] \u2717 Failed to create new session\n")
                else:
                    ok = client.resume_session(chosen)
                    if ok:
                        active_session_id = chosen
                        _connected_print(f"[daemon] \u2713 Switched to: {chosen}\n")
                    else:
                        _connected_print(f"[daemon] \u2717 Failed to switch to {chosen}\n")
                continue

            if cmd == "/session":
                st = client.get_status() or {}
                cur = st.get("session_id") or active_session_id or "(unknown)"
                state = st.get("state", "?")
                _connected_print(f"[daemon] session={cur}  state={state}\n")
                _connected_print("         /session list  /session new\n")
                continue

            # ── 发送消息 ──────────────────────────────────────────────────────
            turn_id = client.send_message(user_input, session_id=active_session_id)
            if not turn_id:
                if not client.health_check():
                    _connected_print("[daemon] Daemon appears to have stopped. Exiting.\n")
                    break
                _connected_print("[error] send_message failed, please retry.\n")
                continue

            # ── 流式接收 ──────────────────────────────────────────────────────
            done_event = threading.Event()
            printed_any = False

            def on_token(text):
                nonlocal printed_any
                _connected_print_token(text)
                printed_any = True

            def on_error(message):
                # 服务端 run_turn() 出错时立刻提示，不必等 turn_done/超时才知道。
                _connected_print(f"\n[error] {message}\n")

            def on_done(_text, error=None):
                if printed_any:
                    _sys.stdout.write("\n")
                    _sys.stdout.flush()
                if error and not printed_any:
                    # 出错且没有任何 token 输出过（典型场景：LLM 调用直接抛异常），
                    # 这里再兜底提示一次，避免用户只看到空白就回到了输入提示符。
                    _connected_print(f"[error] {error}\n")
                done_event.set()

            def stream_worker(_tid=turn_id):
                try:
                    client.stream_output(
                        _tid, on_token=on_token, on_done=on_done, on_error=on_error
                    )
                except Exception as e:
                    _connected_print(f"\n[daemon-client] stream error: {e}\n")
                finally:
                    # 兜底：无论 stream_output 内部发生什么，都必须 set，
                    # 否则用户会一直卡在等待状态，看不到下一个 You ❯ 输入提示。
                    done_event.set()

            _sys.stdout.write("\n")  # token 开始前换行
            _sys.stdout.flush()
            threading.Thread(target=stream_worker, daemon=True).start()
            if not done_event.wait(timeout=600):
                _connected_print("\n[daemon] Timed out waiting for response.\n")
            _sys.stdout.write("\n")  # 输出后空行，与下一个 You ❯ 提示符分隔开
            _sys.stdout.flush()

    except KeyboardInterrupt:
        _sys.stdout.write("\n")
        _connected_print("[daemon] Disconnected (daemon continues running)\n")


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