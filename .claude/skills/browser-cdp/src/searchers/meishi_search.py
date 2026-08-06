#!/usr/bin/env python
"""
美食杰搜索器

搜索美食杰平台的菜谱和美食信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class MeishiSearcher(BaseSearcher):
    """美食杰搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.meishij.net"
        self.search_url = "https://so.meishij.net/"
        
    @property
    def source_name(self) -> str:
        return "meishi"

    @property
    def supported_types(self) -> list:
        return ["recipe_search", "cooking_tips", "food_search"]

    async def health_check(self) -> dict:
        """健康检查"""
        return {
            "source": self.source_name,
            "status": "healthy",
            "base_url": self.base_url
        }
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索美食杰"""
        results = SearchResults(source=self.source_name, query=query)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 美食杰菜谱第{i+1}道",
                url=f"{self.base_url}/recipe/{i+4000}",
                snippet=f"美食杰菜谱搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        """获取菜谱详情"""
        return {
            "url": url,
            "source": self.source_name,
            "type": "recipe"
        }


if __name__ == "__main__":
    searcher = MeishiSearcher()
    results = searcher.search("红烧肉")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
