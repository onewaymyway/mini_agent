"""
examples/claude_code_bot.py
============================
将微信消息转发给 claude CLI 并流式回复。

安装 Claude Code：
    npm install -g @anthropic-ai/claude-code

运行（自动读取 openclaw 凭证）：
    python examples/claude_code_bot.py

也可手动指定：
    WEIXIN_BASE_URL=http://localhost:8080 WEIXIN_TOKEN=your-token \\
        python examples/claude_code_bot.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from weixin import WeixinBot, auto_token
from weixin.handlers.claude_code import ClaudeCodeHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if os.getenv("WEIXIN_TOKEN"):
    base_url = os.getenv("WEIXIN_BASE_URL", "http://localhost:8080")
    token = os.getenv("WEIXIN_TOKEN")
else:
    base_url, token = auto_token()

bot = WeixinBot(base_url=base_url, token=token, auto_typing=True)

bot.add_handler(
    ClaudeCodeHandler(
        model="claude-sonnet-4-20250514",
        system_prompt=(
            "你是一个通过微信对话的助手。"
            "回复要简洁友好，用户用中文就用中文回复。"
        ),
        chunk_size=0,
        max_output_chars=2000,
    )
)

if __name__ == "__main__":
    print(f"Claude Code Bot 已启动，网关：{base_url}")
    asyncio.run(bot.run())
