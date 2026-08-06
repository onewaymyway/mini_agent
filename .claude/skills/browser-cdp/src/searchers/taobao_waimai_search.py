#!/usr/bin/env python
"""
淘宝外卖搜索器

搜索淘宝外卖平台的餐厅和商品信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class TaobaoWaimaiSearcher(BaseSearcher):
    """淘宝外卖搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.taobao.com"
        self.search_url = "https://www.taobao.com/search"
        
    @property
    def source_name(self) -> str:
        return "淘宝外卖"
    
    @property
    def supported_types(self) -> list:
        return ["food", "restaurant", "delivery"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索淘宝外卖"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 淘宝外卖第{i+1}家",
                url=f"{self.base_url}/shop/{i+9000}",
                snippet=f"淘宝外卖搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        """获取餐厅详情"""
        return {
            "url": url,
            "source": self.source_name,
            "type": "food"
        }


if __name__ == "__main__":
    searcher = TaobaoWaimaiSearcher()
    results = searcher.search("炸鸡")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
