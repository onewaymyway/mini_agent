"""
cli/commands/self_cmd.py — `mini-agent self` 子命令（daemon 多用户架构 Phase 4）

用法：
    mini-agent self status

与 `mini-agent user ...`（Phase 1）一样，不进入主 build_parser() 流程，
在 cli/app.py 的 main() 入口最前面按 sys.argv[1] == "self" 整体短路，
统一通过 HTTP 调 daemon 的 /v1/self/status 端点，不直接读 daemon 的内部状态
（CLI 和 daemon 可能不在同一台机器上，也避免 CLI 直接 import daemon 内部模块）。

owner-only：单 token 模式下任何能连上 daemon 的人都等同于 owner，正常使用；
多用户模式下非 owner 调用会被 /v1/self/status 拒绝（403），本命令会原样
把错误信息打印出来，不单独在 CLI 这一层再做一次权限判断（避免两处判断逻辑
不一致）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


def _find_daemon(project_root: Path):
    """返回 DaemonClient 或 None（daemon 未运行）。复用 daemon.py 的发现逻辑。"""
    from mini_agent.cli.daemon import _read_daemon_info, DaemonClient

    info = _read_daemon_info(project_root)
    if info is None:
        return None
    port = info.get("http_port", 8765)
    return DaemonClient(http_port=port, project_root=project_root)


def _request(client, method: str, path: str):
    """同 user_cmd.py::_request——发起一次 HTTP 请求，返回 (status_code, parsed_json_or_None)。"""
    url = client.base_url + path
    req = urllib.request.Request(url, headers=client._headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.cli.commands.self_cmd._request')
            return e.code, {"detail": body.decode(errors="replace")}
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.self_cmd._request')
        return 0, {"detail": str(e)}


def _fmt_ts(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.commands.self_cmd._fmt_ts')
        return str(ts)


def _cmd_status(client) -> int:
    code, body = _request(client, "GET", "/v1/self/status")
    if code == 403:
        print("[self] Owner only — your token does not have access to this.")
        return 1
    if code != 200 or body is None:
        msg = body.get("detail") if body else ""
        print(f"[self] Error ({code}): {msg}")
        return 1

    al = body.get("autonomous_loop")
    print("\033[1mAutonomous Loop\033[0m")
    if al is None:
        print("  (not available — AutonomousLoop failed to initialize)")
    else:
        print(f"  autonomy_level : {al.get('autonomy_level', '?')}")
        print(f"  last_tick_at   : {_fmt_ts(al.get('last_tick_at'))}")
        print(f"  tick_count     : {al.get('tick_count', 0)}")
        print(f"  tick_interval  : {al.get('tick_interval_seconds', '?')}s")

    goals = body.get("goals") or {}
    objectives = goals.get("active_objectives", [])
    active_goals = goals.get("active_goals", [])
    print(f"\n\033[1mGoals\033[0m  ({len(active_goals)} active goal(s), "
          f"{len(objectives)} active objective(s))")
    for g in active_goals[:5]:
        print(f"  [goal]      {g.get('id', '?'):<10} {g.get('title', '')[:60]}")
    for o in objectives[:5]:
        print(f"  [objective] {o.get('id', '?'):<10} {o.get('title', '')[:60]}")
    if not active_goals and not objectives:
        print("  (no active goals/objectives)")

    activity = body.get("recent_activity") or []
    print(f"\n\033[1mRecent Activity\033[0m (last 24h, {len(activity)} record(s))")
    if not activity:
        print("  (none)")
    else:
        for rec in activity[-10:]:
            ts = _fmt_ts(rec.get("at"))
            rec_type = rec.get("type", "?")
            summary = rec.get("summary", "")[:70]
            marker = "\033[33m⚠\033[0m " if rec_type == "session_crashed" else "  "
            print(f"  {marker}[{ts}] {rec_type}: {summary}")

    pool = body.get("session_pool")
    print(f"\n\033[1mSession Pool\033[0m")
    if pool is None:
        print("  (multi-user mode not enabled)")
    else:
        print(f"  active sessions: {pool.get('active_count', 0)}")
        for s in pool.get("sessions", [])[:10]:
            alive = "●" if s.get("is_alive") else "○"
            print(
                f"  {alive} {s.get('session_id', '?'):<14} "
                f"user={s.get('user_id', '?'):<12} role={s.get('role', '?'):<10} "
                f"idle={s.get('idle_seconds', 0):.0f}s"
            )

    return 0


def run_self_cli(argv: list[str], project_root: Path) -> int:
    """处理 `mini-agent self <subcommand>` 的入口。返回退出码。"""
    p = argparse.ArgumentParser(prog="mini-agent self", add_help=True)
    sub = p.add_subparsers(dest="subcmd")
    sub.add_parser("status", help="查看 Self 的状态总览（GoalBacklog / 自主活动 / SessionAgentPool）")

    if not argv:
        p.print_help()
        return 1

    args = p.parse_args(argv)
    if not args.subcmd:
        p.print_help()
        return 1

    client = _find_daemon(project_root)
    if client is None:
        print(
            "[self] No daemon running for this project.\n"
            "       Start one first: mini-agent daemon start --http"
        )
        return 1
    if not client.health_check():
        print("[self] Daemon found but HTTP service not responding.")
        return 1

    if args.subcmd == "status":
        return _cmd_status(client)

    p.print_help()
    return 1
