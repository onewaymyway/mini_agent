"""
weixin.bot
==========
High-level bot framework built on top of weixin.api.

Usage::

    from weixin.bot import WeixinBot

    bot = WeixinBot(base_url="http://localhost:8080", token="your-token")

    @bot.on_text
    async def echo(bot, msg, text):
        await bot.reply_text(msg, f"You said: {text}")

    asyncio.run(bot.run())

Handler signature
-----------------
All handlers receive (bot: WeixinBot, msg: WeixinMessage, **kwargs).
Text handlers additionally receive text: str.
Raw handlers receive the raw WeixinMessage with no extra kwargs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from .api import (
    ILINK_BASE_URL,
    get_config,
    get_updates,
    make_text_message,
    send_message,
    send_typing,
)
from .types import (
    MessageItemType,
    MessageType,
    SendTypingReq,
    TypingStatus,
    WeixinMessage,
)

logger = logging.getLogger(__name__)

# Handler type aliases
RawHandler = Callable[["WeixinBot", WeixinMessage], Awaitable[None]]
TextHandler = Callable[["WeixinBot", WeixinMessage, str], Awaitable[None]]


class WeixinBot:
    """
    Async WeChat bot that long-polls for messages and dispatches to handlers.

    Parameters
    ----------
    base_url:
        iLink server base URL.  Defaults to ``https://ilinkai.weixin.qq.com``
        (Tencent's official server).  Pass a local openclaw gateway URL when
        running behind a proxy.
    token:
        Bearer token from QR login (:func:`weixin.login.qr_login`).
    account:
        A :class:`weixin.login.LoginAccount` returned by ``qr_login()``.
        When provided, *base_url* and *token* are derived from it automatically.
    poll_timeout_ms:
        How long each long-poll request waits for messages. Default 35 s.
    retry_delay_s:
        Seconds to wait before retrying after an error. Default 2 s.
    auto_typing:
        If True, automatically send "typing…" indicators while processing. Default True.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        account=None,            # LoginAccount (typed loosely to avoid circular import)
        poll_timeout_ms: int = 35_000,
        retry_delay_s: float = 2.0,
        auto_typing: bool = True,
    ) -> None:
        if account is not None:
            self.base_url = account.base_url
            self.token = account.token
        else:
            self.base_url = (base_url or ILINK_BASE_URL).rstrip("/")
            self.token = token
        self.poll_timeout_ms = poll_timeout_ms
        self.retry_delay_s = retry_delay_s
        self.auto_typing = auto_typing

        self._text_handlers: list[TextHandler] = []
        self._raw_handlers: list[RawHandler] = []
        self._get_updates_buf: str = ""
        self._running = False

    # ------------------------------------------------------------------
    # Decorator registration API
    # ------------------------------------------------------------------

    def on_text(self, fn: TextHandler) -> TextHandler:
        """Register a handler for incoming text messages."""
        self._text_handlers.append(fn)
        return fn

    def on_message(self, fn: RawHandler) -> RawHandler:
        """Register a handler for ALL incoming messages (raw)."""
        self._raw_handlers.append(fn)
        return fn

    def add_handler(self, handler: "BaseHandler") -> None:
        """Register a BaseHandler subclass instance."""
        handler.register(self)

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    async def reply_text(self, msg: WeixinMessage, text: str) -> None:
        """Send a text reply to the sender of *msg*."""
        reply = make_text_message(
            to_user_id=msg.from_user_id,
            text=text,
            context_token=msg.context_token,
        )
        await send_message(base_url=self.base_url, token=self.token, msg=reply)

    async def send_text(
        self, to_user_id: str, text: str, context_token: Optional[str] = None
    ) -> None:
        """Send a text message to an arbitrary user."""
        msg = make_text_message(to_user_id=to_user_id, text=text, context_token=context_token)
        await send_message(base_url=self.base_url, token=self.token, msg=msg)

    async def start_typing(self, user_id: str) -> None:
        """Send the 'typing…' indicator to a user."""
        try:
            cfg = await get_config(
                base_url=self.base_url, token=self.token, ilink_user_id=user_id
            )
            if cfg.typing_ticket:
                await send_typing(
                    base_url=self.base_url,
                    token=self.token,
                    req=SendTypingReq(
                        ilink_user_id=user_id,
                        typing_ticket=cfg.typing_ticket,
                        status=TypingStatus.TYPING,
                    ),
                )
        except Exception as exc:
            logger.debug("start_typing failed (non-fatal): %s", exc)

    async def stop_typing(self, user_id: str) -> None:
        """Cancel the 'typing…' indicator for a user."""
        try:
            cfg = await get_config(
                base_url=self.base_url, token=self.token, ilink_user_id=user_id
            )
            if cfg.typing_ticket:
                await send_typing(
                    base_url=self.base_url,
                    token=self.token,
                    req=SendTypingReq(
                        ilink_user_id=user_id,
                        typing_ticket=cfg.typing_ticket,
                        status=TypingStatus.CANCEL,
                    ),
                )
        except Exception as exc:
            logger.debug("stop_typing failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, msg: WeixinMessage) -> None:
        # Skip messages the bot itself sent
        if msg.message_type == MessageType.BOT:
            return

        # Raw handlers first
        for handler in self._raw_handlers:
            try:
                await handler(self, msg)
            except Exception:
                logger.exception("Error in raw handler %s", handler)

        # Text handlers
        text = msg.text()
        if text is not None and self._text_handlers:
            if self.auto_typing and msg.from_user_id:
                asyncio.create_task(self.start_typing(msg.from_user_id))
            for handler in self._text_handlers:
                try:
                    await handler(self, msg, text)
                except Exception:
                    logger.exception("Error in text handler %s", handler)
            if self.auto_typing and msg.from_user_id:
                asyncio.create_task(self.stop_typing(msg.from_user_id))

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def poll_once(self) -> None:
        """Run a single long-poll cycle and dispatch any received messages."""
        resp = await get_updates(
            base_url=self.base_url,
            token=self.token,
            get_updates_buf=self._get_updates_buf,
            timeout_ms=self.poll_timeout_ms,
        )

        # -14 = session timeout; reset cursor
        if resp.errcode == -14:
            logger.warning("Session timeout (errcode=-14), resetting cursor.")
            self._get_updates_buf = ""
            return

        if resp.get_updates_buf:
            self._get_updates_buf = resp.get_updates_buf

        for msg in resp.msgs or []:
            asyncio.create_task(self._dispatch(msg))

    async def run(self) -> None:
        """Start the polling loop. Runs until cancelled."""
        self._running = True
        logger.info("WeixinBot started, polling %s", self.base_url)
        while self._running:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Polling error: %s — retrying in %.1fs", exc, self.retry_delay_s)
                await asyncio.sleep(self.retry_delay_s)

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False


# ---------------------------------------------------------------------------
# BaseHandler — extensible handler class (for Claude Code integration etc.)
# ---------------------------------------------------------------------------

class BaseHandler:
    """
    Subclass this to bundle related handlers together and plug them into a bot.

    Example::

        class EchoHandler(BaseHandler):
            async def on_text(self, bot, msg, text):
                await bot.reply_text(msg, text)
    """

    def register(self, bot: WeixinBot) -> None:
        if hasattr(self, "on_text"):
            bot.on_text(self.on_text)  # type: ignore[arg-type]
        if hasattr(self, "on_message"):
            bot.on_message(self.on_message)  # type: ignore[arg-type]
