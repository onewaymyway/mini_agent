"""
web_search/providers/tavily.py — Tavily AI Search API

需要 TAVILY_API_KEY（https://tavily.com 提供免费额度，专为 LLM/Agent 场景优化，
返回结果已做摘要清洗）。
"""

from __future__ import annotations

import os

from mini_agent.web_search.base import SearchResult, WebSearchError, WebSearchProvider

_ENDPOINT = "https://api.tavily.com/search"


class TavilyProvider(WebSearchProvider):
    requires_api_key = True
    api_key_env = "TAVILY_API_KEY"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise WebSearchError(
                "Tavily provider requires 'httpx'. Install with: pip install httpx"
            ) from exc

        api_key = getattr(self.cfg.web_search, "api_key", "") or os.environ.get(self.api_key_env, "")
        if not api_key:
            raise WebSearchError(
                f"Tavily provider requires {self.api_key_env} to be set."
            )

        timeout = getattr(self.cfg.web_search, "timeout", 10.0)
        try:
            resp = httpx.post(
                _ENDPOINT,
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                },
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebSearchError(f"Tavily request failed: {exc}") from exc

        data = resp.json()
        items = data.get("results") or []

        results: list[SearchResult] = []
        for item in items[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                )
            )
        return results
