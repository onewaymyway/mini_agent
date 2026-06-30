#!/usr/bin/env python3
"""
examples/claude_code_bot_standalone.py
========================================
完全独立的 Claude Code bot：扫码登录 + 接入 claude CLI，不依赖 openclaw。

前置条件：
    npm install -g @anthropic-ai/claude-code   # 安装 claude CLI

运行：
    python examples/claude_code_bot_standalone.py
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from weixin import WeixinBot, load_or_login
from weixin.handlers.claude_code import ClaudeCodeHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SAVE_PATH = Path.home() / ".weixin-bot" / "account-0.json"


async def main():
    account = load_or_login(save_path=SAVE_PATH)

    bot = WeixinBot(account=account, auto_typing=True)
    bot.add_handler(ClaudeCodeHandler(
        model="claude-sonnet-4-20250514",
        system_prompt="你是一个微信 AI 助手。回复简洁，用中文。",
        max_output_chars=2000,
    ))

    print(f"\n✅ Claude Code Bot 已启动 (Ctrl+C 退出)")
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
