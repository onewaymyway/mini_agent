#!/usr/bin/env python
"""
酷狗音乐搜索器

搜索酷狗音乐平台的歌曲、歌手、专辑信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class KugouSearcher(BaseSearcher):
    """酷狗音乐搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.kugou.com"
        self.search_url = "https://search.kugou.com/home/"
        
    @property
    def source_name(self) -> str:
        return "酷狗音乐"
    
    @property
    def supported_types(self) -> list:
        return ["music", "song", "singer", "album"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索酷狗音乐"""
        results = SearchResults(source=self.source_name)
        
        # 模拟搜索结果
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 酷狗音乐第{i+1}首",
                url=f"{self.base_url}/song/{i+1000}",
                snippet=f"酷狗音乐搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        """获取歌曲详情"""
        return {
            "url": url,
            "source": self.source_name,
            "type": "music"
        }


if __name__ == "__main__":
    searcher = KugouSearcher()
    results = searcher.search("晴天")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
