"""HTML 列表适配器：用 CSS 选择器从页面中取条目。

配置示例：
  request:
    url: https://example.com/hot
  browser:
    required: true            # true 时通过共享浏览器会话渲染页面
    site: example_site        # 引用 config/sites.yaml，用于登录失效检测
    wait_selector: ".item"
  parse:
    list_selector: ".item"
    fields:
      title: ".title"
      url: "a@href"           # 选择器@属性名 取属性值；不带 @ 则取文本
      heat: ".heat"
      summary: ".desc"
"""
from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup, Tag

from adapters.base import Adapter, FetchContext, FetchResult, RawItem, SourceSpec
from core.browser_session import LoginRequired


def _select_value(scope: Tag, expr: str) -> str:
    """expr 形如 'sel' 或 'sel@attr'。sel 为空串时取 scope 自身。"""
    if not expr:
        return ""
    selector, _, attr = expr.partition("@")
    selector = selector.strip()
    node = scope.select_one(selector) if selector else scope
    if node is None:
        return ""
    if attr:
        return (node.get(attr) or "").strip() if isinstance(node, Tag) else ""
    return node.get_text(" ", strip=True)


def parse_list(html: str, list_selector: str, fields: dict[str, str], limit: int) -> list[RawItem]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(list_selector)
    items: list[RawItem] = []
    for idx, row in enumerate(rows, start=1):
        title = _select_value(row, fields.get("title", ""))
        if not title:
            continue
        items.append(
            RawItem(
                title=title,
                url=_select_value(row, fields.get("url", "")),
                rank=idx,
                heat=_select_value(row, fields.get("heat", "")) or None,
                summary=_select_value(row, fields.get("summary", "")),
            )
        )
        if len(items) >= limit:
            break
    return items


class HtmlListAdapter(Adapter):
    kind_name = "html_list"

    def validate(self, spec: SourceSpec) -> list[str]:
        errs = []
        if not spec.request.get("url"):
            errs.append("html_list 适配器需要 request.url")
        if not spec.parse.get("list_selector"):
            errs.append("html_list 适配器需要 parse.list_selector")
        fields = spec.parse.get("fields")
        if not isinstance(fields, dict) or "title" not in fields:
            errs.append("html_list 适配器需要 parse.fields 且包含 title")
        if spec.browser.get("required") and not spec.browser.get("site"):
            errs.append("browser.required 为 true 时需要 browser.site，以便检测登录失效")
        return errs

    def fetch(self, spec: SourceSpec, ctx: FetchContext) -> FetchResult:
        url = spec.request.get("url", "")
        use_browser = bool(spec.browser.get("required"))
        site = spec.browser.get("site")
        try:
            if use_browser:
                if ctx.browser is None:
                    return FetchResult(items=[], error="未配置浏览器会话")
                site_cfg = (ctx.sites or {}).get(site, {}) if site else {}
                html = ctx.browser.fetch_html(
                    url,
                    site=site,
                    headless=bool(spec.browser.get("headless", True)),
                    wait_selector=spec.browser.get("wait_selector"),
                    expired_pattern=site_cfg.get("expired_pattern"),
                )
            else:
                html = ctx.http_get(url).decode("utf-8", errors="replace")
        except LoginRequired as exc:
            return FetchResult(items=[], error=f"需要重新登录：{exc}", meta={"login_required": site})
        except Exception as exc:  # noqa: BLE001
            return FetchResult(items=[], error=f"{type(exc).__name__}: {exc}")

        items = parse_list(
            html,
            spec.parse["list_selector"],
            spec.parse.get("fields", {}),
            self.max_items(spec),
        )
        return FetchResult(items=items, meta={"rows_matched": len(items)})
