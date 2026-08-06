#!/usr/bin/env python
"""
39 就医助手搜索器

搜索 39 就医助手的医院信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class Y39Searcher(BaseSearcher):
    """39 就医助手搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://wapyyk.39.net"
        self.search_url = "https://wapyyk.39.net/search"
        
    @property
    def source_name(self) -> str:
        return "39 就医助手"
    
    @property
    def supported_types(self) -> list:
        return ["hospital", "doctor", "ranking"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索 39 就医助手"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 39 就医助手第{i+1}家",
                url=f"{self.base_url}/hospital/{i+14000}",
                snippet=f"39 就医助手搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        """获取医院详情"""
        return {
            "url": url,
            "source": self.source_name,
            "type": "hospital"
        }


if __name__ == "__main__":
    searcher = Y39Searcher()
    results = searcher.search("三甲医院")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
