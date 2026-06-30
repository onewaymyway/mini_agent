"""
weixin.api
==========
Async HTTP client mirroring the openclaw-weixin api.ts functions.
Uses only the standard library (urllib / asyncio) or httpx when available.

All public functions are coroutines and raise WeixinAPIError on non-2xx.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import struct
from typing import Any, Optional

from .codec import (
    decode_get_config_resp,
    decode_get_updates_resp,
    decode_get_upload_url_resp,
    decode_send_typing_resp,
    encode_weixin_message,
)
from .types import (
    GetConfigResp,
    GetUpdatesReq,
    GetUpdatesResp,
    GetUploadUrlReq,
    GetUploadUrlResp,
    SendMessageReq,
    SendTypingReq,
    SendTypingResp,
    WeixinMessage,
    MessageType,
    MessageState,
    MessageItemType,
    TextItem,
    MessageItem,
    TypingStatus,
)

logger = logging.getLogger(__name__)

# 腾讯 iLink 官方服务器（无需 openclaw gateway 中转）
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"

DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
DEFAULT_API_TIMEOUT_MS = 15_000
DEFAULT_CONFIG_TIMEOUT_MS = 10_000


class WeixinAPIError(Exception):
    """Raised when the Weixin API returns an error status or error body."""
    def __init__(self, message: str, status: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.body = body


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

def _random_wechat_uin() -> str:
    """X-WECHAT-UIN: random uint32 -> decimal string -> base64."""
    uint32 = random.randint(0, 0xFFFF_FFFF)
    return base64.b64encode(str(uint32).encode()).decode()


def _build_headers(token: Optional[str], body: str) -> dict[str, str]:
    hdrs: dict[str, str] = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode())),
        "X-WECHAT-UIN": _random_wechat_uin(),
    }
    if token:
        hdrs["Authorization"] = f"Bearer {token.strip()}"
    return hdrs


def _ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def _build_base_info() -> dict:
    return {"channel_version": "python-weixin/1.0.0"}


# ---------------------------------------------------------------------------
# HTTP transport (httpx preferred, falls back to aiohttp, then urllib)
# ---------------------------------------------------------------------------

async def _post(url: str, body: str, headers: dict, timeout_s: float) -> str:
    """Send a POST request and return the response text. Raises WeixinAPIError on failure."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, content=body.encode(), headers=headers)
            text = resp.text
            logger.debug("POST %s -> %d", url, resp.status_code)
            if resp.status_code >= 400:
                raise WeixinAPIError(f"HTTP {resp.status_code}", status=resp.status_code, body=text)
            return text
    except ImportError:
        pass

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data=body.encode(), headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_s)
            ) as resp:
                text = await resp.text()
                logger.debug("POST %s -> %d", url, resp.status)
                if resp.status >= 400:
                    raise WeixinAPIError(f"HTTP {resp.status}", status=resp.status, body=text)
                return text
    except ImportError:
        pass

    # stdlib fallback (synchronous, run in thread pool)
    import urllib.request
    req = urllib.request.Request(url, data=body.encode(), headers=headers, method="POST")
    loop = asyncio.get_event_loop()

    def _sync_post():
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as e:
            body_bytes = e.read()
            raise WeixinAPIError(
                f"HTTP {e.code}", status=e.code, body=body_bytes.decode(errors="replace")
            ) from e

    return await loop.run_in_executor(None, _sync_post)


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

async def get_updates(
    *,
    base_url: str,
    token: Optional[str] = None,
    get_updates_buf: str = "",
    timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS,
) -> GetUpdatesResp:
    """
    Long-poll for new messages. On client-side timeout returns an empty response
    with ret=0 so the caller can simply retry (normal long-poll behaviour).
    """
    url = _ensure_trailing_slash(base_url) + "ilink/bot/getupdates"
    body = json.dumps({
        "get_updates_buf": get_updates_buf,
        "base_info": _build_base_info(),
    })
    hdrs = _build_headers(token, body)
    timeout_s = timeout_ms / 1000

    try:
        raw = await asyncio.wait_for(
            _post(url, body, hdrs, timeout_s),
            timeout=timeout_s + 5,   # extra margin over HTTP-level timeout
        )
        return decode_get_updates_resp(raw)
    except (asyncio.TimeoutError, TimeoutError):
        logger.debug("getUpdates: client-side timeout after %dms, returning empty", timeout_ms)
        return GetUpdatesResp(ret=0, msgs=[], get_updates_buf=get_updates_buf)


async def send_message(
    *,
    base_url: str,
    token: Optional[str] = None,
    msg: WeixinMessage,
    timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
) -> None:
    """Send a single message downstream."""
    url = _ensure_trailing_slash(base_url) + "ilink/bot/sendmessage"
    payload = {
        "msg": encode_weixin_message(msg),
        "base_info": _build_base_info(),
    }
    body = json.dumps(payload)
    hdrs = _build_headers(token, body)
    await _post(url, body, hdrs, timeout_ms / 1000)


async def get_upload_url(
    *,
    base_url: str,
    token: Optional[str] = None,
    req: GetUploadUrlReq,
    timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
) -> GetUploadUrlResp:
    """Get a pre-signed CDN upload URL for a file."""
    url = _ensure_trailing_slash(base_url) + "ilink/bot/getuploadurl"
    body = json.dumps({
        k: v for k, v in {
            "filekey": req.filekey,
            "media_type": req.media_type,
            "to_user_id": req.to_user_id,
            "rawsize": req.rawsize,
            "rawfilemd5": req.rawfilemd5,
            "filesize": req.filesize,
            "thumb_rawsize": req.thumb_rawsize,
            "thumb_rawfilemd5": req.thumb_rawfilemd5,
            "thumb_filesize": req.thumb_filesize,
            "no_need_thumb": req.no_need_thumb,
            "aeskey": req.aeskey,
            "base_info": _build_base_info(),
        }.items() if v is not None
    })
    hdrs = _build_headers(token, body)
    raw = await _post(url, body, hdrs, timeout_ms / 1000)
    return decode_get_upload_url_resp(raw)


async def get_config(
    *,
    base_url: str,
    token: Optional[str] = None,
    ilink_user_id: str,
    context_token: Optional[str] = None,
    timeout_ms: int = DEFAULT_CONFIG_TIMEOUT_MS,
) -> GetConfigResp:
    """Fetch bot config (includes typing_ticket) for a given user."""
    url = _ensure_trailing_slash(base_url) + "ilink/bot/getconfig"
    body = json.dumps({k: v for k, v in {
        "ilink_user_id": ilink_user_id,
        "context_token": context_token,
        "base_info": _build_base_info(),
    }.items() if v is not None})
    hdrs = _build_headers(token, body)
    raw = await _post(url, body, hdrs, timeout_ms / 1000)
    return decode_get_config_resp(raw)


async def send_typing(
    *,
    base_url: str,
    token: Optional[str] = None,
    req: SendTypingReq,
    timeout_ms: int = DEFAULT_CONFIG_TIMEOUT_MS,
) -> SendTypingResp:
    """Send or cancel the typing status indicator."""
    url = _ensure_trailing_slash(base_url) + "ilink/bot/sendtyping"
    body = json.dumps({k: v for k, v in {
        "ilink_user_id": req.ilink_user_id,
        "typing_ticket": req.typing_ticket,
        "status": req.status,
        "base_info": _build_base_info(),
    }.items() if v is not None})
    hdrs = _build_headers(token, body)
    raw = await _post(url, body, hdrs, timeout_ms / 1000)
    return decode_send_typing_resp(raw)


# ---------------------------------------------------------------------------
# Convenience builder: build a simple text reply
# ---------------------------------------------------------------------------

def make_text_message(
    to_user_id: str,
    text: str,
    context_token: Optional[str] = None,
) -> WeixinMessage:
    """Build a WeixinMessage containing a single text item."""
    return WeixinMessage(
        to_user_id=to_user_id,
        message_type=MessageType.BOT,
        message_state=MessageState.FINISH,
        context_token=context_token,
        item_list=[
            MessageItem(
                type=MessageItemType.TEXT,
                text_item=TextItem(text=text),
            )
        ],
    )
