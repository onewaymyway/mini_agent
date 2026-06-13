"""
web_search/providers/serper.py — Serper.dev（Google 搜索结果代理）

需要 SERPER_API_KEY（https://serper.dev 提供免费额度，注册赠送 2,500 次查询）。
"""

from __future__ import annotations

import os

from mini_agent.web_search.base import SearchResult, WebSearchError, WebSearchProvider

_ENDPOINT = "https://google.serper.dev/search"


class SerperProvider(WebSearchProvider):
    requires_api_key = True
    api_key_env = "SERPER_API_KEY"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise WebSearchError(
                "Serper provider requires 'httpx'. Install with: pip install httpx"
            ) from exc

        api_key = getattr(self.cfg.web_search, "api_key", "") or os.environ.get(self.api_key_env, "")
        if not api_key:
            raise WebSearchError(
                f"Serper provider requires {self.api_key_env} to be set."
            )

        timeout = getattr(self.cfg.web_search, "timeout", 10.0)
        try:
            resp = httpx.post(
                _ENDPOINT,
                json={"q": query, "num": max_results},
                headers={
                    "X-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebSearchError(f"Serper request failed: {exc}") from exc

        data = resp.json()
        items = data.get("organic") or []

        results: list[SearchResult] = []
        for item in items[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                )
            )
        return results
