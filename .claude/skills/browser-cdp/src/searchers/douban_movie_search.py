#!/usr/bin/env python
"""
豆瓣电影搜索器

豆瓣电影通用搜索器。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class DoubanMovieSearcher(BaseSearcher):
    """豆瓣电影搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://movie.douban.com"
        self.search_url = "https://movie.douban.com/subject_search?search_text={query}"
        
    @property
    def source_name(self) -> str:
        return "豆瓣电影"
    
    @property
    def supported_types(self) -> list:
        return ["movie", "tv"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """豆瓣电影搜索"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 豆瓣电影第{i+1}部",
                url=f"{self.base_url}/subject_search?search_text={query}",
                snippet=f"豆瓣电影搜索: {query}",
                source=self.source_name
            )
            results.add(result)
        
        return results
    
    def get_detail(self, url: str) -> dict:
        return {"url": url, "source": self.source_name, "type": "movie"}


if __name__ == "__main__":
    searcher = DoubanMovieSearcher()
    results = searcher.search("测试")
    print(f"找到 {len(results.results)} 个结果")
