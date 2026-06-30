"""
weixin — Python library for the WeChat iLink Bot protocol.

独立实现，不依赖 openclaw CLI。直接调用腾讯官方接口：
  https://ilinkai.weixin.qq.com

快速上手
--------
    import asyncio
    from weixin import WeixinBot
    from weixin.login import qr_login

    account = qr_login(save_path=Path("~/.weixin-bot/account.json").expanduser())

    bot = WeixinBot(account=account)

    @bot.on_text
    async def echo(bot, msg, text):
        await bot.reply_text(msg, f"收到：{text}")

    asyncio.run(bot.run())
"""

# --- 登录模块（核心新增） ---
from .login import (
    LoginAccount,
    load_account,
    load_all_accounts,
    load_or_login,
    qr_login,
    qr_login_async,
    save_account,
    save_account_indexed,
)

# --- 遗留 openclaw-config 读取（可选，有 openclaw 时才有用） ---
from .auth import (
    WeixinAccount,
    auto_token,
    get_account,
    list_accounts,
    read_openclaw_config,
)

from .api import (
    ILINK_BASE_URL,
    WeixinAPIError,
    get_config,
    get_updates,
    get_upload_url,
    make_text_message,
    send_message,
    send_typing,
)
from .bot import BaseHandler, WeixinBot
from .types import (
    CDNMedia,
    FileItem,
    GetConfigResp,
    GetUpdatesReq,
    GetUpdatesResp,
    GetUploadUrlReq,
    GetUploadUrlResp,
    ImageItem,
    MessageItem,
    MessageItemType,
    MessageState,
    MessageType,
    SendMessageReq,
    SendTypingReq,
    SendTypingResp,
    TextItem,
    TypingStatus,
    UploadMediaType,
    VideoItem,
    VoiceEncodeType,
    VoiceItem,
    WeixinMessage,
)

__all__ = [
    # --- Login (independent QR flow) ---
    "qr_login",
    "qr_login_async",
    "load_or_login",
    "load_account",
    "save_account",
    "load_all_accounts",
    "save_account_indexed",
    "LoginAccount",
    "ILINK_BASE_URL",
    # --- Legacy openclaw config reader ---
    "auto_token",
    "list_accounts",
    "get_account",
    "read_openclaw_config",
    "WeixinAccount",
    # --- Bot ---
    "WeixinBot",
    "BaseHandler",
    # --- API functions ---
    "get_updates",
    "send_message",
    "get_upload_url",
    "get_config",
    "send_typing",
    "make_text_message",
    "WeixinAPIError",
    # --- Types ---
    "WeixinMessage",
    "MessageItem",
    "TextItem",
    "ImageItem",
    "VoiceItem",
    "FileItem",
    "VideoItem",
    "CDNMedia",
    "MessageType",
    "MessageItemType",
    "MessageState",
    "TypingStatus",
    "UploadMediaType",
    "VoiceEncodeType",
    "GetUpdatesReq",
    "GetUpdatesResp",
    "SendMessageReq",
    "GetUploadUrlReq",
    "GetUploadUrlResp",
    "GetConfigResp",
    "SendTypingReq",
    "SendTypingResp",
]
