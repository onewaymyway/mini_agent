"""cli/daemon_supervisor.py — daemon 崩溃监控者（阶段一：只观测+告警，
不重启；阶段二会在同一个循环里加上真正的自动重启）。

设计背景见 next_doc/daemon_crash_recovery_and_alert_plan.md §3.3。

为什么需要一个独立进程，而不是把这套逻辑放进 daemon 自己：daemon 都崩了
（未捕获异常、被信号杀死、OOM、native crash），它自己进程内的任何逻辑都
没机会跑——必须由进程外的监控者发现"子进程消失了"这件事本身。

这个模块以 `python -m mini_agent.cli.daemon_supervisor` 的方式被
`cli/daemon.py::cmd_daemon_start(detach=True)` 通过 subprocess.Popen 拉起，
自己也是一个 detach 的后台进程（写 `.agent/daemon_supervisor.pid`）。

子进程（真正跑 HTTP 服务/Agent 的那个 daemon-mode 进程）的启动参数通过
环境变量 `MINI_AGENT_SUPERVISOR_CHILD_ARGV`（JSON 数组）传入，而不是走
命令行参数——避免跟 supervisor 自己的 argparse 参数混在一起转义出错。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def _log_path(project_root: Path) -> Path:
    return project_root / ".agent" / "daemon.log"


class _HangDetected(Exception):
    """内部信号：探活判定为卡死。不是真正的异常，只是用来跳出等待循环，
    携带诊断用的 reason 字符串。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _wait_child(
    proc: "subprocess.Popen",
    project_root: Path,
    http_port: Optional[int],
    hang_detection_enabled: bool,
    hang_check_interval_seconds: float,
    hang_check_timeout_seconds: float,
    hang_consecutive_failures: int,
    post_restart_health_check_seconds: float = 0.0,
) -> tuple[Optional[int], bool, str]:
    """等待子进程退出，期间按配置做主动探活（daemon_hang_detection_and_
    alert_escalation_plan.md §1.2），并可选地在启动阶段先做一次一次性的
    健康验证（§2.2）。

    返回 `(returncode, was_hang, hang_reason)`：
    - 子进程正常退出（含被信号杀死）：`(returncode, False, "")`
    - 被判定为卡死并强杀：`(None, True, reason)`，`reason` 是给
      `record_daemon_crash(hang_reason=...)` 用的人类可读描述。

    `http_port` 为 None（比如调用方没能读到端口配置）或
    `hang_detection_enabled=False` 时，退化为原来的"一直阻塞直到子进程
    退出"（`proc.wait()`），完全不做探活，行为与阶段一之前一致。
    """
    if not hang_detection_enabled or not http_port:
        return proc.wait(), False, ""

    from mini_agent.cli.daemon import DaemonClient, _force_kill_process

    client = DaemonClient(http_port, project_root=project_root)

    # [daemon_hang_detection_and_alert_escalation_plan.md §2.2] 重启后一次性
    # 健康验证：原来判定"这轮重启是否成功"只看"新子进程有没有立刻退出"，
    # 不代表 HTTP 服务真的起来了——如果新进程卡在初始化阶段，要等它自己
    # 再崩一次，或者等常规探活轮询走完多轮判定才会被发现，期间用户完全
    # 不知情。这里在进入下面的常规探活轮询之前，先给新进程一个固定的
    # 窗口期（`post_restart_health_check_seconds`）证明自己真的可用；
    # 窗口期内子进程自己退出（比如配置错误直接崩了）按正常"进程退出"
    # 处理，不算卡死；窗口期内一直没通过健康检查则按卡死处理（本质上是
    # 把"启动阶段的卡死"和"运行期间的卡死"统一到同一套判定逻辑）。
    if post_restart_health_check_seconds > 0:
        deadline = time.time() + post_restart_health_check_seconds
        became_healthy = False
        poll_interval = min(1.0, max(0.2, hang_check_interval_seconds))
        while time.time() < deadline:
            if proc.poll() is not None:
                return proc.returncode, False, ""
            if client.health_check(timeout=hang_check_timeout_seconds):
                became_healthy = True
                break
            time.sleep(poll_interval)
        if not became_healthy:
            if proc.poll() is not None:
                return proc.returncode, False, ""
            reason = (
                f"重启后 {post_restart_health_check_seconds:.0f}s 内未通过健康检查"
                "（新进程可能卡在初始化阶段），已强制终止"
            )
            _force_kill_process(proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return None, True, reason

    consecutive_failures = 0
    interval = max(0.5, hang_check_interval_seconds)

    while True:
        try:
            returncode = proc.wait(timeout=interval)
            return returncode, False, ""
        except subprocess.TimeoutExpired:
            pass

        if client.health_check(timeout=hang_check_timeout_seconds):
            consecutive_failures = 0
            continue

        consecutive_failures += 1
        if consecutive_failures < hang_consecutive_failures:
            continue

        # 连续多次探测无响应，判定为卡死：进程仍存活但已经无法正常工作，
        # 直接强杀（跳过优雅关停尝试，见 _force_kill_process 的注释）。
        reason = (
            f"连续 {consecutive_failures} 次健康检查无响应"
            f"（每次间隔 {interval:.0f}s，超时 {hang_check_timeout_seconds:.0f}s），"
            "已强制终止"
        )
        _force_kill_process(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return None, True, reason


def run_supervisor(
    project_root: Path,
    child_argv: list[str],
    auto_restart: bool = False,
    max_attempts: int = 5,
    window_seconds: float = 600.0,
    backoff_seconds: Optional[list[float]] = None,
    http_port: Optional[int] = None,
    hang_detection_enabled: bool = True,
    hang_check_interval_seconds: float = 10.0,
    hang_check_timeout_seconds: float = 2.0,
    hang_consecutive_failures: int = 3,
    post_restart_health_check_seconds: float = 0.0,
) -> None:
    """核心监控循环。阶段一调用方固定传 `auto_restart=False`——检测到
    崩溃后只记录+告警，不重启，循环在这一次崩溃后自然结束（等价于"预算
    为 0 次"）。阶段二会把 `auto_restart` 接到配置项、默认打开。

    `http_port`/`hang_detection_enabled`/`hang_check_*`：
    daemon_hang_detection_and_alert_escalation_plan.md 阶段一新增的卡死
    主动探活参数——子进程"退出"和"存活但无响应"是两种性质不同的故障，
    原来只能感知前者（`proc.wait()` 永远不返回后者的情况）。`http_port`
    为 None 时自动退化为不探活（比如调用方没能读到端口配置）。

    `post_restart_health_check_seconds`：阶段二 §2.2 新增，每次 Popen 新
    子进程后先给它这么长时间证明自己真的把 HTTP 服务起来了，超时未通过
    则按卡死处理，不必等到常规探活轮询的多轮判定。传 0 关闭这项检查。"""
    from mini_agent.cli.daemon import (
        _cleanup_pid_files,
        _supervisor_pid_file,
        _read_run_state,
        _run_state_file,
        _STATUS_STOPPED_BY_USER,
        record_daemon_crash,
        count_recent_restart_events,
    )

    backoff_seconds = backoff_seconds or [1, 2, 4, 8, 16, 30, 60]
    supervisor_pid_path = _supervisor_pid_file(project_root)
    supervisor_pid_path.parent.mkdir(parents=True, exist_ok=True)
    supervisor_pid_path.write_text(str(os.getpid()))

    log_path = _log_path(project_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 滑动窗口内的重启时间戳，用于重启预算判断（阶段二会真正用到；
    # 阶段一 auto_restart 恒为 False，这个列表始终不会触发第二次循环，
    # 但先按最终形态实现，避免阶段二再重写一遍循环结构）。
    restart_timestamps: list[float] = []
    attempt = 0

    try:
        while True:
            # 每轮重新打开日志文件，避免同一个文件句柄跨多次重启后
            # 追加写入错乱（子进程崩溃后原 fd 可能已经处于异常状态）。
            log_file = open(log_path, "a", encoding="utf-8", errors="replace")
            try:
                if sys.platform == "win32":
                    DETACHED_PROCESS = 0x00000008
                    CREATE_NEW_PROCESS_GROUP = 0x00000200
                    kwargs = {
                        "stdin": subprocess.DEVNULL,
                        "stdout": log_file,
                        "stderr": log_file,
                        "creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                    }
                else:
                    kwargs = {
                        "stdin": subprocess.DEVNULL,
                        "stdout": log_file,
                        "stderr": log_file,
                        "close_fds": True,
                        "start_new_session": True,
                    }
                proc = subprocess.Popen(child_argv, **kwargs)
            finally:
                log_file.close()

            started_at = time.time()
            child_pid = proc.pid
            returncode, was_hang, hang_reason = _wait_child(
                proc,
                project_root,
                http_port,
                hang_detection_enabled,
                hang_check_interval_seconds,
                hang_check_timeout_seconds,
                hang_consecutive_failures,
                post_restart_health_check_seconds,
            )

            if not was_hang:
                state = _read_run_state(project_root)
                if state and state.get("status") == _STATUS_STOPPED_BY_USER:
                    # 预期内的停止（daemon stop / HTTP shutdown / 信号）——
                    # supervisor 也随之退出，不记录崩溃、不重启。
                    break

            # 非预期退出（含被判定为卡死后强杀）：无论是未捕获异常、被外部
            # 信号杀死、OOM/native crash，还是卡死，run_state 都还停留在
            # "running"（或者干脆读不到——子进程可能在写文件之前就挂了），
            # 这里统一按"异常"处理，只是 decision/summary 会区分卡死场景。
            restart_timestamps.append(time.time())
            cutoff = time.time() - window_seconds
            restart_timestamps = [t for t in restart_timestamps if t >= cutoff]

            # [daemon_hang_detection_and_alert_escalation_plan.md §2.1]
            # 预算判断不再只看内存里的 restart_timestamps（supervisor
            # 自身如果异常退出，下次是全新实例、内存计数归零）——回溯
            # daemon_crash_history.jsonl 里最近 window_seconds 内已经真实
            # 发生过的重启次数，与内存计数取较大值。file 计数在这次事件
            # 写入历史文件之前统计，所以 +1 补上"这次事件本身"（内存计数
            # 已经在上面 append 时天然包含了这次）。
            file_restart_count = count_recent_restart_events(project_root, window_seconds) + 1
            effective_count = max(len(restart_timestamps), file_restart_count)

            will_restart = auto_restart and effective_count <= max_attempts
            if was_hang:
                decision = "restarted" if will_restart else ("giveup" if auto_restart else "hang_killed")
            else:
                decision = "restarted" if will_restart else ("giveup" if auto_restart else "no_restart")

            record_daemon_crash(
                project_root,
                pid=child_pid,
                exit_code=returncode,
                started_at=started_at,
                log_path=log_path,
                restart_attempt=attempt,
                restart_decision=decision,
                hang_reason=hang_reason if was_hang else None,
            )

            if not will_restart:
                break

            attempt += 1
            delay = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
            time.sleep(delay)
            # 重启前把 run_state 恢复成待观察状态，避免上一轮残留的
            # stopped_by_user/running 状态干扰下一轮判定——新子进程启动
            # 后会自己重新写一次 "running"，这里只是防止在它写之前的
            # 空档期读到陈旧状态。
            try:
                _run_state_file(project_root).unlink(missing_ok=True)
            except OSError:
                pass
    finally:
        try:
            _cleanup_pid_files(project_root)
        except Exception:
            pass
        try:
            supervisor_pid_path.unlink(missing_ok=True)
        except OSError:
            pass


def run_foreground_supervisor(
    project_root: Path,
    child_argv: list[str],
    auto_restart: bool = True,
    max_attempts: int = 5,
    window_seconds: float = 600.0,
    backoff_seconds: Optional[list[float]] = None,
    http_port: Optional[int] = None,
    hang_detection_enabled: bool = True,
    hang_check_interval_seconds: float = 10.0,
    hang_check_timeout_seconds: float = 2.0,
    hang_consecutive_failures: int = 3,
    post_restart_health_check_seconds: float = 0.0,
) -> int:
    """前台（不带 --detach）统一使用的 supervisor 循环（阶段三）。

    与后台 `run_supervisor` 的区别只有三处：
    1. 子进程原样继承当前控制台的 stdin/stdout/stderr（不重定向到
       daemon.log），用户依然直接在终端里看到 daemon 的实时输出，效果上
       与旧版 `os.execv`/Windows `Popen+wait` 一致；
    2. supervisor 自身不 detach，就是用户手上这个终端进程本身，但同样写
       `daemon_supervisor.pid`，方便另一个终端里 `daemon stop` 能找到并
       信号它（复用 `_stop_supervisor`，逻辑无需区分前台/后台）；
    3. Ctrl-C（KeyboardInterrupt）在这里被捕获：先调用
       `mark_stopped_by_user` 标记停止意图（不依赖子进程自己来得及写，
       如果子进程收到信号后处理不过来直接被杀，这里已经兜底标记过，
       不会被误判为崩溃），再把信号转发给子进程，然后继续等待其退出。

    崩溃记录、告警发送、重启预算/退避复用与后台完全相同的判定顺序：
    先记录+告警，再决定是否重启（见 daemon_crash_recovery_and_alert_plan
    §3.3 "先感知、后恢复"）。
    """
    from mini_agent.cli.daemon import (
        _cleanup_pid_files,
        _supervisor_pid_file,
        _read_run_state,
        _run_state_file,
        _STATUS_STOPPED_BY_USER,
        record_daemon_crash,
        mark_stopped_by_user,
        count_recent_restart_events,
    )

    backoff_seconds = backoff_seconds or [1, 2, 4, 8, 16, 30, 60]
    supervisor_pid_path = _supervisor_pid_file(project_root)
    supervisor_pid_path.parent.mkdir(parents=True, exist_ok=True)
    supervisor_pid_path.write_text(str(os.getpid()))

    log_path = _log_path(project_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    restart_timestamps: list[float] = []
    attempt = 0
    final_returncode = 0

    try:
        while True:
            if sys.platform == "win32":
                # CREATE_NEW_PROCESS_GROUP：与旧版 Windows 前台分支一致，
                # 让子进程能接收独立的 CTRL_C_EVENT 转发。
                CREATE_NEW_PROCESS_GROUP = 0x00000200
                proc = subprocess.Popen(
                    child_argv, creationflags=CREATE_NEW_PROCESS_GROUP
                )
            else:
                # 不设置 start_new_session：子进程与 supervisor 留在同一个
                # 前台进程组，终端本身发出的 Ctrl-C（SIGINT）会同时送达
                # 两者；下面仍然显式转发一次作为兜底（比如子进程因为某些
                # 原因不在同一进程组时）。
                proc = subprocess.Popen(child_argv)

            started_at = time.time()
            child_pid = proc.pid
            user_interrupted = False
            was_hang = False
            hang_reason = ""
            try:
                returncode, was_hang, hang_reason = _wait_child(
                    proc,
                    project_root,
                    http_port,
                    hang_detection_enabled,
                    hang_check_interval_seconds,
                    hang_check_timeout_seconds,
                    hang_consecutive_failures,
                    post_restart_health_check_seconds,
                )
            except KeyboardInterrupt:
                user_interrupted = True
                mark_stopped_by_user(project_root, pid=child_pid)
                try:
                    import signal as _signal
                    if sys.platform == "win32":
                        proc.send_signal(_signal.CTRL_C_EVENT)  # type: ignore[attr-defined]
                    else:
                        proc.send_signal(_signal.SIGINT)
                except Exception as exc:
                    from mini_agent.errors import log_exception
                    log_exception(
                        exc,
                        where="mini_agent.cli.daemon_supervisor.run_foreground_supervisor",
                    )
                returncode = proc.wait()

            final_returncode = returncode or 0

            if not was_hang:
                state = _read_run_state(project_root)
                if user_interrupted or (
                    state and state.get("status") == _STATUS_STOPPED_BY_USER
                ):
                    break

            restart_timestamps.append(time.time())
            cutoff = time.time() - window_seconds
            restart_timestamps = [t for t in restart_timestamps if t >= cutoff]

            file_restart_count = count_recent_restart_events(project_root, window_seconds) + 1
            effective_count = max(len(restart_timestamps), file_restart_count)

            will_restart = auto_restart and effective_count <= max_attempts
            if was_hang:
                decision = "restarted" if will_restart else ("giveup" if auto_restart else "hang_killed")
            else:
                decision = (
                    "restarted"
                    if will_restart
                    else ("giveup" if auto_restart else "no_restart")
                )

            record_daemon_crash(
                project_root,
                pid=child_pid,
                exit_code=returncode,
                started_at=started_at,
                log_path=log_path,
                restart_attempt=attempt,
                restart_decision=decision,
                hang_reason=hang_reason if was_hang else None,
            )

            if not will_restart:
                label = "Hung" if was_hang else "Crashed"
                if decision == "giveup":
                    print(
                        f"[daemon] {label} (exit={returncode}). "
                        f"Auto-restart budget exhausted, giving up."
                    )
                else:
                    print(f"[daemon] {label} (exit={returncode}). Not restarting.")
                break

            attempt += 1
            delay = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
            label = "Hung" if was_hang else "Crashed"
            print(
                f"[daemon] {label} (exit={returncode}). "
                f"Restarting in {delay}s (attempt {attempt}/{max_attempts})..."
            )
            time.sleep(delay)
            try:
                _run_state_file(project_root).unlink(missing_ok=True)
            except OSError:
                pass
    finally:
        try:
            _cleanup_pid_files(project_root)
        except Exception:
            pass
        try:
            supervisor_pid_path.unlink(missing_ok=True)
        except OSError:
            pass

    return final_returncode


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="mini_agent.cli.daemon_supervisor")
    parser.add_argument("--project", required=True)
    parser.add_argument("--auto-restart", action="store_true", default=False)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--window-seconds", type=float, default=600.0)
    parser.add_argument("--http-port", type=int, default=None)
    parser.add_argument("--hang-detection", action="store_true", default=False)
    parser.add_argument("--hang-check-interval", type=float, default=10.0)
    parser.add_argument("--hang-check-timeout", type=float, default=2.0)
    parser.add_argument("--hang-consecutive-failures", type=int, default=3)
    parser.add_argument("--post-restart-health-check-seconds", type=float, default=30.0)
    args = parser.parse_args()

    project_root = Path(args.project)
    child_argv_json = os.environ.get("MINI_AGENT_SUPERVISOR_CHILD_ARGV")
    if not child_argv_json:
        print("[daemon-supervisor] MINI_AGENT_SUPERVISOR_CHILD_ARGV not set", file=sys.stderr)
        return 1
    child_argv = json.loads(child_argv_json)

    run_supervisor(
        project_root,
        child_argv,
        auto_restart=args.auto_restart,
        max_attempts=args.max_attempts,
        window_seconds=args.window_seconds,
        http_port=args.http_port,
        hang_detection_enabled=args.hang_detection,
        hang_check_interval_seconds=args.hang_check_interval,
        hang_check_timeout_seconds=args.hang_check_timeout,
        hang_consecutive_failures=args.hang_consecutive_failures,
        post_restart_health_check_seconds=args.post_restart_health_check_seconds,
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
