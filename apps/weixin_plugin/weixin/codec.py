"""
weixin.codec
============
Lightweight JSON <-> dataclass conversion that mirrors the openclaw-weixin
wire format (snake_case JSON, optional fields omitted, bytes as base64 str).
"""

from __future__ import annotations

import json
from typing import Any

from .types import (
    CDNMedia,
    FileItem,
    GetConfigResp,
    GetUpdatesResp,
    GetUploadUrlResp,
    ImageItem,
    MessageItem,
    RefMessage,
    SendTypingResp,
    TextItem,
    VideoItem,
    VoiceItem,
    WeixinMessage,
)


# ---------------------------------------------------------------------------
# Decode helpers  (JSON dict -> dataclass)
# ---------------------------------------------------------------------------

def _opt(d: dict, key: str, default=None):
    return d.get(key, default)


def decode_cdn_media(d: dict | None) -> CDNMedia | None:
    if not d:
        return None
    return CDNMedia(
        encrypt_query_param=_opt(d, "encrypt_query_param"),
        aes_key=_opt(d, "aes_key"),
        encrypt_type=_opt(d, "encrypt_type"),
        full_url=_opt(d, "full_url"),
    )


def decode_text_item(d: dict | None) -> TextItem | None:
    if not d:
        return None
    return TextItem(text=_opt(d, "text"))


def decode_image_item(d: dict | None) -> ImageItem | None:
    if not d:
        return None
    return ImageItem(
        media=decode_cdn_media(_opt(d, "media")),
        thumb_media=decode_cdn_media(_opt(d, "thumb_media")),
        aeskey=_opt(d, "aeskey"),
        url=_opt(d, "url"),
        mid_size=_opt(d, "mid_size"),
        thumb_size=_opt(d, "thumb_size"),
        thumb_height=_opt(d, "thumb_height"),
        thumb_width=_opt(d, "thumb_width"),
        hd_size=_opt(d, "hd_size"),
    )


def decode_voice_item(d: dict | None) -> VoiceItem | None:
    if not d:
        return None
    return VoiceItem(
        media=decode_cdn_media(_opt(d, "media")),
        encode_type=_opt(d, "encode_type"),
        bits_per_sample=_opt(d, "bits_per_sample"),
        sample_rate=_opt(d, "sample_rate"),
        playtime=_opt(d, "playtime"),
        text=_opt(d, "text"),
    )


def decode_file_item(d: dict | None) -> FileItem | None:
    if not d:
        return None
    return FileItem(
        media=decode_cdn_media(_opt(d, "media")),
        file_name=_opt(d, "file_name"),
        md5=_opt(d, "md5"),
        len=_opt(d, "len"),
    )


def decode_video_item(d: dict | None) -> VideoItem | None:
    if not d:
        return None
    return VideoItem(
        media=decode_cdn_media(_opt(d, "media")),
        video_size=_opt(d, "video_size"),
        play_length=_opt(d, "play_length"),
        video_md5=_opt(d, "video_md5"),
        thumb_media=decode_cdn_media(_opt(d, "thumb_media")),
        thumb_size=_opt(d, "thumb_size"),
        thumb_height=_opt(d, "thumb_height"),
        thumb_width=_opt(d, "thumb_width"),
    )


def decode_message_item(d: dict | None) -> MessageItem | None:
    if not d:
        return None
    ref_raw = _opt(d, "ref_msg")
    ref_msg: RefMessage | None = None
    if ref_raw:
        ref_msg = RefMessage(
            message_item=decode_message_item(_opt(ref_raw, "message_item")),
            title=_opt(ref_raw, "title"),
        )
    return MessageItem(
        type=_opt(d, "type"),
        create_time_ms=_opt(d, "create_time_ms"),
        update_time_ms=_opt(d, "update_time_ms"),
        is_completed=_opt(d, "is_completed"),
        msg_id=_opt(d, "msg_id"),
        ref_msg=ref_msg,
        text_item=decode_text_item(_opt(d, "text_item")),
        image_item=decode_image_item(_opt(d, "image_item")),
        voice_item=decode_voice_item(_opt(d, "voice_item")),
        file_item=decode_file_item(_opt(d, "file_item")),
        video_item=decode_video_item(_opt(d, "video_item")),
    )


def decode_weixin_message(d: dict) -> WeixinMessage:
    return WeixinMessage(
        seq=_opt(d, "seq"),
        message_id=_opt(d, "message_id"),
        from_user_id=_opt(d, "from_user_id"),
        to_user_id=_opt(d, "to_user_id"),
        client_id=_opt(d, "client_id"),
        create_time_ms=_opt(d, "create_time_ms"),
        update_time_ms=_opt(d, "update_time_ms"),
        delete_time_ms=_opt(d, "delete_time_ms"),
        session_id=_opt(d, "session_id"),
        group_id=_opt(d, "group_id"),
        message_type=_opt(d, "message_type"),
        message_state=_opt(d, "message_state"),
        item_list=[decode_message_item(i) for i in (_opt(d, "item_list") or []) if i],
        context_token=_opt(d, "context_token"),
    )


def decode_get_updates_resp(raw: str) -> GetUpdatesResp:
    d: dict = json.loads(raw)
    return GetUpdatesResp(
        ret=_opt(d, "ret", 0),
        errcode=_opt(d, "errcode"),
        errmsg=_opt(d, "errmsg"),
        msgs=[decode_weixin_message(m) for m in (_opt(d, "msgs") or [])],
        get_updates_buf=_opt(d, "get_updates_buf", ""),
        longpolling_timeout_ms=_opt(d, "longpolling_timeout_ms"),
    )


def decode_get_upload_url_resp(raw: str) -> GetUploadUrlResp:
    d: dict = json.loads(raw)
    return GetUploadUrlResp(
        upload_param=_opt(d, "upload_param"),
        thumb_upload_param=_opt(d, "thumb_upload_param"),
        upload_full_url=_opt(d, "upload_full_url"),
    )


def decode_get_config_resp(raw: str) -> GetConfigResp:
    d: dict = json.loads(raw)
    return GetConfigResp(
        ret=_opt(d, "ret"),
        errmsg=_opt(d, "errmsg"),
        typing_ticket=_opt(d, "typing_ticket"),
    )


def decode_send_typing_resp(raw: str) -> SendTypingResp:
    d: dict = json.loads(raw)
    return SendTypingResp(ret=_opt(d, "ret"), errmsg=_opt(d, "errmsg"))


# ---------------------------------------------------------------------------
# Encode helpers  (dataclass -> JSON-serialisable dict)
# ---------------------------------------------------------------------------

def _strip_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def encode_cdn_media(m: CDNMedia | None) -> dict | None:
    if not m:
        return None
    return _strip_none({
        "encrypt_query_param": m.encrypt_query_param,
        "aes_key": m.aes_key,
        "encrypt_type": m.encrypt_type,
        "full_url": m.full_url,
    })


def encode_message_item(item: MessageItem) -> dict:
    d: dict[str, Any] = {"type": item.type}
    if item.text_item:
        d["text_item"] = _strip_none({"text": item.text_item.text})
    if item.image_item:
        img = item.image_item
        d["image_item"] = _strip_none({
            "media": encode_cdn_media(img.media),
            "thumb_media": encode_cdn_media(img.thumb_media),
            "aeskey": img.aeskey,
            "url": img.url,
        })
    if item.voice_item:
        v = item.voice_item
        d["voice_item"] = _strip_none({
            "media": encode_cdn_media(v.media),
            "encode_type": v.encode_type,
            "sample_rate": v.sample_rate,
            "playtime": v.playtime,
        })
    if item.file_item:
        f = item.file_item
        d["file_item"] = _strip_none({
            "media": encode_cdn_media(f.media),
            "file_name": f.file_name,
            "md5": f.md5,
            "len": f.len,
        })
    if item.video_item:
        vid = item.video_item
        d["video_item"] = _strip_none({
            "media": encode_cdn_media(vid.media),
            "video_size": vid.video_size,
            "play_length": vid.play_length,
            "thumb_media": encode_cdn_media(vid.thumb_media),
        })
    return d


def encode_weixin_message(msg: WeixinMessage) -> dict:
    return _strip_none({
        "to_user_id": msg.to_user_id,
        "from_user_id": msg.from_user_id,
        "client_id": msg.client_id,          # 必须带：服务端用于消息去重，缺失时只有第一条能被投递
        "session_id": msg.session_id,
        "context_token": msg.context_token,
        "message_type": msg.message_type,
        "message_state": msg.message_state,
        "item_list": [encode_message_item(i) for i in msg.item_list] if msg.item_list else None,
    })