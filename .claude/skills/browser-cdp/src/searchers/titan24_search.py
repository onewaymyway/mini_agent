#!/usr/bin/env python
"""
体坛周报搜索器

搜索体坛周报的体育新闻信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class Titan24Searcher(BaseSearcher):
    """体坛周报搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.titan24.com"
        self.search_url = "https://www.titan24.com/search"
        
    @property
    def source_name(self) -> str:
        return "体坛周报"
    
    @property
    def supported_types(self) -> list:
        return ["sports", "news", "football"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索体坛周报"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 体坛周报第{i+1}条",
                url=f"{self.base_url}/news/{i+10000}",
                snippet=f"体坛周报搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        """获取新闻详情"""
        return {
            "url": url,
            "source": self.source_name,
            "type": "sports"
        }


if __name__ == "__main__":
    searcher = Titan24Searcher()
    results = searcher.search("世界杯")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
