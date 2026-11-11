"""JSON 接口适配器。通过点号路径从响应中取列表与字段。

配置示例：
  request:
    url: https://example.com/api/hot
  parse:
    items_path: data.list          # 列表所在路径，支持 a.b.c；段为 * 时展开该列表的每个元素
                                   # 也可写成列表（候选路径），按顺序取第一个非空结果
    fields:
      title: title                 # 字段路径，支持 a.b.c
      url: link
      heat: hot_value
      summary: desc
    url_template: "https://example.com/p/{id}"   # 可选：接口不给链接时拼接，{路径} 取条目字段并做 URL 编码
  request.headers: 可选，额外请求头（如 Referer / User-Agent），写在 request.headers 映射里

嵌套示例（列表套列表）：items_path: data.cards.0.content.*.content
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


def _walk(obj: Any, parts: list[str]) -> list[Any]:
    """沿路径取值；遇到 * 时对列表每个元素继续向下走，返回所有叶子值。"""
    if not parts:
        return [obj]
    if obj is None:
        return []
    head, rest = parts[0], parts[1:]
    if head == "*":
        if not isinstance(obj, list):
            return []
        out: list[Any] = []
        for elem in obj:
            out.extend(_walk(elem, rest))
        return out
    if isinstance(obj, dict):
        return _walk(obj.get(head), rest)
    if isinstance(obj, list) and head.isdigit():
        idx = int(head)
        return _walk(obj[idx] if idx < len(obj) else None, rest)
    return []


def _resolve_list(payload: Any, path: str) -> Optional[list[Any]]:
    """按路径取列表；路径为空时直接用整个响应。取不到列表返回 None。"""
    if not path:
        return payload if isinstance(payload, list) else None
    leaves = _walk(payload, [p for p in path.split(".") if p != ""])
    out: list[Any] = []
    found = False
    for leaf in leaves:
        if isinstance(leaf, list):
            found = True
            out.extend(leaf)
    return out if found else None


def _candidate_paths(items_path: Any) -> list[str]:
    if not items_path:
        return [""]
    if isinstance(items_path, list):
        return [str(p) for p in items_path]
    return [str(items_path)]


def _brief(obj: Any, limit: int = 400) -> str:
    """条目的简短文本表示，用于调试输出。"""
    try:
        text = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(obj)
    return text if len(text) <= limit else text[:limit] + "...(截断)"


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

        raw_list: Optional[list[Any]] = None
        tried: list[str] = []
        for cand in _candidate_paths(items_path):
            tried.append(cand)
            lst = _resolve_list(payload, cand)
            if lst:
                raw_list = lst
                break
            if raw_list is None and lst is not None:
                raw_list = lst  # 找到了列表但为空，保留以便区分报错
        if raw_list is None:
            return FetchResult(
                items=[],
                error=f"items_path {tried} 未指向列表；响应结构：{json.dumps(outline(payload), ensure_ascii=False)[:800]}",
            )

        items: list[RawItem] = []
        skipped = 0
        skipped_samples: list[str] = []
        for obj in raw_list:
            title = _as_text(_get_path(obj, fields["title"])) if isinstance(obj, dict) else ""
            if not title:
                skipped += 1
                if len(skipped_samples) < 3:
                    skipped_samples.append(_brief(obj))
                continue
            url_value = _optional_field(obj, fields, "url") or ""
            if not url_value and spec.parse.get("url_template"):
                url_value = _render_template(str(spec.parse["url_template"]), obj)
            items.append(
                RawItem(
                    title=title,
                    url=url_value,
                    rank=len(items) + 1,
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
        meta: dict[str, Any] = {
            "total_in_payload": len(raw_list),
            "skipped": skipped,
            "debug": {
                "items_path_tried": tried,
                "title_path": fields["title"],
                "fields": fields,
                "payload_outline": outline(payload, depth=6),
                "skipped_samples": skipped_samples,
                "kept_first3": [{"title": i.title, "url": i.url, "heat": i.heat} for i in items[:3]],
                "raw_head": raw.decode("utf-8", errors="replace")[:1500],
            },
        }
        return FetchResult(items=items[:limit], meta=meta)
