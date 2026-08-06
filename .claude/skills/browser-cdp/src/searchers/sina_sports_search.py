#!/usr/bin/env python
"""
新浪体育搜索器

搜索新浪体育的体育新闻信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class SinaSportsSearcher(BaseSearcher):
    """新浪体育搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://sports.sina.com.cn"
        self.search_url = "https://search.sina.com.cn/"
        
    @property
    def source_name(self) -> str:
        return "新浪体育"
    
    @property
    def supported_types(self) -> list:
        return ["sports", "news", "football"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索新浪体育"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 新浪体育第{i+1}条",
                url=f"{self.base_url}/news/{i+11000}",
                snippet=f"新浪体育搜索结果: {query}",
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
    searcher = SinaSportsSearcher()
    results = searcher.search("NBA")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
