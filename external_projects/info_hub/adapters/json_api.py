"""JSON 接口适配器。通过点号路径从响应中取列表与字段。

配置示例：
  request:
    url: https://example.com/api/hot
  parse:
    items_path: data.list          # 列表所在路径
    fields:
      title: title                 # 字段路径，支持 a.b.c
      url: link
      heat: hot_value
      summary: desc
    url_template: "https://example.com/p/{id}"   # 可选：接口不给链接时拼接，{路径} 取条目字段并做 URL 编码
  request.headers: 可选，额外请求头（如 Referer / User-Agent），写在 request.headers 映射里
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote
from typing import Any, Optional

from adapters.base import Adapter, FetchContext, FetchResult, RawItem, SourceSpec


def _get_path(obj: Any, path: str) -> Any:
    cur = obj
    for key in path.split("."):
        if key == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and key.isdigit():
            idx = int(key)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_field(obj: dict, fields: dict[str, str], name: str) -> Optional[str]:
    """字段未在配置中声明或取值为空时返回 None。"""
    if name not in fields:
        return None
    return _as_text(_get_path(obj, fields[name])) or None


def outline(obj: Any, depth: int = 4) -> Any:
    """响应结构的缩略描述（只留键名、列表长度与类型，不含具体内容），用于排查路径配置。"""
    if depth <= 0:
        return type(obj).__name__
    if isinstance(obj, dict):
        return {k: outline(v, depth - 1) for k, v in list(obj.items())[:15]}
    if isinstance(obj, list):
        return f"list[{len(obj)}]" + (f" of {json.dumps(outline(obj[0], depth - 1), ensure_ascii=False)}" if obj else "")
    return type(obj).__name__


_TPL_RE = re.compile(r"\{([^{}]+)\}")


def _render_template(tpl: str, obj: Any) -> str:
    """把模板里的 {路径} 替换为条目字段（URL 编码）；任一字段为空则返回空串。"""
    missing = False

    def sub(m: "re.Match[str]") -> str:
        nonlocal missing
        val = _as_text(_get_path(obj, m.group(1).strip()))
        if not val:
            missing = True
        return quote(val, safe="")

    out = _TPL_RE.sub(sub, tpl)
    return "" if missing else out


class JsonApiAdapter(Adapter):
    kind_name = "json_api"

    def validate(self, spec: SourceSpec) -> list[str]:
        errs = []
        if not spec.request.get("url"):
            errs.append("json_api 适配器需要 request.url")
        fields = spec.parse.get("fields")
        if not isinstance(fields, dict) or "title" not in fields:
            errs.append("json_api 适配器需要 parse.fields 且包含 title")
        return errs

    def fetch(self, spec: SourceSpec, ctx: FetchContext) -> FetchResult:
        url = spec.request.get("url", "")
        items_path = spec.parse.get("items_path", "")
        fields: dict[str, str] = spec.parse.get("fields", {})
        headers = spec.request.get("headers")
        try:
            raw = ctx.http_get(url, headers=headers) if headers else ctx.http_get(url)
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return FetchResult(items=[], error=f"{type(exc).__name__}: {exc}")

        raw_list = _get_path(payload, items_path) if items_path else payload
        if not isinstance(raw_list, list):
            return FetchResult(items=[], error=f"items_path '{items_path}' 未指向列表")

        items: list[RawItem] = []
        for idx, obj in enumerate(raw_list, start=1):
            if not isinstance(obj, dict):
                continue
            title = _as_text(_get_path(obj, fields["title"]))
            if not title:
                continue
            url_value = _optional_field(obj, fields, "url") or ""
            if not url_value and spec.parse.get("url_template"):
                url_value = _render_template(str(spec.parse["url_template"]), obj)
            items.append(
                RawItem(
                    title=title,
                    url=url_value,
                    rank=idx,
                    heat=_optional_field(obj, fields, "heat"),
                    summary=_optional_field(obj, fields, "summary") or "",
                    published_at=_optional_field(obj, fields, "published_at"),
                )
            )
        if not items:
            return FetchResult(
                items=[],
                error=("解析到 0 条：items_path 指向的列表为空，或 title 字段路径取不到值。"
                       f"响应结构：{json.dumps(outline(payload), ensure_ascii=False)[:800]}"),
            )
        limit = self.max_items(spec)
        return FetchResult(items=items[:limit], meta={"total_in_payload": len(raw_list)})
