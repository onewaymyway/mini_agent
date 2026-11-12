"""看板“在专用浏览器中打开”的待匹配登记。

背景：插件原先只靠地址里的 #ihk=... 片段识别条目，但小红书等站点会在加载过程中改写甚至丢弃片段，
工具条因此不出现。现在看板打开页面时，同时把“条目上下文”登记到 data/state/pending_open.json；
插件在页面里找不到标记时，把当前地址发给接收服务（/v1/context），按“主机 + 路径”匹配登记项取回上下文。

- 匹配只看主机与路径，忽略查询参数与片段（xsec_token 等参数会变）。
- 路径不同（站点重定向）时，退化为按路径最后一段 ID（至少 8 位）在同主机内匹配。
- 登记项 24 小时过期，最多保留 200 条；只在本机文件中保存，不对外提供。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from core.store import atomic_write_json, file_lock

TTL = timedelta(hours=24)
MAX_ENTRIES = 200
MIN_ID_LEN = 8
_ID16_RE = re.compile(r"^[0-9a-f]{16}$")


def pending_path(state_dir: Path) -> Path:
    return Path(state_dir) / "pending_open.json"


def _host(netloc: str) -> str:
    host = netloc.lower().rsplit("@", 1)[-1]
    return host[4:] if host.startswith("www.") else host


def match_key(url: str) -> str:
    """主机（去 www.）+ 路径（去末尾斜杠）。非 http(s) 地址返回空串。"""
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return ""
    return f"{_host(parts.netloc)}{parts.path.rstrip('/')}"


def _id_key(url: str) -> str:
    """同主机下路径最后一段（作为条目 ID）。过短的段（如 explore、index.html）不参与匹配。"""
    parts = urlsplit((url or "").strip())
    last = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if parts.scheme.lower() not in ("http", "https") or len(last) < MIN_ID_LEN:
        return ""
    return f"{_host(parts.netloc)}|{last}"


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now().astimezone()


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _alive(entry: dict[str, Any], now: datetime) -> bool:
    try:
        return now - datetime.fromisoformat(str(entry.get("time"))) <= TTL
    except (TypeError, ValueError):
        return False


def register(state_dir: Path, *, url: str, item_key: str, source_id: str, view: str, title: str,
             now: Optional[datetime] = None) -> bool:
    """登记一次看板打开。返回是否登记成功（地址或 key 不合法时返回 False，不抛异常）。"""
    mk = match_key(url)
    if not mk or not _ID16_RE.match(item_key or ""):
        return False
    current = _now(now)
    path = pending_path(state_dir)
    entry = {"match": mk, "id_key": _id_key(url), "item_key": item_key, "source_id": source_id,
             "view": view, "title": title[:120], "url": url.split("#")[0],
             "time": current.isoformat(timespec="seconds")}
    with file_lock(path):
        entries = [e for e in _load(path) if _alive(e, current) and e.get("match") != mk]
        entries.append(entry)
        atomic_write_json(path, {"entries": entries[-MAX_ENTRIES:]})
    return True


def lookup(state_dir: Path, url: str, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """按当前页面地址取回条目上下文；没有匹配返回 None。同一匹配取最近一次登记。"""
    mk = match_key(url)
    if not mk:
        return None
    current = _now(now)
    entries = [e for e in _load(pending_path(state_dir)) if _alive(e, current)]
    hit = next((e for e in reversed(entries) if e.get("match") == mk), None)
    if hit is None:
        ik = _id_key(url)
        if ik:
            hit = next((e for e in reversed(entries) if e.get("id_key") == ik), None)
    if hit is None:
        return None
    return {"item_key": hit["item_key"], "source_id": hit.get("source_id", ""), "view": hit.get("view", ""),
            "title": hit.get("title", ""), "url": hit.get("url", "")}
