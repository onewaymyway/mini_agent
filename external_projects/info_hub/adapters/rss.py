"""RSS 2.0 与 Atom 适配器。仅使用标准库 xml.etree，无额外依赖。"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from adapters.base import Adapter, FetchContext, FetchResult, RawItem, SourceSpec


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(elem: ET.Element, name: str) -> str:
    for child in elem:
        if _local(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _child_link(elem: ET.Element) -> str:
    """RSS 的 <link>文本，或 Atom 的 <link href=...>。"""
    for child in elem:
        if _local(child.tag) != "link":
            continue
        if child.get("href"):
            rel = child.get("rel", "alternate")
            if rel == "alternate":
                return child.get("href", "").strip()
        elif child.text:
            return child.text.strip()
    return ""


def parse_feed(data: bytes) -> list[RawItem]:
    root = ET.fromstring(data)
    entries = [e for e in root.iter() if _local(e.tag) in ("item", "entry")]
    items: list[RawItem] = []
    for idx, e in enumerate(entries, start=1):
        title = _child_text(e, "title")
        if not title:
            continue
        summary = _child_text(e, "description") or _child_text(e, "summary") or _child_text(e, "content")
        published = _child_text(e, "pubDate") or _child_text(e, "published") or _child_text(e, "updated")
        items.append(
            RawItem(
                title=title,
                url=_child_link(e),
                rank=idx,
                summary=summary,
                published_at=published or None,
            )
        )
    return items


class RssAdapter(Adapter):
    kind_name = "rss"

    def validate(self, spec: SourceSpec) -> list[str]:
        errs = []
        if not spec.request.get("url"):
            errs.append("rss 适配器需要 request.url")
        return errs

    def fetch(self, spec: SourceSpec, ctx: FetchContext) -> FetchResult:
        url = spec.request.get("url", "")
        try:
            data = ctx.http_get(url)
            items = parse_feed(data)
        except Exception as exc:  # noqa: BLE001 - 错误转为结果，由上层记录
            return FetchResult(items=[], error=f"{type(exc).__name__}: {exc}")
        limit = self.max_items(spec)
        return FetchResult(items=items[:limit], meta={"total_in_feed": len(items)})
