"""
weixin.types
============
Python dataclass equivalents of the openclaw-weixin TypeScript protocol types.
All bytes-like fields are represented as plain str (base64) matching the JSON wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UploadMediaType(IntEnum):
    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4


class MessageType(IntEnum):
    NONE = 0
    USER = 1
    BOT = 2


class MessageItemType(IntEnum):
    NONE = 0
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class MessageState(IntEnum):
    NEW = 0
    GENERATING = 1
    FINISH = 2


class TypingStatus(IntEnum):
    TYPING = 1
    CANCEL = 2


class VoiceEncodeType(IntEnum):
    PCM = 1
    ADPCM = 2
    FEATURE = 3
    SPEEX = 4
    AMR = 5
    SILK = 6
    MP3 = 7
    OGG_SPEEX = 8


# ---------------------------------------------------------------------------
# CDN / Media
# ---------------------------------------------------------------------------

@dataclass
class CDNMedia:
    """CDN media reference; aes_key is base64-encoded bytes."""
    encrypt_query_param: Optional[str] = None
    aes_key: Optional[str] = None
    encrypt_type: Optional[int] = None   # 0=只加密fileid, 1=打包缩略图信息
    full_url: Optional[str] = None       # 完整下载 URL（服务端直接返回）


# ---------------------------------------------------------------------------
# Message item subtypes
# ---------------------------------------------------------------------------

@dataclass
class TextItem:
    text: Optional[str] = None


@dataclass
class ImageItem:
    media: Optional[CDNMedia] = None
    thumb_media: Optional[CDNMedia] = None
    aeskey: Optional[str] = None         # Raw AES-128 key as hex (preferred for inbound)
    url: Optional[str] = None
    mid_size: Optional[int] = None
    thumb_size: Optional[int] = None
    thumb_height: Optional[int] = None
    thumb_width: Optional[int] = None
    hd_size: Optional[int] = None


@dataclass
class VoiceItem:
    media: Optional[CDNMedia] = None
    encode_type: Optional[int] = None   # VoiceEncodeType
    bits_per_sample: Optional[int] = None
    sample_rate: Optional[int] = None   # Hz
    playtime: Optional[int] = None      # ms
    text: Optional[str] = None          # 语音转文字


@dataclass
class FileItem:
    media: Optional[CDNMedia] = None
    file_name: Optional[str] = None
    md5: Optional[str] = None
    len: Optional[str] = None


@dataclass
class VideoItem:
    media: Optional[CDNMedia] = None
    video_size: Optional[int] = None
    play_length: Optional[int] = None
    video_md5: Optional[str] = None
    thumb_media: Optional[CDNMedia] = None
    thumb_size: Optional[int] = None
    thumb_height: Optional[int] = None
    thumb_width: Optional[int] = None


@dataclass
class RefMessage:
    message_item: Optional["MessageItem"] = None
    title: Optional[str] = None          # 摘要


@dataclass
class MessageItem:
    type: Optional[int] = None           # MessageItemType
    create_time_ms: Optional[int] = None
    update_time_ms: Optional[int] = None
    is_completed: Optional[bool] = None
    msg_id: Optional[str] = None
    ref_msg: Optional[RefMessage] = None
    text_item: Optional[TextItem] = None
    image_item: Optional[ImageItem] = None
    voice_item: Optional[VoiceItem] = None
    file_item: Optional[FileItem] = None
    video_item: Optional[VideoItem] = None


# ---------------------------------------------------------------------------
# Top-level message
# ---------------------------------------------------------------------------

@dataclass
class WeixinMessage:
    """Unified message (proto: WeixinMessage)."""
    seq: Optional[int] = None
    message_id: Optional[int] = None
    from_user_id: Optional[str] = None
    to_user_id: Optional[str] = None
    client_id: Optional[str] = None
    create_time_ms: Optional[int] = None
    update_time_ms: Optional[int] = None
    delete_time_ms: Optional[int] = None
    session_id: Optional[str] = None
    group_id: Optional[str] = None
    message_type: Optional[int] = None   # MessageType
    message_state: Optional[int] = None  # MessageState
    item_list: list[MessageItem] = field(default_factory=list)
    context_token: Optional[str] = None

    # --- convenience helpers ---

    def is_from_user(self) -> bool:
        return self.message_type == MessageType.USER

    def text(self) -> Optional[str]:
        """Return the concatenated text from all TEXT items, or None."""
        parts = [
            item.text_item.text
            for item in (self.item_list or [])
            if item.type == MessageItemType.TEXT
            and item.text_item
            and item.text_item.text
        ]
        return "".join(parts) if parts else None


# ---------------------------------------------------------------------------
# API request / response types
# ---------------------------------------------------------------------------

@dataclass
class GetUpdatesReq:
    get_updates_buf: str = ""


@dataclass
class GetUpdatesResp:
    ret: int = 0
    errcode: Optional[int] = None
    errmsg: Optional[str] = None
    msgs: list[WeixinMessage] = field(default_factory=list)
    get_updates_buf: str = ""
    longpolling_timeout_ms: Optional[int] = None


@dataclass
class SendMessageReq:
    msg: Optional[WeixinMessage] = None


@dataclass
class GetUploadUrlReq:
    filekey: Optional[str] = None
    media_type: Optional[int] = None     # UploadMediaType
    to_user_id: Optional[str] = None
    rawsize: Optional[int] = None
    rawfilemd5: Optional[str] = None
    filesize: Optional[int] = None
    thumb_rawsize: Optional[int] = None
    thumb_rawfilemd5: Optional[str] = None
    thumb_filesize: Optional[int] = None
    no_need_thumb: Optional[bool] = None
    aeskey: Optional[str] = None


@dataclass
class GetUploadUrlResp:
    upload_param: Optional[str] = None
    thumb_upload_param: Optional[str] = None
    upload_full_url: Optional[str] = None


@dataclass
class SendTypingReq:
    ilink_user_id: Optional[str] = None
    typing_ticket: Optional[str] = None
    status: int = TypingStatus.TYPING


@dataclass
class SendTypingResp:
    ret: Optional[int] = None
    errmsg: Optional[str] = None


@dataclass
class GetConfigResp:
    ret: Optional[int] = None
    errmsg: Optional[str] = None
    typing_ticket: Optional[str] = None
