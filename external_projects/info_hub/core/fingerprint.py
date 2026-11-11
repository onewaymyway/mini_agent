"""信息源查重：按"域名 + 路径/选择器"指纹判断是否与已有源重复。

吸取 browser_site_scraper 的教训：域名必须同时兼容裸域名与 www 子域名，
同一站点的同类来源不应因为写法不同而被当作新来源重复添加。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from typing import Optional

from adapters.base import SourceSpec


def normalize_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def path_key(url: str) -> str:
    p = urlparse(url)
    return (p.path or "/").rstrip("/") or "/"


def list_key(spec_like: dict) -> str:
    """列表定位键：html_list 用 list_selector，json_api 用 items_path。"""
    parse = spec_like.get("parse") or {}
    return str(parse.get("list_selector") or parse.get("items_path") or "")


@dataclass(frozen=True)
class Fingerprint:
    domain: str
    path: str
    list_key: str
    adapter: str

    def same_as(self, other: "Fingerprint") -> bool:
        if self.domain != other.domain:
            return False
        if self.list_key and other.list_key and self.list_key == other.list_key and self.adapter == other.adapter:
            return True
        return self.path == other.path and self.adapter == other.adapter


def fingerprint_of(source: dict) -> Fingerprint:
    url = (source.get("request") or {}).get("url", "")
    return Fingerprint(
        domain=normalize_domain(url),
        path=path_key(url),
        list_key=list_key(source),
        adapter=str(source.get("adapter", "")),
    )


def find_similar(existing: dict[str, SourceSpec], candidate: dict) -> list[str]:
    """返回与候选源指纹相似的已有源 id 列表。"""
    fp = fingerprint_of(candidate)
    hits = []
    for sid, spec in existing.items():
        other = fingerprint_of({"request": spec.request, "parse": spec.parse, "adapter": spec.adapter})
        if fp.same_as(other):
            hits.append(sid)
    return sorted(hits)
