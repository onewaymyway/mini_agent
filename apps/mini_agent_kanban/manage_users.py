"""
manage_users.py —— 看板账户管理 CLI（增 / 删 / 查账户，管理员身份，会话）。

看板本身不提供"注册"界面，账户由管理员在服务器上用这个脚本创建，
和 auth.py 共用同一份账户文件（默认 <项目根目录>/.agent/kanban_users.json）。
[kanban_account_management_ui_plan.md] 之后，看板页面里也能做同样的事
（登录后进"👤 账户管理" tab），这个脚本主要用在：部署时创建第一个
管理员账户（鸡生蛋问题——页面兜底需要"没有任何管理员"这个前提，命令行
是最直接的路径）、以及不方便打开浏览器时的应急操作。

用法：
    # 新增账户（会交互式让你输入两遍密码，不会回显）；--admin 把它设为管理员
    python manage_users.py add alice --admin --users-file .agent/kanban_users.json

    # 删除账户（不能删除最后一个管理员）
    python manage_users.py remove alice --users-file .agent/kanban_users.json

    # 列出所有账户（管理员账户带 [admin] 标记，不显示密码）
    python manage_users.py list --users-file .agent/kanban_users.json

    # 把已有账户设为 / 取消管理员（不能取消最后一个管理员）
    python manage_users.py set-admin alice --users-file .agent/kanban_users.json
    python manage_users.py unset-admin alice --users-file .agent/kanban_users.json

[kanban_session_management_plan.md] 会话管理——应对"带免登录 token 的
URL 意外泄露"场景的命令行手段，效果和页面"👤 账户管理"里"我的会话"/
"所有会话"一致，只是不用打开浏览器：

    # 列出当前有效（未过期）的会话，可选按用户名过滤
    python manage_users.py list-sessions --sessions-file .agent/kanban_sessions.json
    python manage_users.py list-sessions --username alice --sessions-file .agent/kanban_sessions.json

    # 撤销单个会话（session id 从 list-sessions 输出里拿）
    python manage_users.py revoke-session <session_id> --sessions-file .agent/kanban_sessions.json

    # 撤销某个用户的全部会话（不传 --username 则撤销所有人的全部会话，
    # 效果类似轮换 kanban_session_secret，但不用真的去改密钥文件）
    python manage_users.py revoke-all-sessions --username alice --sessions-file .agent/kanban_sessions.json
    python manage_users.py revoke-all-sessions --sessions-file .agent/kanban_sessions.json

--users-file / --sessions-file 不传时分别默认用
./.agent/kanban_users.json、./.agent/kanban_sessions.json（相对当前工作
目录），需要和启动看板时传给 app.py 的 --users-file / --project-root
保持一致，否则看板读不到你在这里做的修改。
"""
import argparse
import getpass
import sys
import time
from pathlib import Path

from auth import LastAdminError, SessionStore, UserStore


def main():
    # [用户实测反馈修复] --users-file 原来只挂在最外层 parser 上：
    # argparse 的子命令（subparsers）机制下，外层 parser 定义的可选参数
    # 必须写在子命令 token 之前（`manage_users.py --users-file X add
    # alice`），写在子命令后面（`manage_users.py add alice --users-file X`）
    # 会报 "unrecognized arguments"——这正是本文件顶部"用法"示例和
    # README 里一直写的顺序，实际跑起来会报错。用一个 `--users-file`
    # 共用的 `parent_parser`（`add_help=False` 避免和子命令自己的 `-h`
    # 冲突）分别 `parents=[...]` 挂到每个子命令 parser 上，两种顺序都能用，
    # 不用强迫用户记"这个参数必须放在子命令前面"这种反直觉规则。
    users_file_parser = argparse.ArgumentParser(add_help=False)
    users_file_parser.add_argument(
        "--users-file", default=".agent/kanban_users.json",
        help="账户文件路径（默认 ./.agent/kanban_users.json，需要和 app.py 启动参数一致）",
    )

    sessions_file_parser = argparse.ArgumentParser(add_help=False)
    sessions_file_parser.add_argument(
        "--sessions-file", default=".agent/kanban_sessions.json",
        help="会话登记表文件路径（默认 ./.agent/kanban_sessions.json，"
             "需要和看板实际使用的项目根目录一致）",
    )

    # [用户实测反馈修复] `--users-file` 只挂在每个子命令 parser 上（不
    # 挂在最外层 parser），用 `parents=[users_file_parser]` 复用同一份
    # 参数定义。注意：不能同时挂在最外层 parser 和子命令 parser 上——
    # 那样当 `--users-file` 写在子命令 token 之前时，子命令 parser 自己
    # 的默认值会在解析子命令参数时覆盖掉外层已经解析到的值，反而更容易
    # 出错（同名 dest 在两层 parser 间不会"透传"，后解析的那层会赢）。
    # 现在只有一处定义，`--users-file` 必须写在子命令 token 之后
    # （`add alice --users-file X`），这也是本文件"用法"示例和 README
    # 里一直展示的顺序。`--sessions-file` 同理。
    parser = argparse.ArgumentParser(description="mini-agent 看板账户管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="新增账户 / 重置已有账户的密码", parents=[users_file_parser])
    p_add.add_argument("username")
    p_add.add_argument("--admin", action="store_true", help="把该账户设为管理员（可以在页面管理其他账户）")

    p_rm = sub.add_parser("remove", help="删除账户（不能删除最后一个管理员）", parents=[users_file_parser])
    p_rm.add_argument("username")

    sub.add_parser("list", help="列出所有账户（管理员带 [admin] 标记）", parents=[users_file_parser])

    p_set = sub.add_parser("set-admin", help="把已有账户设为管理员", parents=[users_file_parser])
    p_set.add_argument("username")

    p_unset = sub.add_parser(
        "unset-admin", help="取消已有账户的管理员身份（不能取消最后一个管理员）",
        parents=[users_file_parser],
    )
    p_unset.add_argument("username")

    p_ls = sub.add_parser(
        "list-sessions", help="列出当前有效的登录会话", parents=[sessions_file_parser]
    )
    p_ls.add_argument("--username", default=None, help="只看这个用户的会话，不传则列出所有人的")

    p_rs = sub.add_parser(
        "revoke-session", help="撤销单个会话（session id 从 list-sessions 输出里拿）",
        parents=[sessions_file_parser],
    )
    p_rs.add_argument("session_id")

    p_ras = sub.add_parser(
        "revoke-all-sessions",
        help="撤销某个用户的全部会话；不传 --username 则撤销所有人的全部会话",
        parents=[sessions_file_parser],
    )
    p_ras.add_argument("--username", default=None, help="只撤销这个用户的会话，不传则撤销所有人的")

    args = parser.parse_args()

    if args.cmd in ("list-sessions", "revoke-session", "revoke-all-sessions"):
        session_store = SessionStore(Path(args.sessions_file))

        if args.cmd == "list-sessions":
            sessions = session_store.list_sessions(username=args.username)
            if not sessions:
                print("当前没有任何有效会话。")
            else:
                print("会话列表：")
                for s in sessions:
                    issued = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["issued_at"]))
                    last_seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_seen"]))
                    print(
                        f"  - [{s['username']}] session_id={s['session_id']} "
                        f"登录={issued} 最近活跃={last_seen} 客户端={s['client_id'] or '未知'}"
                    )

        elif args.cmd == "revoke-session":
            ok = session_store.revoke(args.session_id)
            print("✅ 已撤销" if ok else "⚠️ 会话不存在（可能已经过期被自动清理，或 id 输错了）")

        elif args.cmd == "revoke-all-sessions":
            if args.username:
                count = session_store.revoke_all_for_user(args.username)
                print(f"✅ 已撤销 {args.username!r} 的 {count} 个会话")
            else:
                count = session_store.revoke_all()
                print(f"✅ 已撤销全部 {count} 个会话（强制所有人重新登录）")
        return

    store = UserStore(Path(args.users_file))

    if args.cmd == "add":
        pw1 = getpass.getpass("设置密码: ")
        pw2 = getpass.getpass("再次输入密码: ")
        if pw1 != pw2:
            print("❌ 两次输入的密码不一致，已取消。")
            sys.exit(1)
        if len(pw1) < 6:
            print("❌ 密码太短，建议至少 6 位。")
            sys.exit(1)
        store.add_user(args.username, pw1, is_admin=args.admin)
        tag = "（管理员）" if args.admin else ""
        print(f"✅ 账户 {args.username!r}{tag} 已写入 {args.users_file}")

    elif args.cmd == "remove":
        try:
            ok = store.remove_user(args.username)
            print("✅ 已删除" if ok else "⚠️ 账户不存在")
        except LastAdminError as exc:
            print(f"❌ {exc}")
            sys.exit(1)

    elif args.cmd == "list":
        users = store.list_users_detailed()
        if users:
            print("账户列表：")
            for u in users:
                tag = " [admin]" if u["is_admin"] else ""
                print(f"  - {u['username']}{tag}")
        else:
            print("暂无账户，请先用 `python manage_users.py add <用户名> --admin` 创建第一个管理员账户")

    elif args.cmd in ("set-admin", "unset-admin"):
        want_admin = args.cmd == "set-admin"
        try:
            ok = store.set_admin(args.username, want_admin)
            if not ok:
                print("⚠️ 账户不存在")
            else:
                print(f"✅ {args.username!r} 现在{'是' if want_admin else '不是'}管理员")
        except LastAdminError as exc:
            print(f"❌ {exc}")
            sys.exit(1)


if __name__ == "__main__":
    main()
