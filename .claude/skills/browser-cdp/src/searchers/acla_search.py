"""
中国法律援助网 (acla.org.cn) 搜索器
支持：法律援助、政策法规搜索
"""

import sys
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.searchers.base import BaseSearcher, SearchResult


class AclaSearcher(BaseSearcher):
    """中国法律援助网搜索器"""

    def __init__(self, **kwargs):
        super().__init__(
            name="acla",
            domain="acla.org.cn",
            description="中国法律援助网 - 法律援助、政策法规",
            **kwargs
        )
        self.base_url = "https://www.acla.org.cn"
        self.search_url = "https://www.acla.org.cn/search"

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[SearchResult]:
        """搜索法律援助和政策法规"""
        results = []
        try:
            # 搜索政策法规
            policy_results = self._search_policies(query, max_results)
            results.extend(policy_results)

            # 搜索法律援助
            aid_results = self._search_aid(query, max_results)
            results.extend(aid_results)

        except Exception as e:
            self.logger.error(f"搜索失败: {e}")

        return self._deduplicate(results)[:max_results]

    def _search_policies(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索政策法规"""
        results = []
        try:
            url = f"{self.base_url}/policy/search.aspx?keyword={query}"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.policy-item, .policy-list li, .news-list li')
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
                        snippet=f"政策法规: {title}",
                        source="中国法律援助网",
                        category="政策法规"
                    ))
        except Exception as e:
            self.logger.warning(f"政策搜索失败: {e}")
        return results

    def _search_aid(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索法律援助"""
        results = []
        try:
            url = f"{self.base_url}/aid/search.aspx?keyword={query}"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.aid-item, .aid-list li, .case-list li')
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
                        snippet=f"法律援助: {title}",
                        source="中国法律援助网",
                        category="法律援助"
                    ))
        except Exception as e:
            self.logger.warning(f"援助搜索失败: {e}")
        return results

    def get_policy_detail(self, url: str) -> Optional[Dict[str, Any]]:
        """获取政策详情"""
        try:
            self.nav.goto(url)
            time.sleep(2)

            title = self.nav.extract_text('h1, .policy-title, .news-title')
            content = self.nav.extract_text('div.content, .policy-content, #content')
            publish_date = self.nav.extract_text('.publish-date, .date, .time')

            return {
                "title": title,
                "content": content,
                "publish_date": publish_date,
                "url": url,
                "source": "中国法律援助网"
            }
        except Exception as e:
            self.logger.error(f"获取政策详情失败: {e}")
            return None

    def get_aid_info(self, aid_type: str = "all") -> List[Dict[str, Any]]:
        """获取法律援助信息"""
        results = []
        try:
            url = f"{self.base_url}/aid/{aid_type}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.aid-item, .aid-list li')
            for item in items:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                date_el = item.find('span.date, .date')

                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.base_url + link
                    date = date_el.text.strip() if date_el and date_el.text else ""

                    results.append({
                        "title": title,
                        "url": link,
                        "date": date,
                        "type": aid_type
                    })
        except Exception as e:
            self.logger.error(f"获取援助信息失败: {e}")
        return results


if __name__ == "__main__":
    searcher = AclaSearcher(headless=True)
    results = searcher.search("法律援助")
    print(json.dumps([r.to_dict() for r in results[:5]], ensure_ascii=False, indent=2))
