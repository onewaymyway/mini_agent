#!/usr/bin/env python
"""
thp_news.py - 澎湃新闻新闻抓取脚本

使用 browser-cdp skill 抓取澎湃新闻新闻列表和详情。
支持关键词搜索和分类浏览（时政/财经/天下/观察）。

用法:
    python thp_news.py --category shizheng --max-results 20
    python thp_news.py --query "人工智能" --max-results 10
    python thp_news.py --category caijing --output-dir ./thp_results
    python thp_news.py --detail "https://www.thepaper.cn/newsDetail_forward_xxxxxx"

示例:
    python thp_news.py --category shizheng --max-results 20
    python thp_news.py --query "经济" --category caijing --max-results 10
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional

# 导入基础模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import random_delay, save_results, clean_text
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 澎湃新闻专用配置 ==========
THP_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "thp"

# 分类 URL 映射
THP_CATEGORIES = {
    "shizheng": {
        "name": "时政",
        "url": "https://www.thepaper.cn/channel_25955",
        "selector": ".list-item, .news-item, .news-list li, .channel-news li, article",
    },
    "caijing": {
        "name": "财经",
        "url": "https://www.thepaper.cn/channel_25956",
        "selector": ".list-item, .news-item, .news-list li, .channel-news li, article",
    },
    "tianxia": {
        "name": "天下",
        "url": "https://www.thepaper.cn/channel_25957",
        "selector": ".list-item, .news-item, .news-list li, .channel-news li, article",
    },
    "guancha": {
        "name": "观察",
        "url": "https://www.thepaper.cn/channel_25958",
        "selector": ".list-item, .news-item, .news-list li, .channel-news li, article",
    },
}

# 搜索 URL 模板
THP_SEARCH_URL = "https://www.thepaper.cn/searchResult.jsp?keyword={keyword}"


class THPNewsSearcher(BaseSearcher):
    """澎湃新闻新闻搜索器"""

    @property
    def source_name(self) -> str:
        return "thepaper"

    @property
    def supported_types(self) -> List[str]:
        return ["news_list", "news_detail", "search"]

    def search(
        self,
        query: str = "",
        category: str = "",
        max_results: int = 20,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索澎湃新闻新闻

        Args:
            query: 搜索关键词（可选，为空则按分类浏览）
            category: 新闻分类 (shizheng/caijing/tianxia/guancha)
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            新闻列表
        """
        print(f"[澎湃新闻] 开始搜索")
        print(f"  关键词: {query or '(无，按分类浏览)'}")
        print(f"  分类: {category or '(全部)'}")
        print(f"  最大结果: {max_results}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")

        # 随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        if query:
            # 关键词搜索模式
            return self._search_by_keyword(
                query=query,
                max_results=max_results,
                port=port,
                tab_id=tab_id,
                stealth=stealth,
                wait_timeout=wait_timeout,
            )
        elif category:
            # 分类浏览模式
            return self._browse_category(
                category=category,
                max_results=max_results,
                port=port,
                tab_id=tab_id,
                stealth=stealth,
                wait_timeout=wait_timeout,
            )
        else:
            # 默认浏览首页推荐
            return self._browse_category(
                category="shizheng",
                max_results=max_results,
                port=port,
                tab_id=tab_id,
                stealth=stealth,
                wait_timeout=wait_timeout,
            )

    def _search_by_keyword(
        self,
        query: str,
        max_results: int,
        port: int,
        tab_id: str,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """通过关键词搜索"""
        import urllib.parse
        encoded_query = urllib.parse.quote(query)
        url = THP_SEARCH_URL.format(keyword=encoded_query)
        print(f"  [URL] {url}")

        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".list-item, .news-item, article, .search-result",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 使用 JS 提取搜索结果
        js_code = _build_thp_list_js(query)
        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code,
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 内容提取失败: {extract_result.stderr[:200]}")
            return []

        try:
            raw_results = json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return []

        # 限制数量
        results = raw_results[:max_results]

        # 添加元数据
        for r in results:
            r["category"] = "search"
            r["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        print(f"  [结果] 共提取 {len(results)} 条新闻")
        return results

    def _browse_category(
        self,
        category: str,
        max_results: int,
        port: int,
        tab_id: str,
        stealth: bool,
        wait_timeout: int,
    ) -> List[Dict]:
        """浏览指定分类"""
        if category not in THP_CATEGORIES:
            print(f"[错误] 不支持的分类: {category}")
            print(f"[提示] 支持的分类: {list(THP_CATEGORIES.keys())}")
            return []

        cat_info = THP_CATEGORIES[category]
        url = cat_info["url"]
        selector = cat_info["selector"]
        print(f"  [URL] {url} ({cat_info['name']})")

        # 导航到分类页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", selector,
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 使用 JS 提取新闻列表
        js_code = _build_thp_list_js(query="", category=category)
        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code,
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 内容提取失败: {extract_result.stderr[:200]}")
            return []

        try:
            raw_results = json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return []

        # 限制数量
        results = raw_results[:max_results]

        # 添加元数据
        for r in results:
            r["category"] = category
            r["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        print(f"  [结果] 共提取 {len(results)} 条新闻")
        return results

    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        wait_timeout: int = 30,
    ) -> Dict:
        """获取新闻详情"""
        print(f"[澎湃新闻详情] 正在获取: {url}")

        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return {}
            tab_id = result.get("tab_id")
            port = result.get("port", port)

        # 随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        # 导航到详情页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", "#content, .news-content, article, .main-content",
            "--timeout", str(wait_timeout),
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return {}

        time.sleep(1.5)

        # 提取详情信息
        js_code = r"""
(() => {
  const result = {};

  // 标题
  const titleEl = document.querySelector('h1, .article-title, .title, #title');
  result.title = titleEl ? titleEl.innerText.trim() : '';

  // 正文
  const contentEl = document.querySelector('#content, .news-content, article, .main-content, .text');
  result.content = contentEl ? contentEl.innerText.trim() : '';

  // 作者
  const authorEl = document.querySelector('.author, .source, [class*="author"], .news-source');
  result.author = authorEl ? authorEl.innerText.trim() : '';

  // 时间
  const timeEl = document.querySelector('.time, .date, [class*="time"], .news-time');
  result.time = timeEl ? timeEl.innerText.trim() : '';

  // 标签
  const tags = [];
  document.querySelectorAll('.tag, [class*="tag"] a, .keywords a').forEach(tag => {
    const t = tag.innerText.trim();
    if (t) tags.push(t);
  });
  result.tags = tags;

  // 来源
  const sourceEl = document.querySelector('.source, .news-source, [class*="source"]');
  result.source = sourceEl ? sourceEl.innerText.trim() : '澎湃新闻';

  return result;
})()
"""

        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code,
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 详情提取失败: {extract_result.stderr[:200]}")
            return {}

        try:
            detail = json.loads(extract_result.stdout)
            detail["source"] = "thepaper"
            detail["url"] = url
            detail["scraped_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            return detail
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}

    def search_and_get_details(
        self,
        query: str = "",
        category: str = "",
        max_results: int = 10,
        fetch_detail: bool = True,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索并获取详情（批量模式）"""
        print(f"[澎湃新闻] 搜索并获取详情")
        print(f"  关键词: {query or '(无)'}")
        print(f"  分类: {category or '(全部)'}")
        print(f"  最大结果: {max_results}")
        print(f"  获取详情: {fetch_detail}")

        # 先获取列表
        results = self.search(
            query=query,
            category=category,
            max_results=max_results,
            port=port,
            tab_id=tab_id,
            stealth=stealth,
            wait_timeout=wait_timeout,
        )

        if not results:
            print("[警告] 未找到新闻")
            return []

        # 获取详情
        if fetch_detail:
            print(f"\n[详情] 正在获取 {len(results)} 条新闻的详细内容...")
            for i, result in enumerate(results):
                print(f"  [{i + 1}/{len(results)}] {result.get('title', '')[:50]}...")
                detail = self.get_detail(
                    url=result.get("url", ""),
                    port=port,
                    tab_id=tab_id,
                    stealth=stealth,
                    wait_timeout=wait_timeout,
                )
                result.update(detail)
                time.sleep(0.5)  # 避免请求过快

        # 保存结果
        if output_dir:
            path = save_results(results, Path(output_dir), f"thp_{category or query}.json")
            print(f"[保存] {path}")

        return results


def _build_thp_list_js(query: str = "", category: str = "") -> str:
    """构建提取新闻列表的 JS 代码"""
    return r"""
(() => {
  const results = [];

  // 尝试多种选择器
  const selectors = [
    '.list-item a',
    '.news-item a',
    '.news-list a',
    '.channel-news a',
    'article a',
    '.main-list a',
    '.news-content a',
    'a[href*="thepaper.cn/newsDetail"]'
  ];

  let links = [];
  for (const selector of selectors) {
    links = document.querySelectorAll(selector);
    if (links.length > 0) break;
  }

  links.forEach((link, i) => {
    if (i >= 50) return;

    const title = link.innerText.trim();
    let url = link.href || '';

    // 过滤无效链接
    if (!url || !url.startsWith('http')) return;
    if (url.includes('javascript:') || url.includes('#')) return;

    // 获取父容器中的时间信息
    const parent = link.closest('li, .list-item, .news-item, article, .item');
    let time = '';
    if (parent) {
      const timeEl = parent.querySelector('.time, .date, [class*="time"], [class*="date"]');
      if (timeEl) time = timeEl.innerText.trim();
    }

    // 获取摘要
    let snippet = '';
    if (parent) {
      const snippetEl = parent.querySelector('.snippet, .abstract, .summary, [class*="snippet"]');
      if (snippetEl) snippet = snippetEl.innerText.trim();
    }

    results.push({
      title: title,
      url: url,
      time: time,
      snippet: snippet,
      source: 'thepaper'
    });
  });

  return results;
})()
"""


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="澎湃新闻新闻抓取脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 浏览时政分类
    python thp_news.py --category shizheng --max-results 20

    # 搜索关键词
    python thp_news.py --query "人工智能" --max-results 10

    # 财经分类 + 获取详情
    python thp_news.py --category caijing --max-results 10 --fetch-detail

    # 保存到指定目录
    python thp_news.py --query "经济" --output-dir ./thp_results
""",
    )

    # 搜索模式
    parser.add_argument("--query", type=str, default="", help="搜索关键词（可选）")
    parser.add_argument(
        "--category",
        type=str,
        default="",
        choices=["shizheng", "caijing", "tianxia", "guancha"],
        help="新闻分类 (shizheng/caijing/tianxia/guancha)",
    )

    # 输出配置
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument(
        "--fetch-detail",
        action="store_true",
        default=False,
        help="获取新闻详情内容",
    )

    # 浏览器配置
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")

    # 详情模式
    parser.add_argument("--detail", type=str, default=None, help="直接获取指定 URL 的详情")

    args = parser.parse_args()

    # 创建搜索器
    searcher = THPNewsSearcher()

    # 详情模式
    if args.detail:
        print(f"[澎湃新闻] 详情模式: {args.detail}")
        detail = searcher.get_detail(
            url=args.detail,
            port=args.port,
            tab_id=args.tab,
            stealth=args.stealth,
            wait_timeout=args.wait_timeout,
        )
        if detail:
            print(json.dumps(detail, ensure_ascii=False, indent=2))
            if args.output_dir:
                path = save_results([detail], Path(args.output_dir), "thp_detail.json")
                print(f"[保存] {path}")
        return

    # 搜索/浏览模式
    results = searcher.search_and_get_details(
        query=args.query,
        category=args.category,
        max_results=args.max_results,
        fetch_detail=args.fetch_detail,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout,
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条新闻")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到新闻")


if __name__ == "__main__":
    main()
