"""
华律网 (66law.cn) 搜索器
支持：律师查询、法律咨询、法律知识搜索
"""

import sys
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.searchers.base import BaseSearcher, SearchResult


class CnLawyerSearcher(BaseSearcher):
    """华律网搜索器"""

    def __init__(self, **kwargs):
        super().__init__(
            name="cnlawyer",
            domain="66law.cn",
            description="华律网 - 律师查询、法律咨询、法律知识",
            **kwargs
        )
        self.base_url = "https://www.66law.cn"
        self.search_url = "https://so.66law.cn/"

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[SearchResult]:
        """搜索律师、咨询、知识"""
        results = []
        try:
            # 搜索法律知识
            knowledge_results = self._search_knowledge(query, max_results)
            results.extend(knowledge_results)

            # 搜索律师
            lawyer_results = self._search_lawyers(query, max_results)
            results.extend(lawyer_results)

            # 搜索咨询
            consult_results = self._search_consult(query, max_results)
            results.extend(consult_results)

        except Exception as e:
            self.logger.error(f"搜索失败: {e}")

        return self._deduplicate(results)[:max_results]

    def _search_knowledge(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索法律知识"""
        results = []
        try:
            url = f"{self.base_url}/zs/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.article-item, .knowledge-list li, .zs-list li')
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
                        snippet=f"法律知识: {title}",
                        source="华律网",
                        category="法律知识"
                    ))
        except Exception as e:
            self.logger.warning(f"知识搜索失败: {e}")
        return results

    def _search_lawyers(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索律师"""
        results = []
        try:
            url = f"{self.base_url}/lvshi/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.lawyer-item, .lawyer-list li, .lvshi-list li')
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
                        snippet=f"律师: {title}",
                        source="华律网",
                        category="律师查询"
                    ))
        except Exception as e:
            self.logger.warning(f"律师搜索失败: {e}")
        return results

    def _search_consult(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索法律咨询"""
        results = []
        try:
            url = f"{self.base_url}/ask/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.ask-item, .ask-list li, .consult-list li')
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
                        source="华律网",
                        category="法律咨询"
                    ))
        except Exception as e:
            self.logger.warning(f"咨询搜索失败: {e}")
        return results

    def get_lawyer_info(self, url: str) -> Optional[Dict[str, Any]]:
        """获取律师详细信息"""
        try:
            self.nav.goto(url)
            time.sleep(2)

            name = self.nav.extract_text('h1, .lawyer-name, .lvshi-name')
            title = self.nav.extract_text('.lawyer-title, .lvshi-title')
            company = self.nav.extract_text('.lawyer-company, .lvshi-company')
            intro = self.nav.extract_text('div.intro, .lawyer-intro, #content')

            return {
                "name": name,
                "title": title,
                "company": company,
                "introduction": intro,
                "url": url,
                "source": "华律网"
            }
        except Exception as e:
            self.logger.error(f"获取律师信息失败: {e}")
            return None

    def get_law_list(self, law_type: str = "all", page: int = 1) -> List[Dict[str, Any]]:
        """获取法律知识列表"""
        results = []
        try:
            url = f"{self.base_url}/zs/{law_type}/page{page}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.article-item, .knowledge-list li')
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
            self.logger.error(f"获取知识列表失败: {e}")
        return results


if __name__ == "__main__":
    searcher = CnLawyerSearcher(headless=True)
    results = searcher.search("离婚")
    print(json.dumps([r.to_dict() for r in results[:5]], ensure_ascii=False, indent=2))
