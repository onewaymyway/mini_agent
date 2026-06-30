#!/usr/bin/env python3
"""
examples/login_demo.py
======================
演示独立扫码登录流程（不需要 openclaw）。

运行：
    python examples/login_demo.py

扫码成功后会打印凭证，并保存到 ~/.weixin-bot/account-0.json。
后续再次运行会直接加载已保存的凭证，无需重新扫码。
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path
from weixin.login import load_or_login, qr_login

def main():
    save_path = Path.home() / ".weixin-bot" / "account-0.json"

    print(">>> 尝试加载已保存凭证，若无则触发扫码登录")
    account = load_or_login(save_path=save_path)

    print(f"\n>>> 登录账号信息")
    print(f"    base_url : {account.base_url}")
    print(f"    token    : {account.token[:20]}…")
    print(f"    uin      : {account.uin}")
    print(f"    nickname : {account.nickname}")

if __name__ == "__main__":
    main()
