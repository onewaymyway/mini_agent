#!/usr/bin/env python3
"""
scripts/get_token.py
=====================
交互式 Token 获取脚本。

功能
----
1. 读取 ~/.openclaw/openclaw.json，列出所有已登录的微信账号
2. 若无账号，引导用户运行 ``openclaw channels login`` 扫码
3. 支持选择账号并输出 WEIXIN_BASE_URL / WEIXIN_TOKEN 环境变量，
   方便复制到 shell 或 .env 文件

用法
----
    python scripts/get_token.py               # 列出账号，交互选择
    python scripts/get_token.py --index 0     # 直接选第 0 个账号
    python scripts/get_token.py --login       # 强制重新扫码登录
    python scripts/get_token.py --env         # 输出可 eval 的 export 语句
    python scripts/get_token.py --dotenv      # 写入 .env 文件

示例（eval 到当前 shell）
------------------------
    eval "$(python scripts/get_token.py --env)"
    python examples/claude_code_bot.py
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 支持从项目根目录直接运行
sys.path.insert(0, str(Path(__file__).parent.parent))

from weixin.auth import (
    WeixinAccount,
    get_account,
    list_accounts,
    login,
    read_openclaw_config,
    _extract_gateway_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_accounts(accounts: list[WeixinAccount]) -> None:
    print("\n已登录的微信账号：\n")
    for acct in accounts:
        print(f"  {acct}")
    print()


def _interactive_select(accounts: list[WeixinAccount]) -> WeixinAccount:
    if len(accounts) == 1:
        print(f"自动选择唯一账号：{accounts[0]}")
        return accounts[0]

    _print_accounts(accounts)
    while True:
        raw = input(f"请选择账号序号 [0-{len(accounts)-1}]（默认 0）：").strip()
        if raw == "":
            return accounts[0]
        try:
            idx = int(raw)
            return accounts[idx]
        except (ValueError, IndexError):
            print(f"无效输入，请输入 0 到 {len(accounts)-1} 之间的数字。")


def _output_env(acct: WeixinAccount) -> None:
    """输出可 eval 的 export 语句（bash/zsh）。"""
    print(f'export WEIXIN_BASE_URL="{acct.base_url}"')
    print(f'export WEIXIN_TOKEN="{acct.token}"')


def _output_dotenv(acct: WeixinAccount, path: Path) -> None:
    """写入或更新 .env 文件（不覆盖其他键）。"""
    lines: list[str] = []
    if path.exists():
        existing = path.read_text(encoding="utf-8").splitlines()
        lines = [
            l for l in existing
            if not l.startswith("WEIXIN_BASE_URL=") and not l.startswith("WEIXIN_TOKEN=")
        ]

    lines.append(f'WEIXIN_BASE_URL="{acct.base_url}"')
    lines.append(f'WEIXIN_TOKEN="{acct.token}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✅ 已写入 {path}")


def _output_human(acct: WeixinAccount) -> None:
    """人类可读的账号信息输出。"""
    name = acct.nickname or acct.uin or f"account-{acct.index}"
    print(f"\n{'='*50}")
    print(f"  账号：{name}")
    print(f"  Base URL：{acct.base_url}")
    print(f"  Token：{acct.token[:12]}…（已隐藏）")
    print(f"{'='*50}\n")
    print("复制以下内容到你的 .env 或直接设置环境变量：\n")
    print(f"  WEIXIN_BASE_URL={acct.base_url}")
    print(f"  WEIXIN_TOKEN={acct.token}")
    print()


# ---------------------------------------------------------------------------
# Debug: dump raw config
# ---------------------------------------------------------------------------

def _dump_config() -> None:
    try:
        cfg = read_openclaw_config()
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
    except FileNotFoundError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="获取 openclaw-weixin Token 工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--index", "-i", type=int, default=None,
        help="直接选择账号序号（0-based），跳过交互"
    )
    parser.add_argument(
        "--uin", type=str, default=None,
        help="按微信账号 uin 选择"
    )
    parser.add_argument(
        "--login", "-l", action="store_true",
        help="强制重新扫码登录（调用 openclaw channels login）"
    )
    parser.add_argument(
        "--no-restart", action="store_true",
        help="登录后不自动重启 gateway"
    )
    parser.add_argument(
        "--env", "-e", action="store_true",
        help="输出 export VAR=VALUE 形式（可 eval 到 shell）"
    )
    parser.add_argument(
        "--dotenv", "-d", type=str, nargs="?", const=".env", metavar="FILE",
        help="写入 .env 文件（默认 ./.env）"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="只列出所有账号，不做选择"
    )
    parser.add_argument(
        "--dump-config", action="store_true",
        help="打印原始 openclaw.json（调试用）"
    )
    args = parser.parse_args()

    # Debug dump
    if args.dump_config:
        _dump_config()
        return

    # 强制登录
    if args.login:
        try:
            login(restart_gateway=not args.no_restart)
        except FileNotFoundError as e:
            print(f"\n❌ {e}", file=sys.stderr)
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(f"\n❌ 登录失败：{e}", file=sys.stderr)
            sys.exit(1)

    # 读取账号列表
    try:
        accounts = list_accounts()
    except FileNotFoundError:
        print(
            "\n❌ 未找到 openclaw 配置文件。\n\n"
            "请先完成以下步骤：\n"
            "  1. 安装 openclaw：https://docs.openclaw.ai/install\n"
            "  2. 安装插件：npx -y @tencent-weixin/openclaw-weixin-cli install\n"
            "  3. 扫码登录：openclaw channels login --channel openclaw-weixin\n"
            "  4. 重启 gateway：openclaw gateway restart\n"
            "\n或者直接运行：python scripts/get_token.py --login\n",
            file=sys.stderr,
        )
        sys.exit(1)
    except ValueError as e:
        print(f"\n⚠️  {e}\n", file=sys.stderr)
        ans = input("是否现在扫码登录？[Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            try:
                login(restart_gateway=not args.no_restart)
                accounts = list_accounts()
            except Exception as exc:
                print(f"❌ 登录失败：{exc}", file=sys.stderr)
                sys.exit(1)
        else:
            sys.exit(0)

    # 只列出
    if args.list:
        _print_accounts(accounts)
        return

    # 选择账号
    if args.uin is not None:
        try:
            acct = get_account(uin=args.uin)
        except ValueError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
    elif args.index is not None:
        try:
            acct = accounts[args.index]
        except IndexError:
            print(f"❌ 序号 {args.index} 超出范围（共 {len(accounts)} 个账号）", file=sys.stderr)
            sys.exit(1)
    else:
        acct = _interactive_select(accounts)

    # 输出
    if args.env:
        _output_env(acct)
    elif args.dotenv is not None:
        _output_dotenv(acct, Path(args.dotenv))
        _output_human(acct)
    else:
        _output_human(acct)


if __name__ == "__main__":
    import subprocess  # noqa: F811 (already imported above for CalledProcessError)
    main()
