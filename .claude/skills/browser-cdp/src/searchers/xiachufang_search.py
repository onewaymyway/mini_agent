#!/usr/bin/env python
"""
xiachufang_search.py - 下厨房搜索器

使用 browser-cdp skill 搜索下厨房，获取菜谱、烹饪技巧、美食教程等。

用法:
    python xiachufang_search.py "红烧肉"
    python xiachufang_search.py "蛋糕" --type recipe --output-dir ./xcf_results
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


# ========== 下厨房专用配置 ==========
XCF_BASE = "https://www.xiachufang.com"
XCF_SEARCH_URL = f"{XCF_BASE}/explore/"
XCF_RECIPE_URL = f"{XCF_BASE}/recipe"

# 默认输出目录
XCF_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "xiachufang"


class XiachufangSearcher(BaseSearcher):
    """下厨房搜索器"""

    @property
    def source_name(self) -> str:
        return "xiachufang"

    @property
    def supported_types(self) -> List[str]:
        return ["recipe_search", "cooking_tips", "food_gallery", "ingredient_search"]

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
        """搜索下厨房菜谱"""
        print(f"[下厨房] 正在搜索: {query}")
        results = []
        browser = None
        try:
            browser = ensure_browser(port=port, stealth=stealth)
            if search_type in ["recipe", "all"]:
                results.extend(self._search_recipes(browser, query, max_results, wait_timeout))
            if search_type in ["ingredient", "all"]:
                results.extend(self._search_ingredients(browser, query, max_results, wait_timeout))
            seen_urls = set()
            unique_results = []
            for r in results:
                if r.get("url") and r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    unique_results.append(r)
            results = unique_results[:max_results]
            output_path = output_dir or str(XCF_OUTPUT_DIR)
            save_results(results, output_path, source=self.source_name)
            print(f"[下厨房] 搜索完成，共获取 {len(results)} 条结果")
            return results
        except Exception as e:
            print(f"[下厨房] 搜索失败: {e}")
            return []
        finally:
            if browser:
                browser.close()

    def _search_recipes(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{XCF_SEARCH_URL}?q={quote(query)}"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            recipe_items = browser.query_selector_all(".recipe-item, .grid-item, [class*='recipe']")
            for item in recipe_items[:max_results]:
                try:
                    title_elem = item.query_selector(".name, .title, h3, h4, a")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{XCF_BASE}{link}",
                                "source": self.source_name,
                                "type": "recipe",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[下厨房菜谱搜索] 错误: {e}")
        return results

    def _search_ingredients(self, browser, query: str, max_results: int, wait_timeout: int) -> List[Dict]:
        results = []
        try:
            url = f"{XCF_SEARCH_URL}?q={quote(query)}&category=ingredient"
            browser.get(url, timeout=wait_timeout)
            time.sleep(random.uniform(2, 4))
            ingredient_items = browser.query_selector_all(".ingredient-item, .food-item")
            for item in ingredient_items[:max_results]:
                try:
                    title_elem = item.query_selector(".name, .title, h4")
                    link_elem = item.query_selector("a[href]")
                    if title_elem and link_elem:
                        title = title_elem.text.strip()
                        link = link_elem.get_attribute("href")
                        if title and link:
                            results.append({
                                "title": title,
                                "url": link if link.startswith("http") else f"{XCF_BASE}{link}",
                                "source": self.source_name,
                                "type": "ingredient",
                                "snippet": title[:100],
                                "timestamp": int(time.time()),
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"[下厨房食材搜索] 错误: {e}")
        return results

    def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取详情页内容"""
        return {"url": url, "title": "", "content": "", "source": self.source_name}

    def health_check(self, port: int = 9333) -> Dict:
        return {
            "source": self.source_name,
            "status": "healthy",
            "supported_types": self.supported_types,
            "base_url": XCF_BASE,
        }

    def close(self):
        pass


def main():
    parser = argparse.ArgumentParser(description="下厨房搜索器")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", default="all", choices=["recipe", "ingredient", "all"])
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--no-stealth", action="store_true")
    args = parser.parse_args()
    searcher = XiachufangSearcher()
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
