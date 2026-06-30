"""
examples/advanced_bot.py
=========================
Demonstrates multiple handlers, raw message access, and command routing.
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from weixin import WeixinBot, WeixinMessage, MessageItemType
from weixin.bot import BaseHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_URL = os.getenv("WEIXIN_BASE_URL", "http://localhost:8080")
TOKEN = os.getenv("WEIXIN_TOKEN", "")

bot = WeixinBot(base_url=BASE_URL, token=TOKEN or None)


# --- Raw handler: log every incoming message ---

@bot.on_message
async def log_all(bot: WeixinBot, msg: WeixinMessage) -> None:
    types = [item.type for item in msg.item_list]
    print(f"MSG from={msg.from_user_id} types={types} state={msg.message_state}")


# --- Text command routing ---

@bot.on_text
async def router(bot: WeixinBot, msg: WeixinMessage, text: str) -> None:
    text = text.strip()

    if text.startswith("/help"):
        await bot.reply_text(msg, (
            "📖 Commands:\n"
            "/help — show this message\n"
            "/ping — check if bot is alive\n"
            "/upper <text> — convert to uppercase\n"
            "Anything else → echoed back"
        ))

    elif text.startswith("/ping"):
        await bot.reply_text(msg, "🏓 Pong!")

    elif text.startswith("/upper "):
        content = text[7:]
        await bot.reply_text(msg, content.upper())

    else:
        await bot.reply_text(msg, f"Echo: {text}")


# --- BaseHandler subclass example ---

class ImageNotifier(BaseHandler):
    """Acknowledge image messages."""

    async def on_message(self, bot: WeixinBot, msg: WeixinMessage) -> None:
        for item in msg.item_list:
            if item.type == MessageItemType.IMAGE:
                await bot.reply_text(msg, "📷 收到图片！（暂不处理）")
                return


bot.add_handler(ImageNotifier())


if __name__ == "__main__":
    asyncio.run(bot.run())
