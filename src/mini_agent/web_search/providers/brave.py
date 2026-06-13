"""
web_search/providers/brave.py — Brave Search API

需要 BRAVE_API_KEY（https://brave.com/search/api/ 提供免费额度，
Free tier: 2,000 次查询/月）。
"""

from __future__ import annotations

import os

from mini_agent.web_search.base import SearchResult, WebSearchError, WebSearchProvider

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveSearchProvider(WebSearchProvider):
    requires_api_key = True
    api_key_env = "BRAVE_API_KEY"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise WebSearchError(
                "Brave provider requires 'httpx'. Install with: pip install httpx"
            ) from exc

        api_key = getattr(self.cfg.web_search, "api_key", "") or os.environ.get(self.api_key_env, "")
        if not api_key:
            raise WebSearchError(
                f"Brave Search provider requires {self.api_key_env} to be set."
            )

        timeout = getattr(self.cfg.web_search, "timeout", 10.0)
        try:
            resp = httpx.get(
                _ENDPOINT,
                params={"q": query, "count": max_results},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebSearchError(f"Brave Search request failed: {exc}") from exc

        data = resp.json()
        items = (data.get("web") or {}).get("results") or []

        results: list[SearchResult] = []
        for item in items[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                )
            )
        return results
