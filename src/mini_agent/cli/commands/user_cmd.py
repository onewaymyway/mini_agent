"""
cli/commands/user_cmd.py — `mini-agent user` 子命令（daemon 多用户架构 Phase 1）

用法：
    mini-agent user list
    mini-agent user add --name 小明 --role family [--trust 8]
    mini-agent user remove <user_id>
    mini-agent user role <user_id> <role>
    mini-agent user token <user_id>          # 重新生成该用户 token

与 `mini-agent daemon ...` 子命令一样，不进入主 build_parser() 流程，
在 cli/app.py 的 main() 入口最前面按 sys.argv[1] == "user" 整体短路。

实现上不直接读写 .agent/users/users.json，而是统一通过 HTTP 调
daemon 已经开放的 /v1/users 端点（见设计文档 next_doc/
daemon-multiuser-implementation-design.md 第二节）——这样无论 CLI 和
daemon 是否在同一台机器、UserStore 的内存缓存是否已经落盘，结果都以
daemon 当前运行状态为准，不会出现"CLI 直接改了文件，但 daemon 内存
缓存没刷新"这种不一致。

前提：daemon 必须已经以 --http-multi-user 启动；否则 /v1/users 返回 404，
本命令会提示用户先用该参数重启 daemon。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


# ── HTTP 帮助函数（复用 daemon.py 的 daemon 发现逻辑，不重新实现）──────────────

def _find_daemon(project_root: Path):
    """返回 (base_url, token) 或 None（daemon 未运行）。"""
    from mini_agent.cli.daemon import _read_daemon_info, DaemonClient

    info = _read_daemon_info(project_root)
    if info is None:
        return None
    port = info.get("http_port", 8765)
    client = DaemonClient(http_port=port, project_root=project_root)
    return client


def _request(client, method: str, path: str, json_body: Optional[dict] = None):
    """
    发起一次 HTTP 请求，返回 (status_code, parsed_json_or_None)。
    复用 DaemonClient._headers()，与 daemon.py 内部其它请求保持同一套鉴权方式。
    """
    url = client.base_url + path
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    req = urllib.request.Request(url, data=data, headers=client._headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"detail": body.decode(errors="replace")}
    except Exception as e:
        return 0, {"detail": str(e)}


def _print_users_unavailable_hint() -> None:
    print(
        "[user] Multi-user mode is not enabled on the running daemon.\n"
        "       Restart it with: mini-agent daemon start --http --http-multi-user"
    )


# ── 子命令实现 ────────────────────────────────────────────────────────────────

def _cmd_list(client) -> int:
    code, body = _request(client, "GET", "/v1/users")
    if code == 404:
        _print_users_unavailable_hint()
        return 1
    if code != 200:
        print(f"[user] Error ({code}): {body.get('detail') if body else ''}")
        return 1

    users = body.get("users", [])
    if not users:
        print("[user] No users found.")
        return 0

    print(f"  {'user_id':<14} {'name':<14} {'role':<10} {'trust':<6} last_seen")
    print("  " + "─" * 60)
    for u in users:
        import datetime
        last_seen = (
            datetime.datetime.fromtimestamp(u["last_seen"]).strftime("%Y-%m-%d %H:%M")
            if u.get("last_seen") else "-"
        )
        print(f"  {u['user_id']:<14} {u['name']:<14} {u['role']:<10} {u['trust_level']:<6} {last_seen}")
    return 0


def _cmd_add(client, name: str, role: str, trust: int) -> int:
    code, body = _request(client, "POST", "/v1/users", {
        "name": name, "role": role, "trust_level": trust,
    })
    if code == 404:
        _print_users_unavailable_hint()
        return 1
    if code != 200 or not body or not body.get("ok"):
        msg = body.get("message") or body.get("detail") if body else ""
        print(f"[user] Failed to add user: {msg}")
        return 1

    print(f"[user] ✓ Created user {body['user_id']!r} (role={role})")
    print(f"        Token: {body['token']}")
    print("        Give this token to the user — it will not be shown again.")
    return 0


def _cmd_remove(client, user_id: str) -> int:
    code, body = _request(client, "DELETE", f"/v1/users/{user_id}")
    if code == 404 and body and "not enabled" in (body.get("detail") or ""):
        _print_users_unavailable_hint()
        return 1
    if code != 200 or not body or not body.get("ok"):
        msg = body.get("message") or body.get("detail") if body else ""
        print(f"[user] Failed to remove user: {msg}")
        return 1
    print(f"[user] ✓ Removed user {user_id!r}")
    return 0


def _cmd_role(client, user_id: str, role: str) -> int:
    code, body = _request(client, "PATCH", f"/v1/users/{user_id}", {"role": role})
    if code == 404 and body and "not enabled" in (body.get("detail") or ""):
        _print_users_unavailable_hint()
        return 1
    if code != 200 or not body or not body.get("ok"):
        msg = body.get("message") or body.get("detail") if body else ""
        print(f"[user] Failed to update role: {msg}")
        return 1
    print(f"[user] ✓ {user_id!r} role -> {role!r}")
    return 0


def _cmd_token(client, user_id: str) -> int:
    code, body = _request(client, "POST", f"/v1/users/{user_id}/token")
    if code == 404 and body and "not enabled" in (body.get("detail") or ""):
        _print_users_unavailable_hint()
        return 1
    if code != 200 or not body or not body.get("ok"):
        msg = body.get("message") or body.get("detail") if body else ""
        print(f"[user] Failed to rotate token: {msg}")
        return 1
    print(f"[user] ✓ New token for {user_id!r}:")
    print(f"        {body['token']}")
    print("        Old token is now invalid.")
    return 0


# ── 入口 ──────────────────────────────────────────────────────────────────────

def run_user_cli(argv: list[str], project_root: Path) -> int:
    """处理 `mini-agent user <subcommand>` 的入口。返回退出码。"""
    p = argparse.ArgumentParser(prog="mini-agent user", add_help=True)
    sub = p.add_subparsers(dest="subcmd")

    sp_list = sub.add_parser("list", help="列出所有用户")

    sp_add = sub.add_parser("add", help="新增用户")
    sp_add.add_argument("--name", required=True)
    sp_add.add_argument("--role", required=True,
                         choices=["family", "colleague", "agent", "public"])
    sp_add.add_argument("--trust", type=int, default=5, metavar="1-10")

    sp_remove = sub.add_parser("remove", help="删除用户")
    sp_remove.add_argument("user_id")

    sp_role = sub.add_parser("role", help="修改用户角色")
    sp_role.add_argument("user_id")
    sp_role.add_argument("role", choices=["family", "colleague", "agent", "public"])

    sp_token = sub.add_parser("token", help="重新生成用户 token")
    sp_token.add_argument("user_id")

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
            "[user] No daemon running for this project.\n"
            "       Start one first: mini-agent daemon start --http --http-multi-user"
        )
        return 1
    if not client.health_check():
        print("[user] Daemon found but HTTP service not responding.")
        return 1

    if args.subcmd == "list":
        return _cmd_list(client)
    elif args.subcmd == "add":
        return _cmd_add(client, args.name, args.role, args.trust)
    elif args.subcmd == "remove":
        return _cmd_remove(client, args.user_id)
    elif args.subcmd == "role":
        return _cmd_role(client, args.user_id, args.role)
    elif args.subcmd == "token":
        return _cmd_token(client, args.user_id)

    p.print_help()
    return 1
