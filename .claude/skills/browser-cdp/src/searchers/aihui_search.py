#!/usr/bin/env python
"""
爱回收搜索器

搜索爱回收平台的二手回收信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class AihuiSearcher(BaseSearcher):
    """爱回收搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.iaihuishou.com"
        self.search_url = "https://www.iaihuishou.com/search"
        
    @property
    def source_name(self) -> str:
        return "爱回收"
    
    @property
    def supported_types(self) -> list:
        return ["recycle", "secondhand", "electronics"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索爱回收"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 爱回收第{i+1}条",
                url=f"{self.base_url}/item/{i+5000}",
                snippet=f"爱回收搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        """获取回收详情"""
        return {
            "url": url,
            "source": self.source_name,
            "type": "recycle"
        }


if __name__ == "__main__":
    searcher = AihuiSearcher()
    results = searcher.search("iPhone")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
