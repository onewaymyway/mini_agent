"""
web_search/providers/duckduckgo.py — DuckDuckGo HTML 搜索（免费，无需 API key）

抓取 https://html.duckduckgo.com/html/ 的精简 HTML 结果页并解析。
该端点专为无 JS 客户端设计，比抓取主站更稳定，且不需要任何凭据。

限制：
  - 没有官方 SLA，频率过高可能被临时限流（建议加自定义 User-Agent）
  - 解析依赖 HTML 结构，DuckDuckGo 改版时可能需要调整正则
"""

from __future__ import annotations

import html
import re

from mini_agent.web_search.base import SearchResult, WebSearchError, WebSearchProvider

_ENDPOINT = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; mini-agent/1.0; "
        "+https://github.com/mini-agent) WebSearchTool"
    ),
}

# 结果块：<a class="result__a" href="...">标题</a> ... <a class="result__snippet">摘要</a>
_RESULT_RE = re.compile(
    r'class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
# DuckDuckGo HTML 结果的真实链接被包在 //duckduckgo.com/l/?uddg=<encoded>&... 重定向里
_UDDG_RE = re.compile(r"uddg=([^&]+)")


def _clean(text: str) -> str:
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()


def _resolve_url(raw_url: str) -> str:
    from urllib.parse import unquote

    m = _UDDG_RE.search(raw_url)
    if m:
        return unquote(m.group(1))
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url


class DuckDuckGoProvider(WebSearchProvider):
    """免费 DuckDuckGo HTML 搜索，无需 API key。"""

    requires_api_key = False

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise WebSearchError(
                "DuckDuckGo provider requires 'httpx'. Install with: pip install httpx"
            ) from exc

        timeout = getattr(self.cfg.web_search, "timeout", 10.0)
        try:
            resp = httpx.post(
                _ENDPOINT,
                data={"q": query},
                headers=_HEADERS,
                timeout=timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebSearchError(f"DuckDuckGo request failed: {exc}") from exc

        results: list[SearchResult] = []
        for m in _RESULT_RE.finditer(resp.text):
            if len(results) >= max_results:
                break
            url = _resolve_url(m.group("url"))
            title = _clean(m.group("title"))
            snippet = _clean(m.group("snippet"))
            if url and title:
                results.append(SearchResult(title=title, url=url, snippet=snippet))

        return results
