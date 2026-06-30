"""
examples/echo_bot.py
====================
最简 echo bot，展示自动从 ~/.weixin-bot/account.json 读取凭证，
或引导扫码登录（使用 weixin.login 模块）。

运行：
    python examples/echo_bot.py

首次运行无账号时会自动提示扫码登录。
"""

import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from weixin import WeixinBot
from weixin.login import load_or_login

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# 优先读环境变量，否则从本地配置文件自动获取或引导扫码登录
if os.getenv("WEIXIN_TOKEN"):
    base_url = os.getenv("WEIXIN_BASE_URL", "http://localhost:8080")
    token = os.getenv("WEIXIN_TOKEN")
else:
    account = load_or_login()  # 自动读取 ~/.weixin-bot/account.json 或引导扫码
    base_url = account.base_url
    token = account.token

bot = WeixinBot(base_url=base_url, token=token)


@bot.on_text
async def echo(bot: WeixinBot, msg, text: str) -> None:
    print(f"[{msg.from_user_id}] {text}")
    await bot.reply_text(msg, f"🔁 {text}")


if __name__ == "__main__":
    asyncio.run(bot.run())
