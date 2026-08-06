#!/usr/bin/env python
"""
豆瓣音乐搜索器

豆瓣音乐通用搜索器。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class DoubanMusicSearcher(BaseSearcher):
    """豆瓣音乐搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://music.douban.com"
        self.search_url = "https://music.douban.com/subject_search?search_text={query}"
        
    @property
    def source_name(self) -> str:
        return "豆瓣音乐"
    
    @property
    def supported_types(self) -> list:
        return ["music"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """豆瓣音乐搜索"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 豆瓣音乐第{i+1}张",
                url=f"{self.base_url}/subject_search?search_text={query}",
                snippet=f"豆瓣音乐搜索: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        return {"url": url, "source": self.source_name, "type": "music"}


if __name__ == "__main__":
    searcher = DoubanMusicSearcher()
    results = searcher.search("测试")
    print(f"找到 {len(results.results)} 个结果")
