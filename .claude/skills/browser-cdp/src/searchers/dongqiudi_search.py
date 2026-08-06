#!/usr/bin/env python
"""
dongqiudi_search.py - 懂球帝体育搜索器

使用 browser-cdp skill 搜索懂球帝，获取足球新闻、赛事数据、球员信息等。

用法:
    python dongqiudi_search.py "梅西"
    python dongqiudi_search.py "欧冠" --type news --output-dir ./dongqiudi_results
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


# ========== 懂球帝专用配置 ==========
DQD_BASE = "https://www.dongqiudi.com"
DQD_NEWS_URL = f"{DQD_BASE}/news"
DQD_SEARCH_URL = f"{DQD_BASE}/search"
DQD_MATCH_URL = f"{DQD_BASE}/match"
DQD_PLAYER_URL = f"{DQD_BASE}/player"

# 默认输出目录
DQD_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "dongqiudi"


class DongqiudiSearcher(BaseSearcher):
    """懂球帝体育搜索器"""

    @property
    def source_name(self) -> str:
        return "dongqiudi"

    @property
    def supported_types(self) -> List[str]:
        return ["news_search", "match_data", "player_info", "team_info"]

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
        """搜索懂球帝内容

        Args:
            query: 搜索关键词
            search_type: 搜索类型 (news/match/player/team/all)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        print(f"[懂球帝] 正在搜索: {query}")

        results = []
        browser = None

        try:
            browser = ensure_browser(port=port, stealth=stealth)

            if search_type in ["news", "all"]:
                results.extend(self._search_news(browser, query, max_results, wait_timeout))

            if search_type in ["match", "all"]:
                results.extend(self._search_matches(browser, query, max_results, wait_timeout))

            if search_type in ["player", "all"]:
                results.extend(self._search_players(browser, query, max_results, wait_timeout))

            # 去重
            seen_urls = set()
            unique_results = []
            for r in results:
                if r.get("url") and r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    unique_results.append(r)

            results = unique_results[:max_results]

            # 保存结果
            output_path = output_dir or str(DQD_OUTPUT_DIR)
            save_results(results, output_path, source=self.source_name)

            print(f"[懂球帝] 搜索完成，共获取 {len(results)} 条结果")
            return results

        except Exception as e:
            print(f"[懂球帝] 搜索失败: {e}")
            return []
        finally:
            if browser:
                browser.close()

    def _search_news(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        """搜索足球新闻"""
        results = []
        try:
            url = f"{DQD_SEARCH_URL}?keyword={quote(query)}"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))

            news_items = browser.query_selector_all(".news-item, .article-item, [class*='news'], [class*='article']")
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
                                "url": link if link.startswith("http") else f"{DQD_BASE}{link}",
                                "source": self.source_name,
                                "type": "news",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[懂球帝新闻搜索] 错误: {e}")
        return results

    def _search_matches(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        """搜索赛事数据"""
        results = []
        try:
            url = f"{DQD_MATCH_URL}?keyword={quote(query)}"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))

            match_items = browser.query_selector_all(".match-item, .game-item, [class*='match']")
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
                                "url": link if link.startswith("http") else f"{DQD_BASE}{link}",
                                "source": self.source_name,
                                "type": "match",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[懂球帝赛事搜索] 错误: {e}")
        return results

    def _search_players(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        """搜索球员信息"""
        results = []
        try:
            url = f"{DQD_PLAYER_URL}?keyword={quote(query)}"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))

            player_items = browser.query_selector_all(".player-item, .athlete-item, [class*='player']")
            for item in player_items[:max_results]:
                try:
                    title_elem = item.query_selector(".name, .player-name, h4")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{DQD_BASE}{link}",
                                "source": self.source_name,
                                "type": "player",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[懂球帝球员搜索] 错误: {e}")
        return results

    def health_check(self, port: int = 9333) -> Dict:
        """健康检查"""
        return {
            "source": self.source_name,
            "status": "healthy",
            "supported_types": self.supported_types,
            "base_url": DQD_BASE,
        }

    def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取详情页内容"""
        return {"url": url, "title": "", "content": "", "source": self.source_name}

    def close(self):
        """关闭搜索器"""
        pass


def main():
    parser = argparse.ArgumentParser(description="懂球帝体育搜索器")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", default="all", choices=["news", "match", "player", "all"],
                        help="搜索类型")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口")
    parser.add_argument("--output-dir", default=None, help="输出目录")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数")
    parser.add_argument("--no-stealth", action="store_true", help="不使用反检测模式")

    args = parser.parse_args()

    searcher = DongqiudiSearcher()
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