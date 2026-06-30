#!/usr/bin/env python3
"""
examples/echo_bot_standalone.py
================================
完全独立的 echo bot：扫码登录 + 长轮询收消息 + 回复。
不依赖 openclaw，直接调用 ilinkai.weixin.qq.com。

运行：
    python examples/echo_bot_standalone.py

首次运行：终端渲染二维码 → 微信扫码 → 手机确认 → 开始收消息
再次运行：自动读取 ~/.weixin-bot/account-0.json，跳过扫码
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from weixin import WeixinBot, load_or_login

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SAVE_PATH = Path.home() / ".weixin-bot" / "account-0.json"


async def main():
    # 1. 登录（首次扫码，之后自动读缓存）
    account = load_or_login(save_path=SAVE_PATH)

    # 2. 创建 bot（直接传入 account，无需手动填 base_url/token）
    bot = WeixinBot(account=account)

    # 3. 注册消息处理器
    @bot.on_text
    async def echo(bot: WeixinBot, msg, text: str):
        print(f"[{msg.from_user_id}] {text!r}")
        await bot.reply_text(msg, f"🔁 {text}")

    # 4. 启动轮询
    print(f"\n✅ Bot 已启动，监听消息中… (Ctrl+C 退出)")
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
