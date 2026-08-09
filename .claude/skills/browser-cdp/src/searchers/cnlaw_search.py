"""
中国法律网 (cnlaw.com.cn) 搜索器
支持：法律法规、司法案例、法律咨询搜索
"""

import sys
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.searchers.base import BaseSearcher, SearchResult


class CnLawSearcher(BaseSearcher):
    """中国法律网搜索器"""

    def __init__(self, **kwargs):
        super().__init__(
            name="cnlaw",
            domain="cnlaw.com.cn",
            description="中国法律网 - 法律法规、司法案例、法律咨询",
            **kwargs
        )
        self.base_url = "https://www.cnlaw.com.cn"
        self.search_url = "https://www.cnlaw.com.cn/search.aspx"

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[SearchResult]:
        """搜索法律法规、案例、咨询"""
        results = []
        try:
            # 搜索法律法规
            law_results = self._search_laws(query, max_results)
            results.extend(law_results)

            # 搜索案例
            case_results = self._search_cases(query, max_results)
            results.extend(case_results)

            # 搜索咨询
            consult_results = self._search_consult(query, max_results)
            results.extend(consult_results)

        except Exception as e:
            self.logger.error(f"搜索失败: {e}")

        return self._deduplicate(results)[:max_results]

    def _search_laws(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索法律法规"""
        results = []
        try:
            url = f"{self.base_url}/law/search.aspx?keyword={query}"
            self.nav.goto(url)
            time.sleep(2)

            # 提取搜索结果
            items = self.nav.extract_elements('div.result-item, .law-list li, .search-result')
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
                        snippet=f"法律法规: {title}",
                        source="中国法律网",
                        category="法律法规"
                    ))
        except Exception as e:
            self.logger.warning(f"法律法规搜索失败: {e}")
        return results

    def _search_cases(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索司法案例"""
        results = []
        try:
            url = f"{self.base_url}/case/search.aspx?keyword={query}"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.case-item, .case-list li, .case-result')
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
                        snippet=f"司法案例: {title}",
                        source="中国法律网",
                        category="司法案例"
                    ))
        except Exception as e:
            self.logger.warning(f"案例搜索失败: {e}")
        return results

    def _search_consult(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索法律咨询"""
        results = []
        try:
            url = f"{self.base_url}/consult/search.aspx?keyword={query}"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.consult-item, .consult-list li')
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
                        source="中国法律网",
                        category="法律咨询"
                    ))
        except Exception as e:
            self.logger.warning(f"咨询搜索失败: {e}")
        return results

    def get_law_detail(self, url: str) -> Optional[Dict[str, Any]]:
        """获取法律条文详情"""
        try:
            self.nav.goto(url)
            time.sleep(2)

            content = self.nav.extract_text('div.content, .law-content, #content')
            title = self.nav.extract_text('h1, .title, h2')

            return {
                "title": title,
                "content": content,
                "url": url,
                "source": "中国法律网"
            }
        except Exception as e:
            self.logger.error(f"获取详情失败: {e}")
            return None

    def get_law_list(self, law_type: str = "all", page: int = 1) -> List[Dict[str, Any]]:
        """获取法律条文列表"""
        results = []
        try:
            url = f"{self.base_url}/law/list.aspx?type={law_type}&page={page}"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.law-item, .law-list li')
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
                        "type": law_type
                    })
        except Exception as e:
            self.logger.error(f"获取法律列表失败: {e}")
        return results


if __name__ == "__main__":
    searcher = CnLawSearcher(headless=True)
    results = searcher.search("合同法")
    print(json.dumps([r.to_dict() for r in results[:5]], ensure_ascii=False, indent=2))
