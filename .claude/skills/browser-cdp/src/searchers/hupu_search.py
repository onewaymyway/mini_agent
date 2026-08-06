#!/usr/bin/env python
"""
hupu_search.py - 虎扑体育搜索器

使用 browser-cdp skill 搜索虎扑体育，获取体育新闻、赛事数据、社区内容等。

用法:
    python hupu_search.py "NBA"
    python hupu_search.py "足球" --type news --output-dir ./hupu_results
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import random_delay, save_results
from src.searchers.baidu_search import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 虎扑专用配置 ==========
HUPU_BASE = "https://www.hupu.com"
HUPU_NEWS_URL = f"{HUPU_BASE}/nba"
HUPU_SEARCH_URL = f"{HUPU_BASE}/search"
HUPU_BASKETBALL_URL = f"{HUPU_BASE}/nba"
HUPU_FOOTBALL_URL = f"{HUPU_BASE}/football"

# 默认输出目录
HUPU_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "hupu"


class HupuSearcher(BaseSearcher):
    """虎扑体育搜索器"""

    @property
    def source_name(self) -> str:
        return "hupu"

    @property
    def supported_types(self) -> List[str]:
        return ["news_search", "match_data", "community_post", "player_stats"]

    def search(
        self,
        query: str,
        search_type: str = "all",
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        max_results: int = 20,
    ) -> List[Dict]:
        """搜索虎扑体育内容"""
        print(f"[虎扑] 正在搜索: {query}")
        results = []
        browser = None
        try:
            browser = ensure_browser(port=port, stealth=stealth)
            if search_type in ["news", "all"]:
                results.extend(self._search_news(browser, query, max_results, wait_timeout))
            if search_type in ["match", "all"]:
                results.extend(self._search_matches(browser, query, max_results, wait_timeout))
            if search_type in ["community", "all"]:
                results.extend(self._search_community(browser, query, max_results, wait_timeout))
            seen_urls = set()
            unique_results = []
            for r in results:
                if r.get("url") and r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    unique_results.append(r)
            results = unique_results[:max_results]
            output_path = output_dir or str(HUPU_OUTPUT_DIR)
            save_results(results, output_path, source=self.source_name)
            print(f"[虎扑] 搜索完成，共获取 {len(results)} 条结果")
            return results
        except Exception as e:
            print(f"[虎扑] 搜索失败: {e}")
            return []
        finally:
            if browser:
                browser.close()

    def _search_news(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{HUPU_SEARCH_URL}?keyword={quote(query)}&type=news"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            news_items = browser.query_selector_all(".news-item, .post-item")
            for item in news_items[:max_results]:
                try:
                    title_elem = item.query_selector(".title, h3, h2, a")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{HUPU_BASE}{link}",
                                "source": self.source_name,
                                "type": "news",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[虎扑新闻搜索] 错误: {e}")
        return results

    def _search_matches(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{HUPU_SEARCH_URL}?keyword={quote(query)}&type=match"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            match_items = browser.query_selector_all(".match-item, .game-item")
            for item in match_items[:max_results]:
                try:
                    title_elem = item.query_selector(".title, .team-name, h4")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{HUPU_BASE}{link}",
                                "source": self.source_name,
                                "type": "match",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[虎扑赛事搜索] 错误: {e}")
        return results

    def _search_community(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{HUPU_SEARCH_URL}?keyword={quote(query)}&type=post"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            post_items = browser.query_selector_all(".post-item, .thread-item")
            for item in post_items[:max_results]:
                try:
                    title_elem = item.query_selector(".title, .post-title, h3")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{HUPU_BASE}{link}",
                                "source": self.source_name,
                                "type": "community",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[虎扑社区搜索] 错误: {e}")
        return results

    def health_check(self, port: int = 9333) -> Dict:
        return {
            "source": self.source_name,
            "status": "healthy",
            "supported_types": self.supported_types,
            "base_url": HUPU_BASE,
        }

    def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取详情页内容"""
        return {"url": url, "title": "", "content": "", "source": self.source_name}

    def close(self):
        pass


def main():
    parser = argparse.ArgumentParser(description="虎扑体育搜索器")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", default="all", choices=["news", "match", "community", "all"])
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--no-stealth", action="store_true")
    args = parser.parse_args()
    searcher = HupuSearcher()
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        port=args.port,
        stealth=not args.no_stealth,
        output_dir=args.output_dir,
        max_results=args.max_results,
    )
    if results:
        print(f"\n找到 {len(results)} 条结果:")
        for i, r in enumerate(results[:10], 1):
            print(f"{i}. {r.get('title', 'N/A')}")
            print(f"   URL: {r.get('url', 'N/A')}")
            print()


if __name__ == "__main__":
    main()
