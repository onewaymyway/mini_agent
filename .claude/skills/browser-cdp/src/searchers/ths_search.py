"""
同花顺 (10jqka.com.cn) 搜索器
支持：股票行情、财经资讯、资金流向、板块涨跌搜索
"""

import sys
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.searchers.base import BaseSearcher, SearchResult


class ThsSearcher(BaseSearcher):
    """同花顺搜索器"""

    def __init__(self, **kwargs):
        super().__init__(
            name="ths",
            domain="10jqka.com.cn",
            description="同花顺 - 股票行情、财经资讯、资金流向",
            **kwargs
        )
        self.base_url = "https://www.10jqka.com.cn"
        self.quote_url = "https://q.10jqka.com.cn"
        self.news_url = "https://news.10jqka.com.cn"

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[SearchResult]:
        """搜索股票、资讯、资金流向"""
        results = []
        try:
            # 搜索股票行情
            stock_results = self._search_stocks(query, max_results)
            results.extend(stock_results)

            # 搜索财经资讯
            news_results = self._search_news(query, max_results)
            results.extend(news_results)

            # 搜索资金流向
            fund_results = self._search_fund_flow(query, max_results)
            results.extend(fund_results)

        except Exception as e:
            self.logger.error(f"搜索失败: {e}")

        return self._deduplicate(results)[:max_results]

    def _search_stocks(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索股票行情"""
        results = []
        try:
            url = f"{self.quote_url}search/stock/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.stock-item, .stock-list li, .hq-table tr')
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
                        snippet=f"股票行情: {title}",
                        source="同花顺",
                        category="股票行情"
                    ))
        except Exception as e:
            self.logger.warning(f"股票搜索失败: {e}")
        return results

    def _search_news(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索财经资讯"""
        results = []
        try:
            url = f"{self.news_url}/search/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.news-item, .news-list li, .article-list li')
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
                        snippet=f"财经资讯: {title}",
                        source="同花顺",
                        category="财经资讯"
                    ))
        except Exception as e:
            self.logger.warning(f"资讯搜索失败: {e}")
        return results

    def _search_fund_flow(self, query: str, max_results: int) -> List[SearchResult]:
        """搜索资金流向"""
        results = []
        try:
            url = f"{self.base_url}/zjlx/{query}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.fund-item, .fund-list li, .zjlx-table tr')
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
                        snippet=f"资金流向: {title}",
                        source="同花顺",
                        category="资金流向"
                    ))
        except Exception as e:
            self.logger.warning(f"资金流向搜索失败: {e}")
        return results

    def get_stock_quote(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """获取股票行情"""
        try:
            url = f"{self.quote_url}stock/{stock_code}.html"
            self.nav.goto(url)
            time.sleep(2)

            name = self.nav.extract_text('h1, .stock-name, .hq-name')
            price = self.nav.extract_text('.price, .hq-price, .current-price')
            change = self.nav.extract_text('.change, .hq-change, .price-change')
            volume = self.nav.extract_text('.volume, .hq-volume')
            turnover = self.nav.extract_text('.turnover, .hq-turnover')

            return {
                "code": stock_code,
                "name": name,
                "price": price,
                "change": change,
                "volume": volume,
                "turnover": turnover,
                "url": url,
                "source": "同花顺"
            }
        except Exception as e:
            self.logger.error(f"获取行情失败: {e}")
            return None

    def get_sector_list(self, sector_type: str = "all") -> List[Dict[str, Any]]:
        """获取板块列表"""
        results = []
        try:
            url = f"{self.base_url}/block/{sector_type}/"
            self.nav.goto(url)
            time.sleep(2)

            items = self.nav.extract_elements('div.sector-item, .sector-list li, .block-table tr')
            for item in items:
                title_el = item.find('a')
                link_el = item.find('a', has_attribute='href')
                change_el = item.find('span.change, .change')

                if title_el and link_el:
                    title = title_el.text.strip() if title_el.text else link_el.get('title', '')
                    link = link_el.get('href', '')
                    if link and not link.startswith('http'):
                        link = self.base_url + link
                    change = change_el.text.strip() if change_el and change_el.text else ""

                    results.append({
                        "name": title,
                        "url": link,
                        "change": change,
                        "type": sector_type
                    })
        except Exception as e:
            self.logger.error(f"获取板块列表失败: {e}")
        return results


if __name__ == "__main__":
    searcher = ThsSearcher(headless=True)
    results = searcher.search("茅台")
    print(json.dumps([r.to_dict() for r in results[:5]], ensure_ascii=False, indent=2))
