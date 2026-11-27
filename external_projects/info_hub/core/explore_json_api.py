"""P15：从渲染页面观察到的 JSON 响应中识别“列表型接口”。纯函数，不做网络访问，不保存响应正文。

识别条件：响应中存在对象数组（至少 3 条），其中多数条目同时具备标题类字段与绝对 https 链接字段。
输出为 json_api 适配器可直接使用的配置：接口地址（已剔除疑似令牌类参数）、列表路径、字段映射。
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Optional

MIN_ITEMS = 3
MAX_DEPTH = 3
TITLE_KEYS = ("title", "name", "headline", "subject", "text", "label", "word")
URL_KEYS = ("url", "link", "href", "uri", "permalink", "jump_url", "target_url", "web_url", "share_url")
SENSITIVE = re.compile(r"(token|sign|sig|auth|session|secret|key|nonce|ts|timestamp|uid|user|cookie|csrf|"
                       r"ticket|passport|ua|device)", re.IGNORECASE)
PAGE_PARAMS = {"page", "offset", "cursor", "since", "before", "after", "limit", "size", "pn", "start"}


def _find_lists(obj: Any, path: str = "", depth: int = 0, out: Optional[list] = None) -> list[tuple[str, list]]:
    """找出所有“对象数组”及其路径（点号路径，根为数组时路径为空字符串）。"""
    out = [] if out is None else out
    if depth > MAX_DEPTH:
        return out
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj[:5]):
            out.append((path, obj))
    elif isinstance(obj, dict):
        for k, v in list(obj.items())[:40]:
            _find_lists(v, f"{path}.{k}" if path else str(k), depth + 1, out)
    return out


def _pick_fields(items: list) -> Optional[dict[str, str]]:
    """在前若干条中找到稳定存在的标题字段与链接字段。"""
    sample = [it for it in items[:8] if isinstance(it, dict)]
    if not sample:
        return None

    def best(keys: tuple[str, ...], valid) -> Optional[str]:
        for key in keys:
            hits = sum(1 for it in sample if valid(it.get(key)))
            if hits >= max(MIN_ITEMS, int(len(sample) * 0.8)):
                return key
        return None

    title = best(TITLE_KEYS, lambda v: isinstance(v, str) and 2 <= len(v.strip()) <= 120)
    link = best(URL_KEYS, lambda v: isinstance(v, str) and v.startswith("https://"))
    if not title or not link:
        return None
    return {"title": title, "url": link}


def clean_endpoint(url: str) -> str:
    """去掉片段，剔除疑似令牌、签名、用户标识类参数；保留其余查询参数。"""
    parts = urllib.parse.urlsplit(url)
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if not SENSITIVE.search(k)]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), ""))


def observe_json(endpoint: str, payload: Any) -> Optional[dict[str, Any]]:
    """判断一个 JSON 响应是否为列表型接口。返回描述（不含响应正文），否则返回 None。"""
    if not endpoint.startswith("https://"):
        return None
    best: Optional[dict[str, Any]] = None
    for path, items in _find_lists(payload):
        if len(items) < MIN_ITEMS:
            continue
        fields = _pick_fields(items)
        if not fields:
            continue
        if best is None or len(items) > best["count"]:
            best = {"endpoint": clean_endpoint(endpoint), "items_path": path, "fields": fields,
                    "count": len(items)}
    if best is None:
        return None
    params = {k for k, _ in urllib.parse.parse_qsl(urllib.parse.urlsplit(endpoint).query)}
    best["paginated_hint"] = bool(params & PAGE_PARAMS)
    return best


def api_source(source_id: str, name: str, desc: dict[str, Any]) -> dict[str, Any]:
    """由接口描述生成 json_api 源声明（不需要模型，默认停用）。"""
    return {"id": source_id, "name": name, "category": "news", "kind": "feed", "adapter": "json_api",
            "tags": ["自动探索", "接口"], "enabled": False,
            "request": {"url": desc["endpoint"]},
            "parse": {"items_path": desc["items_path"], "fields": dict(desc["fields"])},
            "schedule": {"every": "60m"}, "policy": {"max_items": 30, "dedup_by": "url"}}
