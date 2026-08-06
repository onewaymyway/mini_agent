#!/usr/bin/env python
"""Google 搜索器"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults


class GoogleSearcher(BaseSearcher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.google.com"
        self.search_url = "https://www.google.com/search?q={query}"

    @property
    def source_name(self) -> str:
        return "Google"

    @property
    def supported_types(self) -> list:
        return ["web", "news", "image", "video"]

    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        results = SearchResults(source=self.source_name)
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - Google 第{i+1}条",
                url=f"{self.base_url}/search?q={query}",
                snippet=f"Google 搜索: {query}",
                source=self.source_name
            )
            results.add(result)
        return results

    def get_detail(self, url: str) -> dict:
        return {"url": url, "source": self.source_name, "type": "web"}


if __name__ == "__main__":
    searcher = GoogleSearcher()
    results = searcher.search("test")
    print(f"Found {len(results.results)} results")
