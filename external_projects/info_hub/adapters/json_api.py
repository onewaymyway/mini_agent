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
"""
from __future__ import annotations

import json
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
        try:
            payload = json.loads(ctx.http_get(url).decode("utf-8"))
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
            items.append(
                RawItem(
                    title=title,
                    url=_optional_field(obj, fields, "url") or "",
                    rank=idx,
                    heat=_optional_field(obj, fields, "heat"),
                    summary=_optional_field(obj, fields, "summary") or "",
                    published_at=_optional_field(obj, fields, "published_at"),
                )
            )
        limit = self.max_items(spec)
        return FetchResult(items=items[:limit], meta={"total_in_payload": len(raw_list)})
