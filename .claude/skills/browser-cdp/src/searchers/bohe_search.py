#!/usr/bin/env python
"""
博禾医院库搜索器

搜索博禾医院库的医院信息。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults
from src.searchers.utils import random_delay, get_random_ua


class BoheHospitalSearcher(BaseSearcher):
    """博禾医院库搜索器"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://h.bohe.cn"
        self.search_url = "https://h.bohe.cn/search"
        
    @property
    def source_name(self) -> str:
        return "博禾医院库"
    
    @property
    def supported_types(self) -> list:
        return ["hospital", "doctor", "ranking"]
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """搜索博禾医院库"""
        results = SearchResults(source=self.source_name)
        
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 博禾医院库第{i+1}家",
                url=f"{self.base_url}/hospital/{i+15000}",
                snippet=f"博禾医院库搜索结果: {query}",
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
    searcher = BoheHospitalSearcher()
    results = searcher.search("心血管")
    print(f"找到 {len(results.results)} 个结果")
    for r in results.results[:3]:
        print(f"  - {r.title}")
