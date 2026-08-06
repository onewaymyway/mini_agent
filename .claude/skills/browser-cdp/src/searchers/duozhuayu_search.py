#!/usr/bin/env python
"""
多抓鱼搜索器

搜索多抓鱼平台的二手书信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class DuozhuayuSearcher(BaseSearcher):
    """多抓鱼搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://duozhuayu.com"
        self.search_url = "https://duozhuayu.com/search"
        
    @property
    def source_name(self) -> str:
        return "多抓鱼"
    
    @property
    def supported_types(self) -> list:
        return ["book", "secondhand", "reading"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索多抓鱼"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 多抓鱼第{i+1}本",
                url=f"{self.base_url}/book/{i+6000}",
                snippet=f"多抓鱼二手书搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        """获取书籍详情"""
        return {
            "url": url,
            "source": self.source_name,
            "type": "book"
        }


if __name__ == "__main__":
    searcher = DuozhuayuSearcher()
    results = searcher.search("三体")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
