"""
manage_users.py —— 看板账户管理 CLI（增 / 删 / 查账户）。

看板本身不提供"注册"界面，账户由管理员在服务器上用这个脚本创建，
和 auth.py 共用同一份账户文件（默认 <项目根目录>/.agent/kanban_users.json）。

用法：
    # 新增账户（会交互式让你输入两遍密码，不会回显）
    python manage_users.py add alice --users-file .agent/kanban_users.json

    # 删除账户
    python manage_users.py remove alice --users-file .agent/kanban_users.json

    # 列出所有账户（只显示用户名，不显示密码）
    python manage_users.py list --users-file .agent/kanban_users.json

--users-file 不传时默认用 ./.agent/kanban_users.json（相对当前工作目录），
需要和启动看板时传给 app.py 的 --users-file 保持一致，否则看板读不到你建的账户。
"""
import argparse
import getpass
import sys
from pathlib import Path

from auth import UserStore


def main():
    parser = argparse.ArgumentParser(description="mini-agent 看板账户管理")
    parser.add_argument(
        "--users-file", default=".agent/kanban_users.json",
        help="账户文件路径（默认 ./.agent/kanban_users.json，需要和 app.py 启动参数一致）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="新增账户 / 重置已有账户的密码")
    p_add.add_argument("username")

    p_rm = sub.add_parser("remove", help="删除账户")
    p_rm.add_argument("username")

    sub.add_parser("list", help="列出所有账户")

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
        store.add_user(args.username, pw1)
        print(f"✅ 账户 {args.username!r} 已写入 {args.users_file}")

    elif args.cmd == "remove":
        ok = store.remove_user(args.username)
        print("✅ 已删除" if ok else "⚠️ 账户不存在")

    elif args.cmd == "list":
        users = store.list_users()
        if users:
            print("账户列表：")
            for u in users:
                print(f"  - {u}")
        else:
            print("暂无账户，请先用 `python manage_users.py add <用户名>` 创建")


if __name__ == "__main__":
    main()
