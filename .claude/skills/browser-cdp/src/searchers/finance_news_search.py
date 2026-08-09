"""
财经资讯搜索器
支持：财联社、证券时报、第一财经等财经媒体
"""

import sys
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.searchers.base import BaseSearcher, SearchResult


class FinanceNewsSearcher(BaseSearcher):
    """财经资讯搜索器"""

    def __init__(self, **kwargs):
        super().__init__(
            name="finance_news",
            domain="cls.cn",
            description="财经资讯 - 财联社、证券时报、第一财经",
            **kwargs
        )
        self.sources = {
            "cls": {
                "name": "财联社",
                "base_url": "https://www.cls.cn",
                "search_url": "https://www.cls.cn/searchPage?keyword={query}&type=all"
            },
            "stcn": {
                "name": "证券时报",
                "base_url": "https://www.stcn.com",
                "search_url": "https://www.stcn.com/search/#/search?keyword={query}"
            },
            "yicai": {
                "name": "第一财经",
                "base_url": "https://www.yicai.com",
                "search_url": "https://www.yicai.com/search?keyword={query}"
            }
        }

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[SearchResult]:
        """搜索财经资讯"""
        results = []
        try:
            # 搜索财联社
            cls_results = self._search_cls(query, max_results)
            results.extend(cls_results)

            # 搜索证券时报
            stcn_results = self._search_stcn(query, max_results)
            results.extend(stcn_results)

            # 搜索第一财经
            yicai_results = self._search_yicai(query, max_results)
            results.extend(yicai_results)

        except Exception as e:
            self.logger.error(f"搜索失败: {e}")

        return self._deduplicate(results)[:max_results]

    def _search_cls(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索财联社"""
        results = []
        try:
            url = self.sources["cls"]["search_url"].format(query=query)
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.news-item, .news-list li, .search-result')
            for item in items[:max_results]:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.sources["cls"]["base_url"] + link
                    results.append(SearchResult(
                        title=title,
                        url=link,
                        snippet=f"财联社: {title}",
                        source="财联社",
                        category="财经资讯"
                    ))
        except Exception as e:
            self.logger.warning(f"财联社搜索失败: {e}")
        return results

    def _search_stcn(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索证券时报"""
        results = []
        try:
            url = self.sources["stcn"]["search_url"].format(query=query)
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.news-item, .news-list li, .article-item')
            for item in items[:max_results]:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.sources["stcn"]["base_url"] + link
                    results.append(SearchResult(
                        title=title,
                        url=link,
                        snippet=f"证券时报: {title}",
                        source="证券时报",
                        category="财经资讯"
                    ))
        except Exception as e:
            self.logger.warning(f"证券时报搜索失败: {e}")
        return results

    def _search_yicai(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索第一财经"""
        results = []
        try:
            url = self.sources["yicai"]["search_url"].format(query=query)
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.news-item, .news-list li, .article-item')
            for item in items[:max_results]:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.sources["yicai"]["base_url"] + link
                    results.append(SearchResult(
                        title=title,
                        url=link,
                        snippet=f"第一财经: {title}",
                        source="第一财经",
                        category="财经资讯"
                    ))
        except Exception as e:
            self.logger.warning(f"第一财经搜索失败: {e}")
        return results

    def get_latest_news(self, source: str = "cls", limit: int = 20) -> List[Dict[str, Any]]:
        """获取最新财经新闻"""
        results = []
        try:
            if source == "cls":
                url = f"{self.sources['cls']['base_url']}/telegraph"
            elif source == "stcn":
                url = f"{self.sources['stcn']['base_url']}/"
            elif source == "yicai":
                url = f"{self.sources['yicai']['base_url']}/"
            else:
                return results

            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.news-item, .news-list li, .article-item')
            for item in items[:limit]:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                time_el = item.find('span.time, .time, .date')

                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.sources[source]["base_url"] + link
                    time_str = time_el.text.strip() if time_el and time_el.text else ""

                    results.append({
                        "title": title,
                        "url": link,
                        "time": time_str,
                        "source": source
                    })
        except Exception as e:
            self.logger.error(f"获取新闻失败: {e}")
        return results


if __name__ == "__main__":
    searcher = FinanceNewsSearcher(headless=True)
    results = searcher.search("A股")
    print(json.dumps([r.to_dict() for r in results[:5]], ensure_ascii=False, indent=2))
