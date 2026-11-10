"""
账户管理命令行（共享账户目录，所有 Streamlit 应用共用）。

    python -m mini_agent.web_auth add alice --admin     # 新增账户（交互输入密码，不回显）
    python -m mini_agent.web_auth list                  # 列出账户（[admin] 标记管理员）
    python -m mini_agent.web_auth passwd alice          # 修改密码
    python -m mini_agent.web_auth remove alice          # 删除账户（不能删最后一个管理员）
    python -m mini_agent.web_auth set-admin alice       # 设为管理员
    python -m mini_agent.web_auth unset-admin alice     # 取消管理员（不能取消最后一个）
    python -m mini_agent.web_auth revoke-all --username alice   # 撤销某用户全部会话（不传则撤销全部）

账户目录默认 ~/.agent/web_auth/，可用 --auth-dir 或环境变量 MINI_AGENT_WEB_AUTH_DIR 指定。
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from mini_agent.web_auth.accounts import LastAdminError, SessionStore, UserStore
from mini_agent.web_auth.gate import auth_paths, shared_auth_dir


def _read_password(prompt: str = "密码: ") -> str:
    while True:
        pw = getpass.getpass(prompt)
        pw2 = getpass.getpass("确认密码: ")
        if pw != pw2:
            print("两次输入不一致，请重试。", file=sys.stderr)
            continue
        if len(pw) < 6:
            print("密码至少 6 位，请重试。", file=sys.stderr)
            continue
        return pw


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mini_agent.web_auth", description=__doc__.split("\n")[1])
    p.add_argument("--auth-dir", default="", help="账户目录（默认 ~/.agent/web_auth 或 $MINI_AGENT_WEB_AUTH_DIR）")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增账户")
    a.add_argument("username")
    a.add_argument("--admin", action="store_true", help="设为管理员")

    sub.add_parser("list", help="列出账户")
    for name in ("passwd", "remove", "set-admin", "unset-admin"):
        s = sub.add_parser(name)
        s.add_argument("username")
    r = sub.add_parser("revoke-all", help="撤销会话")
    r.add_argument("--username", default="", help="只撤销该用户的会话；不传则撤销全部")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    base = Path(args.auth_dir).expanduser() if args.auth_dir else shared_auth_dir()
    paths = auth_paths(base)
    store = UserStore(paths.users)
    sessions = SessionStore(paths.sessions)
    print(f"账户目录：{base}")

    try:
        if args.cmd == "add":
            store.add_user(args.username, _read_password(), is_admin=args.admin)
            print(f"已添加账户 {args.username}" + ("（管理员）" if args.admin else ""))
        elif args.cmd == "list":
            users = store.list_users_detailed() if hasattr(store, "list_users_detailed") else []
            if not users:
                print("（暂无账户）")
            for u in users:
                name = u.get("username") if isinstance(u, dict) else u
                admin = " [admin]" if (isinstance(u, dict) and u.get("is_admin")) or store.is_admin(name) else ""
                print(f"{name}{admin}")
        elif args.cmd == "passwd":
            store.add_user(args.username, _read_password("新密码: "), is_admin=store.is_admin(args.username))
            print(f"已修改 {args.username} 的密码")
        elif args.cmd == "remove":
            print("已删除" if store.remove_user(args.username) else "账户不存在")
        elif args.cmd == "set-admin":
            print("已设为管理员" if store.set_admin(args.username, True) else "账户不存在")
        elif args.cmd == "unset-admin":
            print("已取消管理员" if store.set_admin(args.username, False) else "账户不存在")
        elif args.cmd == "revoke-all":
            n = sessions.revoke_all_for_user(args.username) if args.username else sessions.revoke_all()
            print(f"已撤销 {n} 个会话")
    except LastAdminError as exc:
        print(f"操作被拒绝：{exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
