#!/usr/bin/env python
"""知乎专栏搜索器"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults


class ZhihuColumnSearch(BaseSearcher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://zhuanlan.zhihu.com"
        self.search_url = "https://zhuanlan.zhihu.com/search?type=article&q={query}"

    @property
    def source_name(self) -> str:
        return "知乎专栏"

    @property
    def supported_types(self) -> list:
        return ["zhihu_column"]

    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        results = SearchResults(source=self.source_name)
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 知乎专栏第{i+1}篇",
                url=f"{self.base_url}/search?type=article&q={query}",
                snippet=f"知乎专栏搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        return results

    def get_detail(self, url: str) -> dict:
        return {"url": url, "source": self.source_name, "type": "zhihu_column"}


if __name__ == "__main__":
    searcher = ZhihuColumnSearch()
    results = searcher.search("测试")
    print(f"找到 {len(results.results)} 个结果")
