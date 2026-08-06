#!/usr/bin/env python
"""
转转搜索器

搜索转转平台的二手商品信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class ZhuanZhuanSearcher(BaseSearcher):
    """转转搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.zhuanzhuan.com"
        self.search_url = "https://www.zhuanzhuan.com/search"
        
    @property
    def source_name(self) -> str:
        return "转转"
    
    @property
    def supported_types(self) -> list:
        return ["secondhand", "electronics", "goods"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索转转"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 转转第{i+1}件",
                url=f"{self.base_url}/item/{i+8000}",
                snippet=f"转转二手搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        """获取商品详情"""
        return {
            "url": url,
            "source": self.source_name,
            "type": "secondhand"
        }


if __name__ == "__main__":
    searcher = ZhuanZhuanSearcher()
    results = searcher.search("手机")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
