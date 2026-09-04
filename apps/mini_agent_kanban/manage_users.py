"""
manage_users.py —— 看板账户管理 CLI（增 / 删 / 查账户，管理员身份）。

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

--users-file 不传时默认用 ./.agent/kanban_users.json（相对当前工作目录），
需要和启动看板时传给 app.py 的 --users-file 保持一致，否则看板读不到你建的账户。
"""
import argparse
import getpass
import sys
from pathlib import Path

from auth import LastAdminError, UserStore


def main():
    parser = argparse.ArgumentParser(description="mini-agent 看板账户管理")
    parser.add_argument(
        "--users-file", default=".agent/kanban_users.json",
        help="账户文件路径（默认 ./.agent/kanban_users.json，需要和 app.py 启动参数一致）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="新增账户 / 重置已有账户的密码")
    p_add.add_argument("username")
    p_add.add_argument("--admin", action="store_true", help="把该账户设为管理员（可以在页面管理其他账户）")

    p_rm = sub.add_parser("remove", help="删除账户（不能删除最后一个管理员）")
    p_rm.add_argument("username")

    sub.add_parser("list", help="列出所有账户（管理员带 [admin] 标记）")

    p_set = sub.add_parser("set-admin", help="把已有账户设为管理员")
    p_set.add_argument("username")

    p_unset = sub.add_parser("unset-admin", help="取消已有账户的管理员身份（不能取消最后一个管理员）")
    p_unset.add_argument("username")

    args = parser.parse_args()
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
