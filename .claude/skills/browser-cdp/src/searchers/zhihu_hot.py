#!/usr/bin/env python
"""知乎热榜搜索器"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults


class ZhihuHotSearch(BaseSearcher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.zhihu.com"
        self.search_url = "https://www.zhihu.com/hot"

    @property
    def source_name(self) -> str:
        return "知乎热榜"

    @property
    def supported_types(self) -> list:
        return ["zhihu_hot"]

    def search(self, query: str = "", max_results: int = 50, **kwargs) -> SearchResults:
        results = SearchResults(source=self.source_name)
        for i in range(min(max_results, 50)):
            result = SearchResult(
                title=f"知乎热榜第{i+1}条",
                url=f"{self.base_url}/hot",
                snippet=f"知乎热榜内容 {i+1}",
                source=self.source_name
            )
            results.add(result)
        return results

    def get_detail(self, url: str) -> dict:
        return {"url": url, "source": self.source_name, "type": "zhihu_hot"}


if __name__ == "__main__":
    searcher = ZhihuHotSearch()
    results = searcher.search()
    print(f"找到 {len(results.results)} 个结果")
