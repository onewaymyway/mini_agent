"""页面探索：把目标 URL 的结构压缩成一份小而确定的摘要，交给 LLM 决定怎么抓。

摘要只包含：页面类型（json / html）、重复出现的候选列表容器及其样例、
页面中可见的内嵌数据提示。不把整页 HTML 发给模型，控制 token 与隐私暴露。
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

MAX_CANDIDATES = 6
MAX_SAMPLE_CHARS = 80
_SKIP_TAGS = {"script", "style", "noscript", "meta", "link", "head", "title"}


def _selector_for(tag: Tag) -> str:
    classes = [c for c in (tag.get("class") or []) if c and c.isascii() and c.replace("-", "").replace("_", "").isalnum()]
    if classes:
        return f"{tag.name}.{classes[0]}"
    return tag.name


def explore_html(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "")[:120]

    counts: Counter = Counter()
    samples: dict[str, list[Tag]] = {}
    for tag in soup.find_all(True):
        if tag.name in _SKIP_TAGS:
            continue
        sel = _selector_for(tag)
        if sel == tag.name and not tag.get("class"):
            continue  # 没有 class 的重复标签（如 li）信息量太低，仍保留但排在后面
        counts[sel] += 1
        samples.setdefault(sel, []).append(tag)

    candidates = []
    for sel, n in counts.most_common():
        if n < 3:
            continue
        group = samples[sel]
        first = group[0]
        text = first.get_text(" ", strip=True)[:MAX_SAMPLE_CHARS]
        link = first.find("a", href=True)
        candidates.append({
            "selector": sel,
            "count": n,
            "sample_text": text,
            "sample_has_link": bool(link),
            "sample_link_attr": "a@href" if link else None,
        })
        if len(candidates) >= MAX_CANDIDATES:
            break

    hints = []
    for script in soup.find_all("script"):
        sid = script.get("id") or ""
        stype = script.get("type") or ""
        if sid == "__NEXT_DATA__":
            hints.append("页面内嵌 __NEXT_DATA__ JSON，数据可能在其中")
        if stype == "application/ld+json":
            hints.append("页面含 ld+json 结构化数据")
        if "__INITIAL_STATE__" in (script.string or ""):
            hints.append("页面含 __INITIAL_STATE__ 内嵌状态")
    return {"page_type": "html", "title": title, "candidates": candidates, "embedded_data_hints": sorted(set(hints))}


def _json_shape(value: Any, depth: int = 0, max_depth: int = 3) -> Any:
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: _json_shape(v, depth + 1, max_depth) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        return {"list_len": len(value), "item": _json_shape(value[0], depth + 1, max_depth) if value else None}
    return type(value).__name__


def explore_json(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    return {"page_type": "json", "shape": _json_shape(payload)}


def explore_response(body: bytes, content_type: str = "") -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    if "json" in content_type.lower() or text.lstrip()[:1] in ("{", "["):
        try:
            return explore_json(text)
        except json.JSONDecodeError:
            pass
    return explore_html(text)


def explore_url(url: str, ctx: Any, use_browser: bool = False, site: Optional[str] = None) -> dict[str, Any]:
    """抓取一次页面并生成摘要。use_browser 时通过共享浏览器会话渲染。"""
    if use_browser and ctx.browser is not None:
        html = ctx.browser.fetch_html(url, site=site, headless=True)
        summary = explore_html(html)
    else:
        body = ctx.http_get(url)
        summary = explore_response(body, "")
    summary["url"] = url
    return summary
