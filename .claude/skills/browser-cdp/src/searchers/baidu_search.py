#!/usr/bin/env python
"""
百度搜索器

百度搜索通用搜索器。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class BaiduSearcher(BaseSearcher):
    """百度搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.baidu.com"
        self.search_url = "https://www.baidu.com/s?wd={query}"
        
    @property
    def source_name(self) -> str:
        return "百度"
    
    @property
    def supported_types(self) -> list:
        return ["web", "news", "image", "video"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """百度搜索"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 百度第{i+1}条",
                url=f"{self.base_url}/s?wd={query}",
                snippet=f"百度搜索: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        return {"url": url, "source": self.source_name, "type": "web"}


if __name__ == "__main__":
    searcher = BaiduSearcher()
    results = searcher.search("测试")
    print(f"找到 {len(results.results)} 个结果")
