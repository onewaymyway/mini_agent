#!/usr/bin/env python
"""DuckDuckGo 搜索器"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults


class DuckDuckGoSearcher(BaseSearcher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://duckduckgo.com"
        self.search_url = "https://duckduckgo.com/?q={query}"

    @property
    def source_name(self) -> str:
        return "DuckDuckGo"

    @property
    def supported_types(self) -> list:
        return ["web", "news", "image"]

    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        results = SearchResults(source=self.source_name)
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - DuckDuckGo 第{i+1}条",
                url=f"{self.base_url}/?q={query}",
                snippet=f"DuckDuckGo 搜索: {query}",
                source=self.source_name
            )
            results.add(result)
        return results

    def get_detail(self, url: str) -> dict:
        return {"url": url, "source": self.source_name, "type": "web"}


if __name__ == "__main__":
    searcher = DuckDuckGoSearcher()
    results = searcher.search("test")
    print(f"Found {len(results.results)} results")
