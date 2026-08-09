"""
华律师 (haolvshi.com) 搜索器
支持：法律咨询、律师服务搜索
"""

import sys
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.searchers.base import BaseSearcher, SearchResult


class HaolvshiSearcher(BaseSearcher):
    """华律师搜索器"""

    def __init__(self, **kwargs):
        super().__init__(
            name="haolvshi",
            domain="haolvshi.com",
            description="华律师 - 法律咨询、律师服务",
            **kwargs
        )
        self.base_url = "https://www.haolvshi.com"
        self.search_url = "https://www.haolvshi.com/search"

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[SearchResult]:
        """搜索法律咨询和律师服务"""
        results = []
        try:
            # 搜索法律咨询
            consult_results = self._search_consult(query, max_results)
            results.extend(consult_results)

            # 搜索律师服务
            service_results = self._search_services(query, max_results)
            results.extend(service_results)

        except Exception as e:
            self.logger.error(f"搜索失败: {e}")

        return self._deduplicate(results)[:max_results]

    def _search_consult(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索法律咨询"""
        results = []
        try:
            url = f"{self.base_url}/ask/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.ask-item, .consult-list li, .ask-result')
            for item in items[:max_results]:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.base_url + link
                    results.append(SearchResult(
                        title=title,
                        url=link,
                        snippet=f"法律咨询: {title}",
                        source="华律师",
                        category="法律咨询"
                    ))
        except Exception as e:
            self.logger.warning(f"咨询搜索失败: {e}")
        return results

    def _search_services(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索律师服务"""
        results = []
        try:
            url = f"{self.base_url}/service/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.service-item, .service-list li')
            for item in items[:max_results]:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.base_url + link
                    results.append(SearchResult(
                        title=title,
                        url=link,
                        snippet=f"律师服务: {title}",
                        source="华律师",
                        category="律师服务"
                    ))
        except Exception as e:
            self.logger.warning(f"服务搜索失败: {e}")
        return results

    def get_consult_detail(self, url: str) -> Optional[Dict[str, Any]]:
        """获取咨询详情"""
        try:
            self.nav.goto(url)
            time.sleep(2)

            title = self.nav.extract_text('h1, .ask-title, .consult-title')
            content = self.nav.extract_text('div.content, .ask-content, #content')
            ask_time = self.nav.extract_text('.ask-time, .time, .date')

            return {
                "title": title,
                "content": content,
                "ask_time": ask_time,
                "url": url,
                "source": "华律师"
            }
        except Exception as e:
            self.logger.error(f"获取咨询详情失败: {e}")
            return None

    def get_service_list(self, service_type: str = "all", page: int = 1) -> List[Dict[str, Any]]:
        """获取服务列表"""
        results = []
        try:
            url = f"{self.base_url}/service/{service_type}/page{page}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.service-item, .service-list li')
            for item in items:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                price_el = item.find('span.price, .price')

                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.base_url + link
                    price = price_el.text.strip() if price_el and price_el.text else ""

                    results.append({
                        "title": title,
                        "url": link,
                        "price": price,
                        "type": service_type
                    })
        except Exception as e:
            self.logger.error(f"获取服务列表失败: {e}")
        return results


if __name__ == "__main__":
    searcher = HaolvshiSearcher(headless=True)
    results = searcher.search("劳动纠纷")
    print(json.dumps([r.to_dict() for r in results[:5]], ensure_ascii=False, indent=2))
