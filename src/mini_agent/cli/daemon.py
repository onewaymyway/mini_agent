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

    def list_pending_permissions(self) -> list[dict]:
        """获取当前待审批的权限请求列表（GET /v1/permissions/pending）。

        主要用于 connected 模式刚连接/刚切换 session 时的"补报"——如果
        permission_req 事件在本客户端订阅 SSE 之前就已经广播过（比如
        另一个终端先连上、触发了工具调用，本终端是稍后才连接的），单靠
        SSE 是看不到这条历史事件的（除非走 replay，但 replay 只重放
        RingBuffer 里还没被后续事件挤出去的部分，且 permission_done 之前
        的 permission_req 状态需要专门处理，不能简单当成"重放一遍了之"）。
        直接查一次"现在还有哪些 pending"更可靠。
        """
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.base_url}/v1/permissions/pending",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return data.get("permissions", [])
        except Exception:
            return []

    def respond_permission(
        self,
        req_id: str,
        approve: bool,
        edited_input: Optional[dict] = None,
        mode: str = "once",
    ) -> bool:
        """
        提交权限审批决定（POST /v1/permissions/{req_id}）。

        mode: "once"（仅本次）| "always"（以后总是允许这个工具）|
              "deny_always"（以后总是拒绝这个工具）—— 对应
              PermissionGuard._prompt_with_http() 里 CLI 端 (a)/(d) 选项
              的语义，服务端 respond_permission() 路由目前只在 body 里
              接收这个字段（透传），真正的"写入白/黑名单"逻辑在
              PermissionGuard 内部（daemon 本地终端的 CLI 分支才会触发
              _add_allow()/_denied_tools——纯 HTTP 路径目前不持久化这个
              偏好，仅影响这一次请求是否通过，这点和 daemon 本地终端的
              CLI 交互不完全等价，已在 run_connected_repl 的审批提示里
              注明）。

        如果这个 req_id 已经被别的端（daemon 本地终端、另一个 CLI、
        web demo）先处理过，服务端会返回 404——这里转换成 False，
        调用方应该把它当成"已被别人处理，不需要重试"，不是真正的错误。
        """
        try:
            import urllib.request
            import urllib.error
            payload: dict = {"approve": approve, "mode": mode}
            if edited_input is not None:
                payload["edited_input"] = edited_input
            body = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{self.base_url}/v1/permissions/{req_id}",
                data=body,
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("ok", False)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False  # 已被别的端处理，不是真正的失败
            print(f"[daemon-client] respond_permission failed: {e}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[daemon-client] respond_permission failed: {e}", file=sys.stderr)
            return False

    # ── [具身改进 A1] Connected REPL 命令对等：cron / goals / digest ──────────
    # 复用现有 HTTP API（routes.py 已有 /v1/cron /v1/goals /v1/autonomous/status
    # /v1/self/status），这里只是补上 CLI 连接模式缺失的转发方法，
    # 不新增协议、不改动服务端。

    def _get_json(self, path: str, timeout: float = 5) -> Optional[dict]:
        """通用 GET 封装：失败时返回 None（不抛异常，调用方据此判断失败原因）。"""
        try:
            import urllib.request
            req = urllib.request.Request(f"{self.base_url}{path}", headers=self._headers())
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"[daemon-client] GET {path} failed: {e}", file=sys.stderr)
            return None

    def _post_json(self, path: str, payload: Optional[dict] = None, timeout: float = 10) -> Optional[dict]:
        """通用 POST 封装：失败时返回 None。"""
        try:
            import urllib.request
            body = json.dumps(payload or {}).encode()
            req = urllib.request.Request(
                f"{self.base_url}{path}", data=body, headers=self._headers(), method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"[daemon-client] POST {path} failed: {e}", file=sys.stderr)
            return None

    def list_cron_jobs(self) -> Optional[dict]:
        """GET /v1/cron/jobs — CronScheduler job 列表。"""
        return self._get_json("/v1/cron/jobs")

    def run_cron_job(self, job_id: str) -> Optional[dict]:
        """POST /v1/cron/jobs/{id}/run — 立即触发一次。"""
        return self._post_json(f"/v1/cron/jobs/{job_id}/run")

    def list_goals(self) -> Optional[dict]:
        """GET /v1/goals — GoalBacklog 完整视图（active goals + objectives）。"""
        return self._get_json("/v1/goals")

    def get_autonomous_status(self) -> Optional[dict]:
        """GET /v1/autonomous/status — 当前自主化档位 + cron/objective 进度。"""
        return self._get_json("/v1/autonomous/status")

    def get_digest(self) -> Optional[dict]:
        """
        Self 状态总览（晨报）。

        注：v3 改进计划草案设想的端点名是 /v1/digest，但服务端实际实现
        落在 /v1/self/status（GoalBacklog + 最近活动 + session_pool 概况，
        语义与"digest"一致，只是路由命名不同）——这里直接对接已有端点，
        不新增重复路由。
        """
        return self._get_json("/v1/self/status")

    def stream_output(
        self,
        turn_id: str,
        on_token=None,
        on_done=None,
        on_error=None,
        on_event=None,
    ) -> None:
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

        on_event（新增，可选）：统一回调，签名 on_event(evt_type: str, payload: dict)，
        用于接收 token/turn_done/error 之外的"只读展示类"事件——tool_call、
        tool_result、tool_error、info、warning、permission_req、permission_done、
        session_switched 等。引入这个回调之前，_handle_sse_frame 对这些事件类型
        一律静默忽略（见函数末尾原来的注释"其他事件……忽略"），这正是 connected
        模式 CLI 完全看不到工具调用过程的根因——协议层（AgentEvent/EventType）
        早就在推送这些事件，只是客户端从来没有读取/转发它们。
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
                            done = self._handle_sse_frame(
                                frame, on_token, on_done, on_error, on_event
                            )
                            if done:
                                return  # turn_done 已触发，退出循环
                        continue
                    frame_lines.append(line)
        except Exception as e:
            err = str(e)
            if "timed out" not in err.lower() and "RemoteDisconnected" not in err:
                print(f"[daemon-client] stream error: {e}", file=sys.stderr)

    def _handle_sse_frame(
        self, frame: str, on_token, on_done, on_error=None, on_event=None
    ) -> bool:
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
        elif evt_type in (
            "tool_call", "tool_result", "tool_error",
            "info", "warning", "permission_req", "permission_done",
            "session_switched", "fs_change",
        ):
            # 之前这里被静默忽略——是 connected 模式看不到工具调用过程的根因。
            # 统一转发给 on_event，由调用方决定怎么渲染（见 run_connected_repl
            # 里的 _render_sse_event，复用 ui/renderer.py 的图标/摘要样式）。
            if on_event:
                on_event(evt_type, payload)
        # 其他事件（turn_start / replay_done 等）忽略——这两类纯粹是协议层
        # 的簿记信息（标记一轮开始 / SSE 重放完毕），没有对应的可展示内容。

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

        # daemon 进程的 stdout/stderr 重定向到日志文件而非 DEVNULL，
        # 崩溃时可查看：<project_root>/.agent/daemon.log
        log_path = project_root / ".agent" / "daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w", encoding="utf-8", errors="replace")

        if sys.platform == "win32":
            # Windows 没有 start_new_session；必须用 creationflags 让子进程
            # 脱离当前控制台会话，否则父进程（powershell 命令）退出时子进程
            # 会随控制台会话一起被杀死。
            # DETACHED_PROCESS(0x8) 让子进程无控制台；
            # CREATE_NEW_PROCESS_GROUP(0x200) 防止 Ctrl-C 信号传播。
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs = {
                "stdout": log_file,
                "stderr": log_file,
                "creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            }
        else:
            kwargs = {
                "stdout": log_file,
                "stderr": log_file,
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
                log_file.flush()
                log_file.close()
                print(
                    f"[daemon] Error: daemon process (PID={pid}) exited unexpectedly.",
                    file=sys.stderr,
                )
                print(
                    f"[daemon] Check log for details: {log_path}",
                    file=sys.stderr,
                )
                # 打印日志末尾 30 行，方便直接看到错误
                try:
                    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    tail = lines[-30:] if len(lines) > 30 else lines
                    if tail:
                        print("[daemon] --- daemon.log tail ---", file=sys.stderr)
                        for l in tail:
                            print(f"  {l}", file=sys.stderr)
                        print("[daemon] --- end ---", file=sys.stderr)
                except Exception:
                    pass
                _cleanup_pid_files(project_root)
                return 1
            if pid_path.exists() and client.health_check():
                log_file.close()
                print(f"[daemon] HTTP service ready at http://127.0.0.1:{http_port}")
                print(f"[daemon] Log: {log_path}")
                return 0

        log_file.close()
        print(
            "[daemon] Warning: HTTP service did not respond within 15s, "
            "but daemon process is running."
        )
        print(f"[daemon] Log: {log_path}")
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

def _pick_session(
    client: "DaemonClient",
    term=None,
) -> Optional[str]:
    """
    展示 session 选择菜单，返回：
      - session_id str  → 用户选择已有 session（需 resume）
      - ""              → 用户选择新建 session
      - None            → 用户取消（q 或 Ctrl-C）

    如果 daemon 没有任何历史 session，静默返回 ""（直接新建）。
    默认回车选最近一条 session（编号 1）。

    term（Optional Terminal）：若传入，用 term.print()/term.prompt_user()
    输出菜单和读取选择，而不是裸 print()/input()。

    ★ 架构修复（彻底取代之前"手动 _enter_input_mode()/_exit_input_mode()
    配合重入检测"的方案）：之前的实现是在裸 print()/input() 外面手动
    包一层状态栏暂停/恢复，需要额外的重入检测逻辑来防止嵌套调用互相
    干扰（"/session list 命令执行期间结果提示又被状态栏打断"那个 bug）。
    这其实是在用 patch 的方式弥补"输出路径绕开了 Terminal 渲染队列"这个
    根本问题——只要还在用裸 print()/input()，类似的竞态以后只会在新的
    地方重新出现。

    现在改为直接用 term.print()（把内容交给渲染队列，由唯一的渲染线程
    串行处理，天然和状态栏刷新互斥，不需要任何手动暂停）和
    term.prompt_user()（内部自带 _enter_input_mode()/_exit_input_mode()
    配对调用，且是不可重入问题的——它和 print() 走的是同一个队列，
    调用顺序就是处理顺序，没有"两个独立调用方都想暂停"的并发问题）。
    term 为 None 时（极端情况下没拿到 Terminal 实例）才退回裸
    print()/input()，作为兜底。
    """
    sessions = client.list_sessions(limit=8)
    if not sessions:
        return ""  # 没有历史，静默新建

    status = client.get_status() or {}
    current_sid = status.get("session_id", "")

    def _out(line: str) -> None:
        if term is not None:
            term.print(line)
        else:
            print(line)

    # session 标题来自用户历史输入（"New session" 之类是默认值，但更常见
    # 的是用户第一句话的摘要），属于不可信的外部数据——必须用
    # rich.markup.escape() 转义后才能安全拼进 term.print() 的 markup
    # 字符串里。否则如果标题恰好包含方括号（比如用户问过 "[紧急] 帮我..."
    # 这种问题），会被 rich 误解析成未知标签，轻则显示异常、重则内容
    # 被吃掉（rich 对无法识别的标签是直接消费掉方括号内的文本，不是
    # 原样保留）。term.print() 走的是裸 print()/_out() 在 term=None 时
    # 没有这个问题（标准 print() 不解析 markup），但有 term 的主路径必须
    # 转义。
    from rich.markup import escape as _esc

    _out("")
    _out("  [bold]最近的 sessions[/bold]")
    _out("  " + "─" * 54)
    for i, s in enumerate(sessions, 1):
        sid   = s.get("id", "")
        title = _esc((s.get("title") or "(untitled)")[:36])
        turns = s.get("turns", 0)
        age   = _esc(s.get("age_str") or (s.get("updated_at") or "")[:16])
        mark  = " [green]● active[/green]" if sid == current_sid else ""
        _out(f"  [cyan][{i}][/cyan] {title:<36} {turns:>3}轮  {age}{mark}")
    _out("  " + "─" * 54)
    _out("  [cyan][n][/cyan] 新建 session    [cyan][q][/cyan] 退出")
    _out("")

    while True:
        try:
            if term is not None:
                raw = term.prompt_user("  选择 [1]: ").strip()
            else:
                raw = input("  选择 [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            _out("")
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
        _out(f"  [yellow]请输入 1-{len(sessions)}、n 或 q[/yellow]")


# 注：旧版的 _connected_print()/_connected_print_token() 已在本次重构中
# 移除——它们是裸 _sys.stdout.write() 包装，是之前两轮状态栏竞态 bug 的
# 根源（见 run_connected_repl 文档字符串"架构要点"第 1 点）。现在统一
# 通过 _out()（包装 term.print()）和 term.stream_token()/term.streaming()
# 输出，不再需要这两个函数。


def _connected_status_bar_provider(client: "DaemonClient") -> "list[str]":
    """
    connected 模式专用状态栏内容提供者。
    通过 GET /v1/status 轮询 daemon 状态，构建与本地 status_bar 风格一致的状态行。
    注册到 Terminal.set_statusbar_provider() 后由刷新线程定期调用。
    """
    try:
        status = client.get_status()
        if not status:
            return ["  \033[90m⚡ [connected] daemon 无响应\033[0m"]

        state = status.get("state", "unknown")
        session_id = status.get("session_id", "")
        sid_short = session_id[:8] if session_id else "?"
        queue_depth = status.get("queue_depth", 0)
        subscribers = status.get("subscribers", 0)

        # 状态图标
        state_icon = {
            "idle":    "\033[90m●\033[0m idle",
            "running": "\033[36m●\033[0m running",
            "waiting": "\033[33m●\033[0m waiting",
            "waiting_permission": "\033[31m●\033[0m waiting_permission",
        }.get(state, f"\033[90m●\033[0m {state}")

        lines = [
            f"  🌐 [connected] session={sid_short}  {state_icon}"
            f"  \033[90mqueue={queue_depth}  clients={subscribers}\033[0m"
        ]
        return lines
    except Exception:
        return ["  \033[90m⚡ [connected] status error\033[0m"]


def _render_sse_event(term, evt_type: str, payload: dict, *, prefix: str = "") -> None:
    """
    把"只读展示类"SSE 事件（tool_call / tool_result / tool_error / info /
    warning / session_switched / fs_change）渲染到 term，样式尽量贴近
    daemon 本地终端（ui/renderer.py 里 print_tool_call 等函数）的效果，
    这样无论从哪个端看，视觉体验基本一致。

    复用 renderer.py 的图标/摘要 helper（_tool_icon/_tool_summary/
    _result_lang），不重新发明一套展示逻辑，避免两边样式漂移。

    prefix: 旁观（observer）路径下传入一个来源标记（如 "[其他终端] "），
            拼在内容前面，区分"这是别的客户端触发的输出"；主路径
            （自己发起的 turn）不传，保持和本地模式一样干净的输出。

    注意：调用方负责判断"这条事件是否应该被渲染"（比如 observer 路径要
    检查 _waiting_input），本函数只管渲染，不做任何过滤判断。

    ★ rich markup 转义（重要）：payload 里几乎所有字段（tool_name 之外
    的 summary/message/result/path/title 等）都来自工具实际执行结果或
    用户输入，是不可信的外部数据。rich.console.Console.print() 默认会
    解析字符串里的 "[xxx]" 当作 markup 标签——如果不转义，一段恰好包含
    方括号的 bash 输出（很常见，比如很多 CLI 工具用 "[INFO]"/"[WARN]"
    这种前缀）会被当成未知标签，轻则显示错位，重则内容被直接吃掉
    （rich 对无法识别的标签是消费掉方括号本身，不是当文本保留）。
    这个问题同样存在于 ui/renderer.py（它在展示 tool_result 主体内容时
    用 Text() 包装规避了，但 tool_name/summary 等字段是直接拼接的，
    没有处理）——这里统一用 rich.markup.escape() 处理所有拼进 markup
    模板字符串的字段，比 renderer.py 现状更完整。
    """
    if term is None:
        return
    try:
        from mini_agent.ui import renderer as _r
    except Exception:
        _r = None

    from rich.markup import escape as _esc
    from rich.text import Text as _Text

    try:
        if evt_type == "tool_call":
            tool_name  = payload.get("tool_name", "")
            tool_input = payload.get("tool_input", {}) or {}
            icon    = _r._tool_icon(tool_name) if _r else "🔧"
            summary = _r._tool_summary(tool_name, tool_input) if _r else ""
            term.print(
                f"\n{prefix}{icon} [bold cyan]{_esc(tool_name)}[/bold cyan]  "
                f"[dim]{_esc(summary)}[/dim]"
            )

        elif evt_type == "tool_result":
            tool_name = payload.get("tool_name", "")
            result    = str(payload.get("result", ""))
            if not result or not result.strip():
                term.print(f"{prefix}  [dim](empty result)[/dim]")
            else:
                truncate = 2000
                display = result if len(result) <= truncate else result[:truncate] + "\n…[truncated]"
                lang = _r._result_lang(tool_name, result) if _r else None
                if lang:
                    # Syntax 对象本身就是安全的（不经过 markup 解析），
                    # 不需要 escape。
                    term.syntax(display, lang, theme="ansi_dark",
                                line_numbers=False, background_color="default")
                else:
                    # 用 Text 对象包装而不是直接传字符串给 print()——
                    # Text() 是 rich 提供的"已知安全"的纯文本载体，
                    # 不会解析其中的 "[xxx]"，这是规避 markup 注入风险
                    # 的正确方式（renderer.py 里展示 tool_result 时也是
                    # 这么做的，这里保持一致）。
                    term.print(_Text(display, style="dim"))

        elif evt_type == "tool_error":
            tool_name = payload.get("tool_name", "")
            message   = payload.get("message", "")
            term.print(f"{prefix}  [red]✗ {_esc(tool_name)} error:[/red] {_esc(message)}")

        elif evt_type == "info":
            term.print(f"{prefix}[blue]ℹ[/blue]  {_esc(payload.get('message', ''))}")

        elif evt_type == "warning":
            term.print(f"{prefix}[yellow]⚠[/yellow]  {_esc(payload.get('message', ''))}")

        elif evt_type == "session_switched":
            sid = payload.get("session_id", "")
            title = payload.get("title", "")
            term.print(f"{prefix}[dim]↳ session switched: {_esc(sid)} {_esc(title)}[/dim]")

        elif evt_type == "fs_change":
            action = payload.get("action", "")
            path   = payload.get("path", "")
            term.print(f"{prefix}[dim]📁 {_esc(action)}: {_esc(path)}[/dim]")
        # permission_req / permission_done 不在这里渲染——它们需要交互式
        # 审批流程（见 _handle_connected_permission），不是单纯的展示事件。
    except Exception:
        pass  # 渲染失败不应该打断主流程（比如某条事件字段缺失）


def _format_permission_summary(tool_name: str, tool_input: dict) -> str:
    """权限请求的摘要文案，复用 renderer.py 的 _tool_summary，没有就退化
    成简单的 repr。"""
    try:
        from mini_agent.ui import renderer as _r
        s = _r._tool_summary(tool_name, tool_input)
        if s:
            return s
    except Exception:
        pass
    try:
        return json.dumps(tool_input, ensure_ascii=False)[:120]
    except Exception:
        return str(tool_input)[:120]


def _is_dangerous_tool_guess(tool_name: str, tool_input: dict) -> bool:
    """
    HTTP 端事件里没有直接携带"是否危险"这个标记（PermissionGuard 内部的
    判断逻辑没有通过 AgentEvent 暴露出来），这里只能从 tool_name 做一个
    粗略猜测，仅用于审批提示的颜色/标签，不影响实际审批逻辑（真正的
    危险判定仍然只在服务端 PermissionGuard.check() 里做一次，HTTP 端
    永远只是把用户的 y/n 选择转发过去，不会绕过服务端判断）。
    """
    return tool_name in ("bash", "delete_file", "patch_file")


def _handle_connected_permission(
    client: "DaemonClient",
    term,
    req_id: str,
    tool_name: str,
    tool_input: dict,
    turn_id: str,
    *,
    prefix: str = "",
) -> None:
    """
    在 connected CLI 客户端上完整渲染一次权限审批交互，与 daemon 本地
    终端的 (y)/(a)/(n)/(d)/(s)/(w) 选项尽量保持一致的体验（具体实现见
    permissions.py::PermissionGuard._prompt_with_http，本函数是它在
    "纯 HTTP 客户端"侧的对应物）。

    这是"多端同步"设计的核心体现：审批请求本来就是通过 SSE 广播给
    *所有*订阅了这个 session 的客户端（daemon 本地终端、web demo、
    任意数量的 CLI 客户端）——谁先提交了决定，PermissionGuard 内部的
    竞速逻辑就采纳谁的（见 _prompt_with_http 的 _http_watcher 线程
    设计）。本函数只是让 CLI 客户端也能成为这个竞速里的一个参与者，
    不需要、也不应该尝试在客户端这一侧重新发明审批仲裁逻辑。

    由于本函数会阻塞在 term.confirm() 等待本端用户输入，调用方必须把它
    放在一个独立线程里调用（不能占住主 SSE 读取循环），见
    run_connected_repl 里 _watch_permission 的用法——这样即使本端用户
    迟迟不响应，也不会卡住其他事件（比如同一 turn 后续的 token）的接收；
    而如果别的端先决定了，本函数会通过 interrupt_event 提前结束等待。

    edit（编辑 bash 命令）功能这里不提供——daemon 本地终端的 (e)dit 选项
    依赖在本地终端重新读一段多行输入，HTTP 客户端要做到等价体验需要
    额外的多行输入 UI，暂不实现；本端用户如果需要编辑命令，可以选择
    (w)ait 转交给 daemon 本地终端或 web demo 处理。
    """
    if term is None:
        # 极端兜底：没有 Terminal 实例，没办法做交互式审批，直接放弃
        # （服务端会在超时后自动按 deny 处理，不会卡住整个流程）。
        return

    import threading as _threading

    permission_done_event = _threading.Event()
    decided_elsewhere = {"flag": False}

    def _watch_done():
        """后台轮询：如果这个 req_id 在我们等待期间已经被别的端处理掉了
        （list_pending_permissions 里不再包含它），设置 interrupt_event
        让本端的 confirm() 提前结束等待，不用傻等到超时。"""
        import time as _time
        while not permission_done_event.is_set():
            _time.sleep(0.5)
            pending = client.list_pending_permissions()
            if not any(p.get("req_id") == req_id for p in pending):
                decided_elsewhere["flag"] = True
                permission_done_event.set()
                return

    watcher = _threading.Thread(target=_watch_done, daemon=True)
    watcher.start()

    dangerous = _is_dangerous_tool_guess(tool_name, tool_input)
    label = "[bold red]⚠ DANGEROUS[/bold red]" if dangerous else "[yellow]Tool request[/yellow]"
    summary = _format_permission_summary(tool_name, tool_input)

    from rich.markup import escape as _esc

    term.print(f"\n{prefix}{label}: [bold]{_esc(tool_name)}[/bold]")
    term.print(f"{prefix}  [dim]{_esc(summary)}[/dim]")
    term.print(f"{prefix}  [dim](其他已连接的端也能审批这条请求，谁先响应就算谁的)[/dim]")

    if tool_name == "bash":
        choices = "(y)es  (a)lways  (n)o  (d)eny-always  (s)how  (w)ait"
    else:
        choices = "(y)es  (a)lways  (n)o  (d)eny-always  (s)how  (w)ait"

    # ★ 历史教训（已在 permissions.py 修复根因，这里仍保留双重保险）：
    # 这个类名曾经在 permissions.py 里从未被真正定义过（只在注释里提到），
    # ui/terminal.py::confirm() 每次命中中断分支时都会执行
    # "try: from mini_agent.permissions import X; except ImportError:
    # class X(Exception): pass"——这个 import 曾经总是失败，于是每次都
    # 动态生成一个全新的本地类；调用方如果也用同样模式 except 这个类型，
    # 捕获到的是自己生成的另一个类对象，跨模块精确匹配会失败。
    # 现在 permissions.py 里已经真正定义了这个类（见该文件
    # _InterruptedByHTTP 类的文档字符串），下面的 try/except ImportError
    # 会成功导入到真正同一个类，可以放心做精确捕获了；额外保留一层
    # except Exception 兜底（同 permissions.py::_prompt_with_http 的策略），
    # 防止任何未预见的其它异常类型也能被妥善处理，不会让整个审批交互
    # 流程崩溃。
    try:
        from mini_agent.permissions import _InterruptedByHTTP
    except ImportError:
        class _InterruptedByHTTP(Exception):
            pass

    while not permission_done_event.is_set():
        try:
            choice = term.confirm(
                prompt_lines=[],
                choices=choices,
                default="y",
                interrupt_event=permission_done_event,
            )
        except _InterruptedByHTTP:
            break
        except (KeyboardInterrupt, EOFError):
            choice = "n"
        except Exception:
            # 兜底：未预见的其它异常类型，同样通过 permission_done_event
            # 状态判断下一步，不强行区分异常类型。
            if permission_done_event.is_set():
                break
            choice = "n"

        if permission_done_event.is_set():
            break

        if choice in ("y", "yes"):
            ok = client.respond_permission(req_id, True, mode="once")
            permission_done_event.set()
            if ok:
                term.print(f"{prefix}  [green]→ approved[/green] (via this terminal)")
            break

        if choice in ("a", "always"):
            ok = client.respond_permission(req_id, True, mode="always")
            permission_done_event.set()
            if ok:
                term.print(f"{prefix}  [green]→ approved (always)[/green] (via this terminal)")
            break

        if choice in ("n", "no"):
            ok = client.respond_permission(req_id, False, mode="once")
            permission_done_event.set()
            if ok:
                term.print(f"{prefix}  [red]→ denied[/red] (via this terminal)")
            break

        if choice in ("d", "deny"):
            ok = client.respond_permission(req_id, False, mode="deny_always")
            permission_done_event.set()
            if ok:
                term.print(f"{prefix}  [red]→ denied (always)[/red] (via this terminal)")
            break

        if choice in ("s", "show"):
            try:
                term.syntax(json.dumps(tool_input, ensure_ascii=False, indent=2),
                            "json", theme="ansi_dark", line_numbers=False)
            except Exception:
                term.print(f"{prefix}  [dim]{_esc(repr(tool_input))}[/dim]")
            continue  # 显示完继续等待下一次选择，不算决定

        if choice in ("w", "wait"):
            term.print(f"{prefix}  [dim]⏳ 等待其他端审批…[/dim]")
            permission_done_event.wait(timeout=125.0)
            break

    watcher.join(timeout=1)
    if decided_elsewhere["flag"]:
        term.print(f"{prefix}  [dim](已被其他端处理)[/dim]")


def _fmt_ts(ts: Optional[float]) -> str:
    """把 epoch 秒格式化为可读时间，None/0 时返回占位符。"""
    if not ts:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return str(ts)


def _handle_connected_cron(client: "DaemonClient", user_input: str, _out) -> None:
    """
    [具身改进 A1] /cron 命令分发：
      /cron               等价于 /cron list
      /cron list          展示所有 cron job
      /cron run <job_id>  立即触发一次
    """
    parts = user_input.split()
    sub = parts[1].lower() if len(parts) > 1 else "list"

    if sub == "run":
        if len(parts) < 3:
            _out("[daemon] usage: /cron run <job_id>")
            return
        job_id = parts[2]
        result = client.run_cron_job(job_id)
        if result is None:
            _out(f"[daemon] \u2717 Failed to run job {job_id!r}（daemon 未响应或该 job 不存在）")
        else:
            _out(f"[daemon] \u2713 Triggered job {job_id!r}")
        return

    # 默认 / "list"：展示 job 列表
    data = client.list_cron_jobs()
    if data is None:
        _out("[daemon] \u2717 Failed to fetch cron jobs（daemon 未响应）")
        return
    jobs = data.get("jobs", [])
    if not jobs:
        note = data.get("note", "")
        _out(f"[daemon] (no cron jobs){'  ' + note if note else ''}")
        return
    _out(f"[daemon] Cron jobs ({len(jobs)}):")
    for j in jobs:
        flag = "on " if j.get("enabled") else "off"
        next_run = j.get("next_run_str") or _fmt_ts(j.get("next_run_at"))
        _out(
            f"  [{flag}] {j.get('id', '?')}  {j.get('name', '')}"
            f"  schedule={j.get('schedule', '')}  next_run={next_run}"
            f"  run_count={j.get('run_count', 0)}"
        )


def _handle_connected_goals(client: "DaemonClient", _out) -> None:
    """[具身改进 A1] /goals：展示 GoalBacklog 的 Goal/Objective 层级。"""
    data = client.list_goals()
    if data is None:
        _out("[daemon] \u2717 Failed to fetch goals（daemon 未响应）")
        return
    goals = data.get("goals", [])
    objectives = data.get("objectives", [])

    if not goals and not objectives:
        _out("[daemon] (no active goals/objectives)")
        return

    if goals:
        _out(f"[daemon] Active goals ({len(goals)}):")
        for g in goals:
            _out(
                f"  \u25cb {g.get('id', '?')}  [{g.get('status', '?')}]"
                f"  pri={g.get('priority', '-')}  {g.get('title', '')}"
            )
    if objectives:
        _out(f"[daemon] Active objectives ({len(objectives)}):")
        for o in objectives:
            note = o.get("progress_notes") or ""
            note_suffix = f"  — {note}" if note else ""
            _out(
                f"  \u25aa {o.get('id', '?')}  [{o.get('status', '?')}]"
                f"  {o.get('title', '')}{note_suffix}"
            )


def _handle_connected_digest(client: "DaemonClient", _out) -> None:
    """
    [具身改进 A1] /digest（晨报视图）：自主化档位 + 待办目标 + 最近活动摘要。
    对接 /v1/self/status + /v1/autonomous/status，两者合并展示。
    """
    auto = client.get_autonomous_status()
    digest = client.get_digest()

    if auto is None and digest is None:
        _out("[daemon] \u2717 Failed to fetch digest（daemon 未响应）")
        return

    if auto is not None:
        level = auto.get("autonomy_level", "unknown")
        next_tick = auto.get("next_tick_in")
        next_tick_str = f"{next_tick:.0f}s" if isinstance(next_tick, (int, float)) else "-"
        _out(f"[daemon] autonomy_level={level}  next_tick_in={next_tick_str}")
        cron_jobs = auto.get("cron_jobs") or []
        if cron_jobs:
            _out(f"         cron_jobs: {len(cron_jobs)} 个（详情见 /cron）")
        oe = auto.get("objective_executions")
        if oe:
            _out(f"         objective_executions: {oe}")

    if digest is not None:
        goals = digest.get("goals", {})
        active_goals = goals.get("active_goals", [])
        active_objectives = goals.get("active_objectives", [])
        _out(f"         active_goals={len(active_goals)}  active_objectives={len(active_objectives)}")

        recent = digest.get("recent_activity") or []
        if recent:
            _out(f"[daemon] Recent activity (last {min(len(recent), 5)} of {len(recent)}):")
            for rec in recent[-5:]:
                ts = _fmt_ts(rec.get("ts") or rec.get("timestamp"))
                kind = rec.get("type", "event")
                _out(f"  {ts}  {kind}: {str(rec)[:120]}")

        pool = digest.get("session_pool")
        if pool:
            _out(f"         session_pool: active={pool.get('active_count', 0)}")


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

    ★ 设计目标（本次重构）：connected 模式应该和"本地直跑模式"、web demo
    一样具有完整的交互能力——状态栏持续可见、能看到工具调用过程、能在
    本端完成权限审批，并且这一切在同一 session 的多个客户端之间（daemon
    本地终端、web demo、任意数量的 CLI 客户端）自动保持同步。

    架构要点：
      1. 【输出统一走 Terminal 渲染队列，不再裸写 stdout】
         之前的实现里，提示符/流式 token/结果提示全部用裸
         _sys.stdout.write() 写入，跟 Terminal 状态栏刷新线程（同样直接
         写 stdout，但在另一个线程里）之间没有任何协调，只能靠手动的
         _bar_pause()/_bar_resume() 打补丁式地暂停/恢复刷新线程——这正是
         之前两轮 bug（提示符被吃掉、回复内容被打断）的根源，并且代价
         是状态栏几乎全程被迫保持暂停，形同摆设。
         现在改用 term.print()/term.stream_token()/term.streaming()/
         term.prompt_user()——这些方法把内容交给 Terminal 唯一的渲染
         线程串行处理，每条消息处理时自动"擦状态栏→写内容→重绘状态栏"，
         天然互斥，不需要任何手动暂停/恢复。daemon 本地终端（终端 A）
         本来就是这样工作的，这里只是让 connected 模式接入同一套机制，
         而不是自己发明一套简化版。代价是这次改动比之前两次单纯打补丁
         大得多，但消除的是整整一类竞态，不是治标。

      2. 【工具调用 / 状态事件可见】
         AgentEvent/EventType 协议层早就支持 tool_call/tool_result/
         tool_error/info/warning/session_switched/fs_change 这些事件类型，
         api/server.py 的 _install_output_hook 也确实把 daemon 本地的
         渲染同步广播到了 SSE 流上——问题只在于 DaemonClient 客户端这边
         一直把它们静默丢弃（见 _handle_sse_frame 旧版注释"其他事件……
         忽略"）。现在 stream_output() 新增 on_event 回调转发这些事件，
         _render_sse_event() 复用 ui/renderer.py 的图标/摘要逻辑渲染，
         视觉效果与终端 A 基本一致。

      3. 【权限审批】
         permissions.py::PermissionGuard._prompt_with_http() 本来就设计
         成"daemon 本地终端 CLI 输入 + 任意 HTTP 客户端"两路竞速、谁先
         响应算谁的（HttpPermissionGate 内部用 threading.Event 配合
         req_id 仲裁）。connected 模式只需要成为这个竞速里的一个参与者：
         收到 permission_req 事件 → _handle_connected_permission() 渲染
         (y)/(a)/(n)/(d)/(s)/(w) 选项 → 本端用户选择后调用
         DaemonClient.respond_permission() 提交。如果别的端先决定了，
         本端的等待会被 interrupt_event 提前中断（轮询
         list_pending_permissions() 检测 req_id 是否还在），不会傻等
         超时。

      4. 【多端同步】
         这一点不需要任何新机制——SSE 广播（OutputBroadcaster）本来就是
         "推给所有订阅者"，每个 session 有自己独立的 bridge/ring/
         broadcaster，多用户架构下天然按 session 隔离。本函数的主路径
         订阅 /v1/stream/{turn_id}（只看自己发起的这个 turn），后台
         observer 线程订阅 /v1/stream（看同一 session 里所有其它事件）——
         两条路径合起来，不管事件是谁触发的、本端是不是正忙着自己的
         turn，都不会被错过。
         注意一个有意的简化：如果本端正在 streaming 自己的 turn 期间，
         observer 看到了"别的 turn"产生的 permission_req，这里只打印
         一行通知，不会打断当前的流式输出去抢占式地做交互式审批——
         这种"同一时刻两件事都要本端键盘输入"的场景留给其他端处理
         （daemon 本地终端、web demo 仍然是合法的审批入口），CLI 客户端
         不追求做到绝对无缝。

      5. 【向后兼容】
         /session list、/session new、/session、exit 等内置命令行为不变；
         _pick_session() 现在也改用 term.print()/term.prompt_user()，
         不再需要之前那套手动暂停/重入检测逻辑。
    """
    import threading

    port = daemon_info["http_port"]
    pid  = daemon_info["pid"]
    _proj = daemon_info.get("project_root")
    client = DaemonClient(port, project_root=_proj)

    # 提前拿到 term 实例——下面所有输出（包括"正在连接"这几行）统一走
    # term.print()，不再用裸 _sys.stdout.write。get_terminal() 是模块级
    # 单例，多次调用返回同一个实例，不会重复创建渲染线程。
    import sys as _sys
    _term = None
    try:
        from mini_agent.ui.terminal import get_terminal
        _term = get_terminal()
    except Exception:
        pass  # 极端兜底：拿不到就退回裸 print，下面 _out() 会处理

    def _out(line: str) -> None:
        """统一输出函数：有 term 就走渲染队列，没有就退回裸 print。"""
        if _term is not None:
            _term.print(line)
        else:
            print(line)

    # ── 等待 HTTP 就绪 ────────────────────────────────────────────────────────
    _out(f"[daemon] Connecting to daemon (PID={pid}, port={port})...")
    for _attempt in range(10):
        if client.health_check():
            break
        time.sleep(0.5)
    else:
        _sys.stderr.write("[daemon] Error: HTTP service not responding. "
                          "Try: mini-agent daemon status\n")
        return

    agent_name = daemon_info.get("agent_name") or f"daemon:{port}"
    _out(f"[daemon] Connected \u2713  (PID={pid}, port={port})")

    # ── 状态栏：注册 connected 模式专用 provider ─────────────────────────────
    # app.py 在检测到 daemon 存在后调用了 stop_status_bar()，这里重新启动，
    # 注册的是 _connected_status_bar_provider 而非本地 _build_lines。
    # 跟之前版本不同的是：现在不再需要任何手动暂停/恢复逻辑——只要本函数
    # 剩下的所有输出都通过 term.print()/term.stream_token()/
    # term.prompt_user() 走渲染队列，状态栏刷新自然就不会和它们冲突。
    if _term is not None:
        try:
            _term.set_statusbar_provider(lambda: _connected_status_bar_provider(client))
        except Exception:
            pass

    # ── Session 选择 ──────────────────────────────────────────────────────────
    chosen_sid = _pick_session(client, term=_term)
    if chosen_sid is None:
        _out("[daemon] Exited (daemon continues running)")
        # 退出前停止状态栏
        if _term is not None:
            try:
                _term.set_statusbar_provider(None)
            except Exception:
                pass
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

    _out(f"[daemon] \u2713 {label}")
    _out("[daemon] '/session list' \u5207\u6362\uff0c'/session new' \u65b0\u5efa\uff0c'/cron' '/goals' '/digest' \u67e5\u770b\u81ea\u4e3b\u4efb\u52a1\uff0c'exit' \u65ad\u5f00")
    _out("")

    # agent 回复前缀，格式与 daemon 本身终端 A 一致（ui/renderer.py 里
    # print_assistant_prefix 的等价物，这里手动拼是因为需要拿到 agent_name
    # 这个 connected 模式特有的变量）。agent_name 来自 daemon 启动配置
    # （daemon_info["agent_name"]），相对可信但仍统一转义一次，防止用户
    # 自定义了一个包含方括号的 agent 名字时被 rich 误解析。
    from rich.markup import escape as _esc_agent_name
    agent_name_escaped = _esc_agent_name(agent_name)
    agent_prefix_markup = f"\n[bold yellow]{agent_name_escaped}[/bold yellow][bold cyan] \u276f [/bold cyan]"

    # ── 多终端同步：后台 observer 线程 ───────────────────────────────────────
    # 持续订阅全局 /v1/stream（非 per-turn 端点；按 session 隔离，"全局"指
    # 的是"这个 session 里所有 turn"，不是跨 session）。本终端等待用户输入
    # 时，若同一 session 的其他终端/web demo 触发了新的 turn，把输出实时
    # 打印到本终端；权限请求则不受这个限制，随时都可能需要响应。
    #
    # 状态共享（线程安全通过 threading.Event/Lock 保证）：
    #   _waiting_input    - True 表示当前正在阻塞等待用户输入（可打印旁观输出）
    #   _my_turn_id       - 本终端当前正在处理的 turn_id（避免旁观自己的回复）
    #   _observer_lock    - 保证旁观输出与主输出不交错
    _waiting_input   = threading.Event()
    _waiting_input.set()   # 初始就是在等输入
    _my_turn_id_holder: list[Optional[str]] = [None]   # 用列表做可变容器
    # 本端自己 turn 的"当前是否正处于流式输出中间"标记，改成共享容器
    # （而不是每轮 turn 里的局部变量），这样 observer 线程在实时插入其他
    # 端的输出之前，可以安全地把本端未完成的这一行收尾（stream_end()），
    # 并把标记复位——本端 on_token 下一次收到新 token 时会发现标记是
    # False，自动重新打印一次前缀，相当于"另起一行接着流"，不会和
    # 其他端的内容混在同一行里。
    _own_printed_any_holder: list[bool] = [False]
    _observer_lock   = threading.Lock()
    _observer_stop   = threading.Event()
    # 记录本端正在处理的权限请求 req_id 集合，避免主路径和 observer 路径
    # 同时对同一个 req_id 弹出两次审批交互（主路径走 per-turn stream 时，
    # 如果碰巧也命中了自己发起的 turn 的 permission_req，应该只处理一次）。
    _active_permission_reqs: set = set()
    _active_permission_lock = threading.Lock()

    def _claim_permission_req(req_id: str) -> bool:
        """原子地"认领"一个 req_id，返回 True 表示认领成功（之前没人在处理），
        False 表示已经有人在处理了（不要重复弹出交互）。"""
        with _active_permission_lock:
            if req_id in _active_permission_reqs:
                return False
            _active_permission_reqs.add(req_id)
            return True

    def _release_permission_req(req_id: str) -> None:
        with _active_permission_lock:
            _active_permission_reqs.discard(req_id)

    def _observer_worker() -> None:
        """
        后台线程：订阅 /v1/stream SSE，实时把同一 session 里其他终端/
        web demo 触发的输出打印到本终端；权限请求随时处理。
        断线后自动重连（sleep 2s）。

        两个关键点（修复"新客户端看不到历史"和"事件跨 session 混在
        一起、时序错乱"两个问题）：
          1. 带 session_id 参数订阅，让服务端 (_sse_generator) 只推送
             "当前这个 session" 的事件——而不是 daemon 进程启动以来
             所有 session 混在一起的全部历史。
          2. 记录每个 session 收到过的最大 event id (_last_event_id)，
             重连时带上 since_id 续接，不再每次重连都把整个历史重放
             一遍（那是之前"内容重复、时序错乱"的根源）。只有真正切换
             到了一个不同的 session 时，才把 since_id 归零，从头回放
             这个 session 的完整历史——这正是"后连的客户端也能看到之前
             的历史记录"这个需求要的效果。
        """
        import urllib.request as _ureq
        from urllib.parse import quote as _urlquote

        # 每个 turn 是否已打印过前缀（避免多个 token 事件里重复打前缀）
        _turn_prefix_printed: set = set()
        _last_event_id = [0]
        _last_seen_session: list[Optional[str]] = [object()]  # 哨兵，保证首次必然判定为"变了"

        def _parse_event_id(frame: str) -> Optional[int]:
            for ln in frame.splitlines():
                if ln.startswith("id:"):
                    try:
                        return int(ln[3:].strip())
                    except ValueError:
                        return None
            return None

        while not _observer_stop.is_set():
            try:
                cur_sid = active_session_id
                if cur_sid != _last_seen_session[0]:
                    # 切换到了另一个 session（或首次连接）：从头回放这个
                    # session 的完整历史，让本端能看到"之前的历史记录"。
                    _last_event_id[0] = 0
                    _last_seen_session[0] = cur_sid
                    _turn_prefix_printed.clear()

                url = f"{client.base_url}/v1/stream?since_id={_last_event_id[0]}"
                if cur_sid:
                    url += f"&session_id={_urlquote(cur_sid)}"
                req = _ureq.Request(url, headers=client._headers())
                with _ureq.urlopen(req, timeout=300) as resp:
                    frame_lines: list[bytes] = []
                    while not _observer_stop.is_set():
                        if active_session_id != _last_seen_session[0]:
                            # 用户在别的地方（主循环）切换了 session：主动
                            # 断开重连，外层 while 会用新 session_id 重新
                            # 订阅。不强行打断 readline()——最多等到下一次
                            # 心跳（服务端每 20s 发一次）或下一条事件时
                            # 自然退出这层循环，不需要额外的信号量。
                            break
                        line = resp.readline()
                        if not line:
                            break  # 服务端关闭连接，退出重连
                        if line in (b"\n", b"\r\n"):
                            if frame_lines:
                                frame = b"".join(frame_lines).decode("utf-8", errors="replace")
                                frame_lines = []
                                eid = _parse_event_id(frame)
                                if eid is not None:
                                    _last_event_id[0] = eid
                                _handle_observer_frame(frame, _turn_prefix_printed)
                            continue
                        frame_lines.append(line)
            except Exception:
                pass
            if not _observer_stop.is_set():
                time.sleep(2)  # 断线后等待重连

    def _handle_observer_frame(frame: str, turn_prefix_printed: set) -> None:
        """
        解析单帧 SSE，决定是否输出/响应到本终端。

        过滤规则：
          - permission_req / permission_done：始终处理（不受 turn_id 归属
            或 _waiting_input 限制）——权限审批的设计就是"任意已连接的端
            都能响应"，哪怕本端正忙着输入自己的下一条消息，也应该能看到
            有请求在等待（即使为了不打断本端当前的交互而选择不强行弹出
            confirm()，至少应该有一行提示）。
          - 其它事件类型（token/tool_call/tool_result/...）：只有
            "不是本端自己发起的 turn" 且 "本端正在等待用户输入" 才显示——
            自己的 turn 走主路径的 per-turn stream 就够了，不需要重复；
            本端正忙着自己的 turn 时，旁观输出和自己的输出交织在一起会
            造成更大的困惑，不如不显示（这点和之前的设计保持一致）。
        """
        evt_type = ""
        data_line = ""
        for ln in frame.splitlines():
            if ln.startswith("event:"):
                evt_type = ln[6:].strip()
            elif ln.startswith("data:"):
                data_line = ln[5:].strip()

        if not data_line:
            return
        try:
            payload = json.loads(data_line)
        except Exception:
            return

        turn_id = payload.get("turn_id", "")
        my_tid = _my_turn_id_holder[0]
        is_own_turn = bool(turn_id) and turn_id == my_tid

        # ── 权限请求：始终处理，不受 turn_id 归属或 _waiting_input 限制 ────
        if evt_type == "permission_req":
            req_id     = payload.get("req_id", "")
            tool_name  = payload.get("tool_name", "")
            tool_input = payload.get("tool_input", {}) or {}
            if not req_id or not _claim_permission_req(req_id):
                return
            if is_own_turn or _waiting_input.is_set():
                # 本端空闲，或者这正好是本端自己 turn 的权限请求（主路径
                # 也会处理，但 _claim_permission_req 保证只有一边真正弹出
                # 交互）：正常弹出交互式审批。
                try:
                    with _observer_lock:
                        _handle_connected_permission(
                            client, _term, req_id, tool_name, tool_input, turn_id,
                            prefix="" if is_own_turn else "[dim][其他终端][/dim] ",
                        )
                finally:
                    _release_permission_req(req_id)
            else:
                # 本端正忙着 streaming 自己的 turn：不抢占式弹出 confirm()
                # （会和当前正在写的流式输出冲突），只打印一行通知。
                if _term is not None:
                    try:
                        from rich.markup import escape as _esc_local
                        with _observer_lock:
                            _term.print(
                                f"[dim][其他终端] 有权限请求待审批: {_esc_local(tool_name)} "
                                f"(req_id={_esc_local(req_id[:8])})，可在当前任务结束后用 "
                                f"/session 查看，或在 daemon 本地终端/web 端处理[/dim]"
                            )
                    except Exception:
                        pass
                _release_permission_req(req_id)
            return

        if evt_type == "permission_done":
            # 没有特别需要展示的内容（_handle_connected_permission 自己
            # 会在决定时打印结果）；这里只是确保 claim 状态被释放，防止
            # 极端情况下（比如本端从未真正处理过这个 req_id）泄漏。
            req_id = payload.get("req_id", "")
            if req_id:
                _release_permission_req(req_id)
            return

        # ── 其它事件：本端自己的 turn 不重复处理（主路径已经在显示了），──────
        # 除此之外一律实时显示——哪怕本端正忙着自己的 turn、正在等待用户
        # 输入，或者根本没有在做任何事，都应该立刻看到同一个 session 里
        # 其他端（其它命令行 / web demo）的输入和输出，这才是"daemon 里
        # 看到的那个样子"。不再有 _waiting_input 这个门槛——以前那个门槛
        # 会导致本端忙碌时直接丢弃其他端的事件，不是"延迟显示"而是
        # "永久错过"，是真正的 bug。
        if is_own_turn:
            return

        prefix = "[dim][其他终端][/dim] "

        with _observer_lock:
            # 如果本端自己的 turn 正流式输出到一半，先把这一行收尾，
            # 再插入其他端的内容，避免两路文本交织到同一行里。收尾后
            # 把共享标记复位，本端 on_token 下次收到新 token 时会发现
            # 需要重新打印一次前缀，相当于"换行后接着流"，内容本身不丢。
            if _own_printed_any_holder[0] and _term is not None:
                _term.stream_end()
                _own_printed_any_holder[0] = False

            if evt_type == "token":
                text = payload.get("text", "")
                if not text or _term is None:
                    return
                if turn_id not in turn_prefix_printed:
                    turn_prefix_printed.add(turn_id)
                    _term.print(f"\n{prefix}[bold yellow]{agent_name_escaped}[/bold yellow][bold cyan] \u276f [/bold cyan]", end="")
                _term.stream_token(text)

            elif evt_type == "turn_done":
                if turn_id in turn_prefix_printed and _term is not None:
                    _term.stream_end()
                turn_prefix_printed.discard(turn_id)

            elif evt_type in ("tool_call", "tool_result", "tool_error", "info", "warning",
                               "session_switched", "fs_change"):
                _render_sse_event(_term, evt_type, payload, prefix=prefix)

    # 启动 observer 线程
    _obs_thread = threading.Thread(target=_observer_worker, daemon=True, name="daemon-observer")
    _obs_thread.start()


    # ── REPL 主循环 ───────────────────────────────────────────────────────────
    #
    # 不再需要任何 _bar_pause()/_bar_resume() 手动管理——term.prompt_user()
    # 内部自带 _enter_input_mode()/_exit_input_mode() 配对调用，
    # term.print()/term.streaming() 都走渲染队列，状态栏刷新和这些输出
    # 之间的互斥由 Terminal 自己保证。这是本次重构想要达成的核心简化：
    # 之前两轮 bug 修复本质上都是在给"裸写 stdout"这个根本问题打补丁，
    # 现在从根上换掉了这个做法，那两类竞态不会再出现，不需要再靠手动
    # 维护"暂停范围对不对、有没有重入"这类细节。
    try:
        while True:
            _waiting_input.set()    # 标记：正在等用户输入（允许 observer 打印）

            try:
                user_input = (_term.prompt_user() if _term is not None
                              else input("\nYou \u276f ")).strip()
            except (EOFError, KeyboardInterrupt):
                _out("[daemon] Disconnected (daemon continues running)")
                break

            if not user_input:
                continue

            # ── 内置命令 ──────────────────────────────────────────────────────
            cmd = user_input.lower()

            if cmd in ("exit", "quit", "/exit", "/quit"):
                _out("[daemon] Disconnected (daemon continues running)")
                break

            if cmd in ("/session new", "/new"):
                new_sid = client.new_session()
                if new_sid:
                    active_session_id = new_sid
                    _out(f"[daemon] \u2713 New session: {new_sid}")
                else:
                    _out("[daemon] \u2717 Failed to create new session")
                continue

            if cmd in ("/session list", "/sessions", "/session ls"):
                chosen = _pick_session(client, term=_term)
                if chosen is None:
                    continue
                if chosen == "":
                    new_sid = client.new_session()
                    if new_sid:
                        active_session_id = new_sid
                        _out(f"[daemon] \u2713 New session: {new_sid}")
                    else:
                        _out("[daemon] \u2717 Failed to create new session")
                else:
                    ok = client.resume_session(chosen)
                    if ok:
                        active_session_id = chosen
                        _out(f"[daemon] \u2713 Switched to: {chosen}")
                    else:
                        _out(f"[daemon] \u2717 Failed to switch to {chosen}")
                continue

            if cmd == "/session":
                st = client.get_status() or {}
                cur = st.get("session_id") or active_session_id or "(unknown)"
                state = st.get("state", "?")
                _out(f"[daemon] session={cur}  state={state}")
                _out("         /session list  /session new")
                continue

            # ── [具身改进 A1] /cron /goals /digest：connected REPL 命令对等 ──
            if cmd.startswith("/cron"):
                _handle_connected_cron(client, user_input, _out)
                continue

            if cmd.startswith("/goals"):
                _handle_connected_goals(client, _out)
                continue

            if cmd in ("/digest", "/autonomous", "/autonomous status"):
                _handle_connected_digest(client, _out)
                continue

            # ── 发送消息 ──────────────────────────────────────────────────────
            turn_id = client.send_message(user_input, session_id=active_session_id)
            if not turn_id:
                if not client.health_check():
                    _out("[daemon] Daemon appears to have stopped. Exiting.")
                    break
                _out("[error] send_message failed, please retry.")
                continue

            # 标记：本终端正在处理这个 turn，observer 不要重复打印
            _waiting_input.clear()
            _my_turn_id_holder[0] = turn_id

            # ── 流式接收：token / tool_call / tool_result / permission_req ───
            done_event = threading.Event()
            # 复用共享容器 _own_printed_any_holder（而不是本轮局部变量）：
            # observer 线程需要能在其他端有新内容插入时，安全地把本端这
            # 一行收尾并复位这个标记，见 _handle_observer_frame 里的说明。
            _own_printed_any_holder[0] = False

            def on_token(text, _pa=None):
                if _term is None:
                    return
                with _observer_lock:  # 与 observer 互斥，避免输出交错
                    if not _own_printed_any_holder[0]:
                        _term.print(agent_prefix_markup, end="")
                    _term.stream_token(text)
                    _own_printed_any_holder[0] = True

            def on_error(message):
                if _term is not None:
                    from rich.markup import escape as _esc_err
                    _term.print(f"\n[red][error][/red] {_esc_err(str(message))}")

            def on_done(_text, error=None):
                with _observer_lock:
                    if _own_printed_any_holder[0] and _term is not None:
                        _term.stream_end()
                        _own_printed_any_holder[0] = False
                    if error and _term is not None:
                        from rich.markup import escape as _esc_err
                        _term.print(f"[red][error][/red] {_esc_err(str(error))}")
                done_event.set()

            def on_event(evt_type, payload, _tid=turn_id):
                """处理本端自己发起的 turn 里出现的非 token 事件——
                工具调用过程、权限请求等。"""
                if evt_type == "permission_req":
                    req_id     = payload.get("req_id", "")
                    tool_name  = payload.get("tool_name", "")
                    tool_input = payload.get("tool_input", {}) or {}
                    if not req_id or not _claim_permission_req(req_id):
                        return
                    try:
                        with _observer_lock:
                            # 权限请求出现在 token 流之间——如果正好在
                            # streaming 一段文本中间，先收尾当前这段，
                            # 避免审批提示和未完成的文本混在一行。
                            if _own_printed_any_holder[0] and _term is not None:
                                _term.stream_end()
                                _own_printed_any_holder[0] = False
                            _handle_connected_permission(
                                client, _term, req_id, tool_name, tool_input, _tid,
                            )
                    finally:
                        _release_permission_req(req_id)
                    return

                if evt_type == "permission_done":
                    req_id = payload.get("req_id", "")
                    if req_id:
                        _release_permission_req(req_id)
                    return

                # tool_call / tool_result / tool_error / info / warning / ...
                with _observer_lock:
                    if _own_printed_any_holder[0] and _term is not None:
                        _term.stream_end()
                        _own_printed_any_holder[0] = False
                    _render_sse_event(_term, evt_type, payload)

            def stream_worker(_tid=turn_id):
                try:
                    client.stream_output(
                        _tid, on_token=on_token, on_done=on_done,
                        on_error=on_error, on_event=on_event,
                    )
                except Exception as e:
                    if _term is not None:
                        from rich.markup import escape as _esc_err
                        _term.print(f"\n[red][daemon-client] stream error:[/red] {_esc_err(str(e))}")
                finally:
                    done_event.set()

            threading.Thread(target=stream_worker, daemon=True).start()
            if not done_event.wait(timeout=600):
                if _term is not None:
                    _term.print("\n[yellow][daemon] Timed out waiting for response.[/yellow]")

            # 本 turn 结束，重新允许 observer 打印
            _my_turn_id_holder[0] = None

    except KeyboardInterrupt:
        _out("[daemon] Disconnected (daemon continues running)")
    finally:
        # 清理：停止 observer 线程，停止状态栏，并且——这是这次要修的关键
        # 一步——真正关掉 Terminal 自己的后台线程（渲染线程 / 状态栏刷新
        # 线程 / 新增的"定时补打印"线程）。
        #
        # 之前这里只 set 了 _observer_stop、清了状态栏回调，但从没调用过
        # Terminal.stop()，也没有 join _obs_thread。这些线程全是
        # daemon=True，进程退出时 Python 会强制把它们杀掉——如果杀掉的
        # 那一刻它们正好在写 stdout（比如 _ptk_flush_thread 每 0.6s 醒一次
        # 可能正在打印，或者渲染线程正在处理队列里的最后几条消息），就会
        # 撞上解释器关闭时的 stdout 缓冲锁，抛出
        # "Fatal Python error: _enter_buffered_busy: could not acquire
        # lock for <_io.BufferedWriter name='<stdout>'> at interpreter
        # shutdown" ——这正是你复现到的那个崩溃。Terminal.stop() 本来就是
        # 专门为了避免这个问题写的（见它自己的 docstring），只是这条路径
        # 之前忘了调用。
        _observer_stop.set()
        if _obs_thread is not None:
            _obs_thread.join(timeout=2.0)
        if _term is not None:
            try:
                _term.set_statusbar_provider(None)
            except Exception:
                pass
            try:
                _term.stop()
            except Exception:
                pass


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