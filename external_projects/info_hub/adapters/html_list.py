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

import re
from urllib.parse import urljoin
from typing import Optional

from bs4 import BeautifulSoup, Tag

from adapters.base import Adapter, FetchContext, FetchResult, RawItem, SourceSpec
from core.browser_session import LoginRequired, RiskControlBlocked


def _select_value(scope: Tag, expr: str) -> str:
    """expr 形如 'sel' 或 'sel@attr'。sel 为空串时取 scope 自身。"""
    if not expr:
        return ""
    selector, _, attr = expr.partition("@")
    selector = selector.strip()
    # "self"/"" 取条目本身；"parent" 取父元素（链接与来源、日期并列时使用）
    if selector in ("", "self"):
        node = scope
    elif selector == "parent":
        node = scope.parent if isinstance(scope.parent, Tag) else None
    else:
        node = scope.select_one(selector)
    if node is None:
        return ""
    if attr:
        return (node.get(attr) or "").strip() if isinstance(node, Tag) else ""
    return node.get_text(" ", strip=True)


DEFAULT_DATE_PATTERN = r"(\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)"


def _extract_date(text: str, pattern: str) -> Optional[str]:
    """从文本中取第一个匹配的日期（或日期时间）；无匹配返回 None。"""
    if not text:
        return None
    m = re.search(pattern, text)
    if not m:
        return None
    return (m.group(1) if m.groups() else m.group(0)).strip() or None


def _date_from_ancestors(row: Tag, list_selector: str, pattern: str, max_levels: int = 6) -> Optional[str]:
    """从条目向上找第一个“只包含本条目”的祖先容器，取其中的日期。

    日期常与链接不在同一父元素里，但容器若包含多条新闻，其中的日期不属于本条，故只接受单条容器。
    """
    node = row.parent
    for _ in range(max_levels):
        if not isinstance(node, Tag):
            break
        if len(node.select(list_selector)) > 1:
            break  # 已包含其他新闻，再往上只会更混杂
        date = _extract_date(node.get_text(" ", strip=True), pattern)
        if date:
            return date
        node = node.parent
    return None


def parse_list(html: str, list_selector: str, fields: dict[str, str], limit: int,
               date_pattern: Optional[str] = None) -> list[RawItem]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(list_selector)
    items: list[RawItem] = []
    pattern = date_pattern or DEFAULT_DATE_PATTERN
    for idx, row in enumerate(rows, start=1):
        title = _select_value(row, fields.get("title", ""))
        if not title:
            continue
        published = None
        if fields.get("published_at") == "ancestor":
            published = _date_from_ancestors(row, list_selector, pattern)
        elif fields.get("published_at"):
            published = _extract_date(_select_value(row, fields["published_at"]), pattern)
        items.append(
            RawItem(
                title=title,
                url=_select_value(row, fields.get("url", "")),
                rank=idx,
                heat=_select_value(row, fields.get("heat", "")) or None,
                summary=_select_value(row, fields.get("summary", "")),
                published_at=published,
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
        except RiskControlBlocked as exc:
            return FetchResult(items=[], error=f"风控拦截：{exc}", meta={"risk_blocked": site})
        except Exception as exc:  # noqa: BLE001
            return FetchResult(items=[], error=f"{type(exc).__name__}: {exc}")

        items = parse_list(
            html,
            spec.parse["list_selector"],
            spec.parse.get("fields", {}),
            self.max_items(spec),
            date_pattern=spec.parse.get("date_pattern"),
        )
        # 相对链接补全为绝对地址（以页面地址为基准）
        base_url = spec.request.get("url", "")
        for i in items:
            if i.url and not i.url.startswith(("http://", "https://")):
                i.url = urljoin(base_url, i.url)
        with_date = sum(1 for i in items if i.published_at)
        debug = {
            "rows_matched": len(items),
            "with_date": with_date,
            "list_selector": spec.parse["list_selector"],
            "fields": spec.parse.get("fields", {}),
            "kept_first3": [{"title": i.title, "url": i.url, "published_at": i.published_at}
                            for i in items[:3]],
        }
        return FetchResult(items=items, meta={"rows_matched": len(items), "with_date": with_date, "debug": debug})
