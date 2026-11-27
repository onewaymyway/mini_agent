"""P14：探索中的纯函数——订阅入口、sitemap 栏目、站内分类页的识别。不做任何网络访问。"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

FEED_TYPES = ("application/rss+xml", "application/atom+xml")
SKIP_SEGMENTS = {"tag", "tags", "page", "search", "login", "signin", "signup", "register", "user", "users",
                 "about", "contact", "privacy", "terms", "download", "downloads", "shop", "cart", "help",
                 "feed", "rss", "atom", "api", "static", "assets", "images", "img", "js", "css"}
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


def _abs(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href.strip())


def feed_links(html: str, base_url: str) -> list[str]:
    """页面声明的订阅入口（<link rel="alternate" type="application/rss+xml|atom+xml">）。"""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for tag in soup.find_all("link"):
        rel = " ".join(tag.get("rel") or []).lower()
        typ = (tag.get("type") or "").lower()
        href = tag.get("href")
        if "alternate" in rel and typ in FEED_TYPES and href:
            url = _abs(base_url, href)
            if url.startswith("https://") and url not in out:
                out.append(url)
    return out


def sitemap_locations(robots_txt: str) -> list[str]:
    """robots.txt 中声明的 Sitemap 地址（仅 https）。"""
    out = []
    for line in robots_txt.splitlines():
        if line.lower().startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url.startswith("https://") and url not in out:
                out.append(url)
    return out


def parse_sitemap_urls(xml_text: str, limit: int = 500) -> list[str]:
    return [u.strip() for u in LOC_RE.findall(xml_text)][:limit]


def section_listings(urls: list[str], min_count: int = 3, limit: int = 10) -> list[dict[str, Any]]:
    """从 sitemap 的文章 URL 推断栏目：第一层路径段出现次数 ≥ min_count 的，给出栏目页地址。"""
    counts: dict[str, int] = {}
    roots: dict[str, str] = {}
    for u in urls:
        parts = urllib.parse.urlsplit(u)
        segs = [s for s in parts.path.split("/") if s]
        if len(segs) < 2:  # 首页或单层页面不算栏目内文章
            continue
        seg = segs[0].lower()
        if seg in SKIP_SEGMENTS or "." in seg:
            continue
        counts[seg] = counts.get(seg, 0) + 1
        roots.setdefault(seg, f"{parts.scheme}://{parts.netloc}/{segs[0]}/")
    ranked = sorted((s for s in counts if counts[s] >= min_count), key=lambda s: -counts[s])[:limit]
    return [{"url": roots[s], "section": s, "count": counts[s]} for s in ranked]


def category_links(html: str, base_url: str, limit: int = 8) -> list[str]:
    """首页中同站、单层路径、文字较短的链接，视为可能的栏目页。"""
    base = urllib.parse.urlsplit(base_url)
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if not (2 <= len(text) <= 8):
            continue
        url = _abs(base_url, a["href"])
        parts = urllib.parse.urlsplit(url)
        if parts.netloc != base.netloc or parts.query or parts.fragment:
            continue
        segs = [s for s in parts.path.split("/") if s]
        if len(segs) != 1 or segs[0].lower() in SKIP_SEGMENTS or "." in segs[0]:
            continue
        clean = f"https://{parts.netloc}/{segs[0]}/"
        if clean not in out and clean.rstrip("/") != base_url.rstrip("/"):
            out.append(clean)
        if len(out) >= limit:
            break
    return out


def rss_source(source_id: str, name: str, url: str) -> dict[str, Any]:
    """订阅入口对应的 rss 源声明（不需要模型，直接确定性生成）。"""
    return {"id": source_id, "name": name, "category": "news", "kind": "feed", "adapter": "rss",
            "tags": ["自动探索", "订阅"], "enabled": False, "request": {"url": url},
            "schedule": {"every": "60m"}, "policy": {"max_items": 30, "dedup_by": "url"}}


def all_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """页面中全部 <a href> 的绝对地址与链接文字（P16 入队用）。"""
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        out.append((_abs(base_url, href), a.get_text(strip=True)))
    return out

