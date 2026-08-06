#!/usr/bin/env python
"""搜狗搜索器"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults


class SogouSearcher(BaseSearcher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.sogou.com"
        self.search_url = "https://www.sogou.com/web?query={query}"

    @property
    def source_name(self) -> str:
        return "搜狗"

    @property
    def supported_types(self) -> list:
        return ["web", "news", "image"]

    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        results = SearchResults(source=self.source_name)
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 搜狗第{i+1}条",
                url=f"{self.base_url}/web?query={query}",
                snippet=f"搜狗搜索: {query}",
                source=self.source_name
            )
            results.add(result)
        return results

    def get_detail(self, url: str) -> dict:
        return {"url": url, "source": self.source_name, "type": "web"}


if __name__ == "__main__":
    searcher = SogouSearcher()
    results = searcher.search("测试")
    print(f"找到 {len(results.results)} 个结果")
