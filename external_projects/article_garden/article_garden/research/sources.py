"""research/sources.py — 来源卡（方案 §7 第 5 步、§8.3）。

来源卡落盘于 research/packs/<tid>/sources/src-NNNN.yaml。可信度按域名启发式分级：
  D：命中章程 sources.avoid；A：学术与官方文档；B：开源与技术社区；C：其它。
摘录只保留包含“必须覆盖概念”的段落，并受 excerpt_chars_max 限制（只摘事实与短摘录）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from urllib.parse import urlsplit

from .. import store

A_HOSTS = ("arxiv.org", "doi.org", "openreview.net", "ieee.org", "acm.org", "nature.com", ".edu", ".gov")
B_HOSTS = ("github.com", "gitlab.com", "huggingface.co", "stackoverflow.com", "readthedocs", "kaggle.com")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify(url: str, avoid: Iterable[str]) -> str:
    low = (url or "").lower()
    for term in avoid or []:
        if term and str(term).lower() in low:
            return "D"
    parts = urlsplit(url or "")
    host = parts.netloc.lower()
    if any(h in host for h in A_HOSTS) or host.startswith("docs.") or "/docs/" in parts.path.lower():
        return "A"
    if any(h in host for h in B_HOSTS):
        return "B"
    return "C"


def excerpt(text: str, points: Iterable[str], max_chars: int) -> str:
    """只保留包含任一要点的段落，拼接到 max_chars 字以内。"""
    pts = [p for p in points if p]
    keep = []
    for para in re.split(r"\n\s*\n", text or ""):
        p = para.strip()
        if p and any(pt in p for pt in pts):
            keep.append(p)
    out = ""
    for p in keep:
        room = max_chars - len(out) - (2 if out else 0)
        if room <= 0:
            break
        piece = p if len(p) <= room else p[:room]
        out = f"{out}\n\n{piece}" if out else piece
    return out.strip()


def covers(text: str, points: Iterable[str]) -> List[str]:
    return [p for p in points if p and p in (text or "")]


# ------------------------------------------------------------------ 存储

def _dir(ctx, tid: str):
    return ctx.path("research", "packs", tid, "sources")


def list_cards(ctx, tid: str) -> List[Dict[str, Any]]:
    d = _dir(ctx, tid)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("src-*.yaml")):
        data = store.read_yaml(p, default=None)
        if isinstance(data, dict):
            out.append(data)
    return out


def has_url(ctx, tid: str, url: str) -> bool:
    return any(c.get("url") == url for c in list_cards(ctx, tid))


def next_id(ctx, tid: str) -> str:
    """计数器持久化：删除来源后编号不复用，避免旧引用指向新来源。"""
    p = ctx.path("research", "packs", tid, "counters.json")
    c = store.read_json(p, default={}) or {}
    n = int(c.get("next_source", 1))
    c["next_source"] = n + 1
    store.write_json_atomic(p, c)
    return f"src-{n:04d}"


def make_card(sid: str, tid: str, *, url: str, title: str, text: str, credibility: str,
              source_type: str, points: Iterable[str], query: str = "") -> Dict[str, Any]:
    pts = list(points)
    return {"id": sid, "topic_id": tid, "url": url, "title": title, "source_type": source_type,
            "credibility": credibility, "fetched_at": _now(), "excerpt": text,
            "covers": covers(text, pts), "query": query}


def write(ctx, tid: str, card: Dict[str, Any]) -> None:
    store.write_yaml_atomic(_dir(ctx, tid) / f"{card['id']}.yaml", card)


def delete(ctx, tid: str, sid: str) -> bool:
    p = _dir(ctx, tid) / f"{sid}.yaml"
    if p.is_file():
        p.unlink()
        return True
    return False
