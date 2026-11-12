"""条目反馈：感兴趣 / 不感兴趣 / 稍后看，可选原因标签与备注。

存储为 append-only 的 jsonl（data/feedback/events.jsonl），每行一条事件，不修改、不删除，
便于之后用不同方法重新分析。写入使用文件锁，与看板/后台 tick 并发安全。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from core.store import append_jsonl, file_lock, read_jsonl

ACTIONS: dict[str, str] = {
    "interested": "感兴趣",
    "not_interested": "不感兴趣",
    "later": "稍后看",
}

REASONS: dict[str, str] = {
    "off_topic": "话题不符",
    "shallow": "内容太浅",
    "seen": "已经看过",
    "source_dislike": "不喜欢这个来源",
}

MAX_NOTE_CHARS = 300
VIEW_MODES = ("reader", "iframe", "newtab")


def normalize_url(url: str) -> str:
    """规范化 URL：去掉 fragment 与末尾斜杠，host 小写，便于同一条目在不同抓取间对齐。"""
    parts = urlsplit((url or "").strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def item_key(url: str, title: str = "") -> str:
    """条目稳定标识：URL 规范化后取哈希；没有 URL 时退化为标题。"""
    basis = normalize_url(url) if url else (title or "").strip()
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def feedback_path(data_dir: Path) -> Path:
    return Path(data_dir) / "feedback" / "events.jsonl"


def validate_event(body: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化一次反馈请求。不合法时抛出 ValueError（由 API 层转为 400）。"""
    action = str(body.get("action") or "").strip()
    if action not in ACTIONS:
        raise ValueError(f"未知反馈动作：{action or '（空）'}")
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip()
    if not url and not title:
        raise ValueError("反馈需要条目标题或链接")
    if url and not re.match(r"^https?://", url):
        raise ValueError("条目链接必须是 http(s) 地址")
    reasons = body.get("reasons") or []
    if not isinstance(reasons, list):
        raise ValueError("原因标签必须是列表")
    bad = [r for r in reasons if r not in REASONS]
    if bad:
        raise ValueError(f"未知原因标签：{', '.join(map(str, bad))}")
    note = str(body.get("note") or "").strip()[:MAX_NOTE_CHARS]
    source_id = str(body.get("source_id") or "").strip()
    if not source_id or source_id != Path(source_id).name:
        raise ValueError("信息源 id 不合法")
    open_mode = str(body.get("open_mode") or "").strip()
    return {
        "action": action,
        "source_id": source_id,
        "item_key": item_key(url, title),
        "title": title[:200],
        "url": url,
        "reasons": list(dict.fromkeys(reasons)),  # 去重且保持顺序
        "note": note,
        "open_mode": open_mode if open_mode in VIEW_MODES else "",
    }


def record_feedback(data_dir: Path, body: dict[str, Any]) -> dict[str, Any]:
    event = validate_event(body)
    event = {"id": uuid.uuid4().hex[:12],
             "time": datetime.now().astimezone().isoformat(timespec="seconds"), **event}
    path = feedback_path(data_dir)
    with file_lock(path):
        append_jsonl(path, event)
    return event


def read_feedback(data_dir: Path, key: Optional[str] = None) -> list[dict[str, Any]]:
    events = list(read_jsonl(feedback_path(data_dir)))
    if key:
        events = [e for e in events if e.get("item_key") == key]
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {action: 0 for action in ACTIONS}
    for e in events:
        if e.get("action") in counts:
            counts[e["action"]] += 1
    return counts
