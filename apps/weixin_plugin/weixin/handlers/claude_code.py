"""
weixin.handlers.claude_code
============================
A BaseHandler that routes WeChat text messages to Claude Code (claude CLI)
and streams the response back to the sender.

Requirements
------------
- ``claude`` CLI must be on PATH (installed via ``npm install -g @anthropic-ai/claude-code``)
- The bot account must have sufficient permissions.

Usage::

    from weixin.bot import WeixinBot
    from weixin.handlers.claude_code import ClaudeCodeHandler

    bot = WeixinBot(base_url="http://localhost:8080", token="your-token")
    bot.add_handler(ClaudeCodeHandler(
        system_prompt="You are a helpful WeChat assistant.",
        chunk_size=300,          # characters per streaming message
        max_output_chars=4000,   # cap total reply length
    ))
    asyncio.run(bot.run())
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Optional

from ..bot import BaseHandler, WeixinBot
from ..types import WeixinMessage

logger = logging.getLogger(__name__)


class ClaudeCodeHandler(BaseHandler):
    """
    Streams ``claude`` CLI output back to WeChat as the model generates it.

    Parameters
    ----------
    model:
        Claude model string, e.g. ``claude-sonnet-4-20250514``.
    system_prompt:
        System prompt forwarded to ``claude --system``.
    chunk_size:
        Approximate characters per partial message. 0 = send only the final reply.
    max_output_chars:
        Hard cap on total reply characters (truncated with a notice).
    cli_path:
        Path to the ``claude`` binary. Auto-detected from PATH if None.
    working_dir:
        Working directory for the claude process. Defaults to CWD.
    extra_args:
        Extra CLI arguments appended to every ``claude`` invocation.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        system_prompt: Optional[str] = None,
        chunk_size: int = 0,
        max_output_chars: int = 4000,
        cli_path: Optional[str] = None,
        working_dir: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> None:
        self.model = model
        self.system_prompt = system_prompt
        self.chunk_size = chunk_size
        self.max_output_chars = max_output_chars
        self.cli_path = cli_path or shutil.which("claude") or "claude"
        self.working_dir = working_dir or os.getcwd()
        self.extra_args = extra_args or []

    def _build_cmd(self, prompt: str) -> list[str]:
        cmd = [self.cli_path, "--print", "--model", self.model]
        if self.system_prompt:
            cmd += ["--system", self.system_prompt]
        cmd += self.extra_args
        cmd += ["--", prompt]
        return cmd

    async def on_text(self, bot: WeixinBot, msg: WeixinMessage, text: str) -> None:
        if not msg.from_user_id:
            return

        cmd = self._build_cmd(text)
        logger.info("Running claude for user %s: %r", msg.from_user_id, text[:80])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
        except FileNotFoundError:
            logger.error("claude CLI not found at %r", self.cli_path)
            await bot.reply_text(msg, "⚠️ Claude Code CLI not found. Please install it first.")
            return

        accumulated = ""
        last_sent_len = 0

        assert proc.stdout is not None

        while True:
            chunk = await proc.stdout.read(256)
            if not chunk:
                break
            accumulated += chunk.decode(errors="replace")

            # Truncate if needed
            if len(accumulated) > self.max_output_chars:
                accumulated = accumulated[: self.max_output_chars]
                accumulated += "\n\n…（回复过长，已截断）"
                proc.kill()
                break

            # Streaming: send partial updates
            if self.chunk_size > 0:
                unsent = len(accumulated) - last_sent_len
                if unsent >= self.chunk_size:
                    await bot.reply_text(msg, accumulated)
                    last_sent_len = len(accumulated)

        await proc.wait()

        stderr_output = b""
        if proc.stderr:
            stderr_output = await proc.stderr.read()

        if proc.returncode != 0 and not accumulated.strip():
            err = stderr_output.decode(errors="replace").strip()
            logger.warning("claude exited %d: %s", proc.returncode, err[:200])
            await bot.reply_text(msg, f"⚠️ Claude Code 出错 (exit {proc.returncode}): {err[:300]}")
            return

        # Send final / only reply
        if accumulated.strip():
            if self.chunk_size == 0 or len(accumulated) > last_sent_len:
                await bot.reply_text(msg, accumulated.strip())
        else:
            await bot.reply_text(msg, "（Claude Code 未返回内容）")
