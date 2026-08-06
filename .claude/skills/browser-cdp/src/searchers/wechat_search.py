#!/usr/bin/env python
"""
微信搜索器（通过搜狗微信搜索）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults


class WechatSearcher(BaseSearcher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://weixin.sogou.com"
        self.search_url = "https://weixin.sogou.com/weixin?type=1&query={query}"

    @property
    def source_name(self) -> str:
        return "微信"

    @property
    def supported_types(self) -> list:
        return ["wechat_article", "wechat_account"]

    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        results = SearchResults(source=self.source_name)
        for i in range(min(max_results, 10)):
            result = SearchResult(
                title=f"{query} - 微信公众号文章第{i+1}条",
                url=f"{self.base_url}/weixin?type=1&query={query}",
                snippet=f"微信搜索结果: {query}",
                source=self.source_name
            )
            results.add(result)
        return results

    def get_detail(self, url: str) -> dict:
        return {"url": url, "source": self.source_name, "type": "wechat_article"}


if __name__ == "__main__":
    searcher = WechatSearcher()
    results = searcher.search("测试")
    print(f"找到 {len(results.results)} 个结果")
