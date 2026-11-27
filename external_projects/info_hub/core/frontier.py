"""P16：Frontier（待访问队列）。持久化、可断点续跑、深度受限、带禁止名单。

存储：data/discovery/frontier.jsonl，只追加写入。每行是一次状态变化，同一 URL 以最后一行为准。
入队规则：
  - 只接受 https 地址；去掉片段与追踪参数；
  - 深度不超过 MAX_DEPTH（种子页深度 0）；
  - 默认只入同站链接；跨站入队需要 frontier.cross_site 显式开启，且只能来自可信来源页（种子或已接受来源）；
  - 登录、注册、购物、下载、广告等路径与文件类型直接丢弃；
  - 命中禁止名单（config/denylist.yaml，按域名及其子域名匹配）的站点不入队；
  - 每个页面最多入队 per_page_cap 条；队列中待访问条目有全局上限。
"""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from core.store import append_jsonl, file_lock, read_jsonl

MAX_DEPTH = 2
TRACKING = re.compile(r"^(utm_|spm|fbclid|gclid|from|ref|source|share|mc_|_ga)", re.IGNORECASE)
SKIP_PATH = re.compile(r"(login|signin|signup|register|account|cart|shop|pay|order|download|sponsor|"
                       r"advert|\bad\b|mailto|javascript)", re.IGNORECASE)
SKIP_EXT = (".pdf", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4", ".mp3", ".exe",
            ".dmg", ".apk", ".css", ".js", ".ico")
HOT_WORDS = ("news", "tech", "hot", "list", "latest", "feed", "rank", "top", "article", "blog", "finance",
             "world", "science", "video", "game", "gaming", "culture", "business", "society", "sports")


def normalize(url: str) -> Optional[str]:
    """规范化为可入队的 https 地址；不合格返回 None。"""
    parts = urllib.parse.urlsplit((url or "").strip())
    if parts.scheme != "https" or not parts.netloc:
        return None
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if not TRACKING.search(k)]
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urllib.parse.urlunsplit(("https", netloc, path, urllib.parse.urlencode(kept), ""))


def host_key(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")


def score_link(url: str, text: str = "") -> Optional[float]:
    """链接优先级。返回 None 表示直接丢弃。"""
    parts = urllib.parse.urlsplit(url)
    low = parts.path.lower()
    if low.endswith(SKIP_EXT) or SKIP_PATH.search(low) or SKIP_PATH.search(text or ""):
        return None
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) > 3:
        return None
    score = 1.0
    if len(segs) == 1:
        score += 1.0                       # 单层路径多为栏目页
    if any(w in low for w in HOT_WORDS):
        score += 1.0
    if not (2 <= len(text or "") <= 8):
        score -= 0.5
    return score


def load_denylist(root: Path) -> set[str]:
    try:
        data = yaml.safe_load((Path(root) / "config" / "denylist.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return set()
    return {str(h).lower().removeprefix("www.").strip() for h in (data.get("hosts") or []) if str(h).strip()}


def is_denied(host: str, denylist: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in denylist)


class Frontier:
    def __init__(self, data_dir: Path, max_queued: int = 500) -> None:
        self.path = Path(data_dir) / "discovery" / "frontier.jsonl"
        self.max_queued = max_queued

    def _latest(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in read_jsonl(self.path):
            if isinstance(row, dict) and row.get("url"):
                out[row["url"]] = row
        return out

    def _write(self, row: dict[str, Any]) -> None:
        with file_lock(self.path):
            append_jsonl(self.path, row)

    def rows(self) -> list[dict[str, Any]]:
        return list(self._latest().values())

    def queued_count(self) -> int:
        return sum(1 for r in self._latest().values() if r.get("status") == "queued")

    def enqueue(self, url: str, depth: int, parent: str, priority: float, now_iso: str) -> bool:
        if depth > MAX_DEPTH:
            return False
        latest = self._latest()
        if url in latest:               # 已入队、已处理或已跳过的，不重复入队
            return False
        if sum(1 for r in latest.values() if r.get("status") == "queued") >= self.max_queued:
            return False
        self._write({"url": url, "host": host_key(url), "depth": depth, "parent": parent,
                     "priority": round(priority, 2), "status": "queued", "time": now_iso})
        return True

    def set_status(self, url: str, status: str, now_iso: str) -> None:
        row = self._latest().get(url, {"url": url, "host": host_key(url), "depth": 0, "parent": "",
                                       "priority": 0.0})
        self._write(dict(row, status=status, time=now_iso))

    def next_batch(self, n: int, allow_host) -> list[dict[str, Any]]:
        """取出最应该先访问的 n 条：优先级高、深度浅、先入队。allow_host(host) 为 False 的跳过（不改状态）。"""
        queued = [r for r in self._latest().values() if r.get("status") == "queued" and allow_host(r.get("host", ""))]
        queued.sort(key=lambda r: (-float(r.get("priority", 0)), int(r.get("depth", 0)), str(r.get("time", ""))))
        return queued[:max(0, n)]


def enqueue_links(frontier: Frontier, links: list[tuple[str, str]], *, parent: str, parent_depth: int,
                  denylist: set[str], cross_site: bool, trusted: bool, now_iso: str,
                  per_page_cap: int = 20) -> int:
    """把页面中的链接 [(url, 链接文字)] 入队。返回实际入队数量。"""
    depth = parent_depth + 1
    if depth > MAX_DEPTH:
        return 0
    parent_host = host_key(parent)
    added = 0
    scored: list[tuple[float, str]] = []
    for raw, text in links:
        url = normalize(raw)
        if not url:
            continue
        host = host_key(url)
        if is_denied(host, denylist):
            continue
        if host != parent_host and not (cross_site and trusted):
            continue                                    # 跨站：只有显式开启且来自可信页面才入队
        s = score_link(url, text)
        if s is None:
            continue
        scored.append((s, url))
    for s, url in sorted(scored, key=lambda t: -t[0]):
        if added >= per_page_cap:
            break
        if frontier.enqueue(url, depth, parent, s, now_iso):
            added += 1
    return added
