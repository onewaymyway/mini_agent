"""RSS 2.0 与 Atom 适配器。仅使用标准库 xml.etree，无额外依赖。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from adapters.base import Adapter, FetchContext, FetchResult, RawItem, SourceSpec, failure_debug

# XML 1.0 不允许的控制字符；裸 & 与裸 < 是 RSS 里最常见的非法字符（标题或摘要里未转义）
_BAD_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BARE_AMP = re.compile(rb"&(?!(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#x[0-9A-Fa-f]+);)")
_BARE_LT = re.compile(rb"<(?![A-Za-z_/!?])")


def sanitize_xml(data: bytes) -> bytes:
    """尽量修复常见的非法 XML：去掉控制字符，转义裸 & 与裸 <。仅在原文解析失败后才使用。"""
    data = _BAD_CONTROL.sub(b"", data)
    data = _BARE_AMP.sub(b"&amp;", data)
    return _BARE_LT.sub(b"&lt;", data)


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
        data: bytes | None = None
        try:
            data = ctx.http_get(url)
        except Exception as exc:  # noqa: BLE001 - 错误转为结果，由上层记录
            return FetchResult(items=[], error=f"{type(exc).__name__}: {exc}",
                               meta={"debug": failure_debug(None, exc)})
        note: dict = {}
        try:
            items = parse_feed(data)
        except ET.ParseError as first:
            try:  # 原文非法：修复常见字符后再解析一次；修复成功则记录提示
                items = parse_feed(sanitize_xml(data))
                note = {"xml_sanitized": True, "xml_error_before_fix": str(first)}
            except ET.ParseError:
                return FetchResult(items=[], error=f"ParseError: {first}",
                                   meta={"debug": failure_debug(data, first)})
        except Exception as exc:  # noqa: BLE001
            return FetchResult(items=[], error=f"{type(exc).__name__}: {exc}",
                               meta={"debug": failure_debug(data, exc)})
        limit = self.max_items(spec)
        return FetchResult(items=items[:limit], meta={"total_in_feed": len(items), **note})
