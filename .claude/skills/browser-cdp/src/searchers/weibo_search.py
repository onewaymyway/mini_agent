#!/usr/bin/env python
"""
weibo_search.py - 微博搜索自动化脚本

使用 browser-cdp skill 搜索微博热搜榜和关键词，获取热搜榜单和搜索结果。

用法:
    python weibo_search.py --type hot              # 获取微博热搜榜
    python weibo_search.py --type keyword --query "AI Agent"  # 关键词搜索
    python weibo_search.py --type hot --max-results 20 --output-dir ./weibo_results
    python weibo_search.py --type keyword --query "Python" --port 9333

示例:
    python weibo_search.py --type hot
    python weibo_search.py --type keyword --query "自主进化Agent" --max-results 10
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
from urllib.parse import quote

# 导入基础模块
sys.path.insert(0, str(Path(__file__).parent))
from src.searchers.base import SearcherConfig, SearchResult, SearchResults, BaseSearcher
from src.searchers.utils import random_delay, save_results, dedup_results
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== 微博专用配置 ==========
WEIBO_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "weibo"
WEIBO_BASE = "https://weibo.com"
WEIBO_HOT_URL = "https://weibo.com/hot/search"
WEIBO_SEARCH_URL = "https://s.weibo.com/weibo?q={query}"


class WeiboSearcher(BaseSearcher):
    """微博搜索器"""

    @property
    def source_name(self) -> str:
        return "weibo"

    @property
    def supported_types(self) -> List[str]:
        return ["hot_search", "keyword_search", "detail"]

    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._port = config.port if config else 9333
        self._tab_id = config.tab_id if config else None

    def _ensure_browser(self, port: Optional[int] = None, tab_id: Optional[str] = None,
                        stealth: bool = True) -> tuple:
        """确保浏览器连接，返回 (port, tab_id)"""
        p = port or self._port
        tid = tab_id or self._tab_id
        if tid is None:
            result = ensure_browser(port=p, stealth=stealth)
            if result.get("error"):
                raise RuntimeError(f"浏览器启动失败: {result['error']}")
            tid = result.get("tab_id")
            p = result.get("port", p)
        return p, tid

    def get_hot_search(self, max_results: int = 50, port: Optional[int] = None,
                       tab_id: Optional[str] = None, stealth: bool = True,
                       wait_timeout: int = 30) -> List[Dict]:
        """获取微博热搜榜

        Args:
            max_results: 最大结果数 (默认 50)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            wait_timeout: 等待超时时间

        Returns:
            热搜榜单列表
        """
        print(f"[微博热搜] 正在获取热搜榜...")

        port, tab_id = self._ensure_browser(port=port, tab_id=tab_id, stealth=stealth)
        print(f"  [浏览器] 端口: {port}, Tab: {tab_id}")

        # 随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        # 导航到热搜页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", WEIBO_HOT_URL,
            "--wait-selector", ".searchHotList, .hot-list, [class*=hot]",
            "--timeout", str(wait_timeout)
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 使用 JS 提取热搜数据
        js_code = r"""
(() => {
  const results = [];
  // 尝试多种选择器匹配热搜列表
  const selectors = [
    '.searchHotList .item',
    '.hot-list .item',
    '[class*="hot"] .item',
    '.pl2 a',
    '.txt a'
  ];

  let items = [];
  for (const sel of selectors) {
    items = Array.from(document.querySelectorAll(sel));
    if (items.length > 0) break;
  }

  // 如果没找到结构化列表，尝试提取所有带排名的链接
  if (items.length === 0) {
    const allLinks = Array.from(document.querySelectorAll('a'));
    allLinks.forEach(a => {
      const text = a.textContent.trim();
      if (text && text.length > 2 && text.length < 100 && !a.href.includes('weibo.com/u/')) {
        results.push({
          rank: results.length + 1,
          title: text,
          url: a.href,
          hot_value: '',
          source: 'weibo_hot'
        });
      }
    });
  } else {
    items.forEach((item, index) => {
      const rankEl = item.querySelector('.num, .rank, [class*="rank"]');
      const titleEl = item.querySelector('.txt a, .title a, a');
      const hotEl = item.querySelector('.hot, .num, [class*="hot"]');

      const rank = rankEl ? rankEl.textContent.trim() : String(index + 1);
      const title = titleEl ? titleEl.textContent.trim() : '';
      const url = titleEl ? (titleEl.href || '') : '';
      const hotValue = hotEl ? hotEl.textContent.trim() : '';

      if (title && url) {
        results.push({
          rank: rank,
          title: title,
          url: url,
          hot_value: hotValue,
          source: 'weibo_hot'
        });
      }
    });
  }

  return results.slice(0, %d);
})()
""" % max_results

        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 内容提取失败: {extract_result.stderr[:200]}")
            return []

        try:
            raw_results = json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return []

        # 去重和限制数量
        results = dedup_results(raw_results, by="url")[:max_results]

        # 添加元数据
        for r in results:
            r['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            if 'rank' not in r:
                r['rank'] = len(results)

        print(f"  [结果] 共提取 {len(results)} 条热搜")
        return results

    def search_keyword(self, query: str, max_results: int = 20, port: Optional[int] = None,
                       tab_id: Optional[str] = None, stealth: bool = True,
                       wait_timeout: int = 30) -> List[Dict]:
        """微博关键词搜索

        Args:
            query: 搜索关键词
            max_results: 最大结果数 (默认 20)
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            wait_timeout: 等待超时时间

        Returns:
            搜索结果列表
        """
        print(f"[微博搜索] 正在搜索: {query}")

        port, tab_id = self._ensure_browser(port=port, tab_id=tab_id, stealth=stealth)
        print(f"  [浏览器] 端口: {port}, Tab: {tab_id}")

        # 随机延迟
        delay = random_delay(1.0, 2.0)
        print(f"  [延迟] 请求前等待 {delay:.1f} 秒")

        # 构建搜索 URL
        search_url = WEIBO_SEARCH_URL.format(query=quote(query))
        print(f"  [URL] {search_url}")

        # 导航到搜索结果页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", search_url,
            "--wait-selector", ".search-main, .feed-wrap, [class*=feed]",
            "--timeout", str(wait_timeout)
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []

        time.sleep(2.0)

        # 检查是否需要登录
        js_check = r"""
(() => {
  const loginBtn = document.querySelector('.login-btn, .S_txt1[href*="login"], a[href*="login"]');
  return loginBtn ? 'need_login' : 'ok';
})()
"""
        check_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_check
        ])

        if check_result.returncode == 0 and 'need_login' in check_result.stdout:
            print("[提示] 微博需要登录态，请手动登录后重试")
            print("[提示] 使用 --dedicated --name weibo_session 保留登录态")
            return []

        # 使用 JS 提取搜索结果
        js_code = r"""
(() => {
  const results = [];
  // 尝试多种选择器匹配微博搜索结果
  const selectors = [
    '.card-wrap',
    '.weibo-card',
    '[class*="card"]',
    '.feed-card'
  ];

  let cards = [];
  for (const sel of selectors) {
    cards = Array.from(document.querySelectorAll(sel));
    if (cards.length > 0) break;
  }

  cards.forEach((card, index) => {
    if (index >= %d) return;

    // 标题/内容
    const titleEl = card.querySelector('.content_text, .txt, .node_content, .W_texta');
    const title = titleEl ? titleEl.textContent.trim().substring(0, 200) : '';

    // 链接
    const linkEl = card.querySelector('a[href*="weibo.com"]');
    let url = linkEl ? linkEl.href : '';
    if (url && !url.startsWith('http')) {
      url = 'https:' + url;
    }

    // 作者
    const authorEl = card.querySelector('.S_txt2, .name, a[href*="u"]');
    const author = authorEl ? authorEl.textContent.trim() : '';

    // 发布时间
    const timeEl = card.querySelector('.ct, .time, [class*="time"]');
    const pub_time = timeEl ? timeEl.textContent.trim() : '';

    // 互动数据
    const repostEl = card.querySelector('.btn_repost span:first-child');
    const commentEl = card.querySelector('.btn_comment span:first-child');
    const likeEl = card.querySelector('.btn_like span:first-child');

    if (title || author) {
      results.push({
        title: title,
        url: url,
        author: author,
        published_time: pub_time,
        reposts: repostEl ? repostEl.textContent.trim() : '',
        comments: commentEl ? commentEl.textContent.trim() : '',
        likes: likeEl ? likeEl.textContent.trim() : '',
        source: 'weibo_search',
        query: '%s'
      });
    }
  });

  return results;
})()
""" % (max_results, query)

        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 内容提取失败: {extract_result.stderr[:200]}")
            return []

        try:
            raw_results = json.loads(extract_result.stdout)
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return []

        # 去重和限制数量
        results = dedup_results(raw_results, by="url")[:max_results]

        # 添加元数据
        for r in results:
            r['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')

        print(f"  [结果] 共提取 {len(results)} 条结果")
        return results

    def search(self, query: str = "", search_type: str = "hot",
               max_results: int = 20, port: Optional[int] = None,
               tab_id: Optional[str] = None, stealth: bool = True,
               output_dir: Optional[str] = None,
               wait_timeout: int = 30) -> List[Dict]:
        """统一搜索入口

        Args:
            query: 搜索关键词 (热搜模式可留空)
            search_type: 搜索类型 (hot/keyword)
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间

        Returns:
            搜索结果列表
        """
        if search_type == "hot":
            results = self.get_hot_search(
                max_results=max_results,
                port=port,
                tab_id=tab_id,
                stealth=stealth,
                wait_timeout=wait_timeout
            )
        elif search_type == "keyword":
            if not query:
                print("[错误] 关键词搜索需要提供 query 参数")
                return []
            results = self.search_keyword(
                query=query,
                max_results=max_results,
                port=port,
                tab_id=tab_id,
                stealth=stealth,
                wait_timeout=wait_timeout
            )
        else:
            print(f"[错误] 不支持的搜索类型: {search_type}")
            print(f"[提示] 支持的类型: hot, keyword")
            return []

        # 保存结果
        if output_dir and results:
            os.makedirs(output_dir, exist_ok=True)
            filename = f"weibo_{search_type}_{query.replace(' ', '_') if query else 'hot'}.json"
            path = os.path.join(output_dir, filename)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"  [保存] {path}")

        return results

    def get_detail(self, url: str, port: Optional[int] = None,
                   tab_id: Optional[str] = None, stealth: bool = True) -> Dict:
        """获取微博详情页内容"""
        print(f"[微博详情] 正在获取: {url}")

        port, tab_id = self._ensure_browser(port=port, tab_id=tab_id, stealth=stealth)

        # 导航到详情页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".weibo-detail, .detail, [class*=detail]",
            "--timeout", "30"
        ])

        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return {}

        time.sleep(1.5)

        # 提取详情信息
        js_code = r"""
(() => {
  const result = {};

  // 正文内容
  const contentEl = document.querySelector('.weibo-detail .txt, .detail .txt, .W_texta');
  result.content = contentEl ? contentEl.textContent.trim() : '';

  // 发布时间
  const timeEl = document.querySelector('.ct a, .time, [class*="time"]');
  result.published_time = timeEl ? timeEl.textContent.trim() : '';

  // 作者
  const authorEl = document.querySelector('.name, .S_txt2, a[href*="u"]');
  result.author = authorEl ? authorEl.textContent.trim() : '';

  // 互动数据
  const repostEl = document.querySelector('.btn_repost span:first-child');
  const commentEl = document.querySelector('.btn_comment span:first-child');
  const likeEl = document.querySelector('.btn_like span:first-child');
  result.reposts = repostEl ? repostEl.textContent.trim() : '';
  result.comments = commentEl ? commentEl.textContent.trim() : '';
  result.likes = likeEl ? likeEl.textContent.trim() : '';

  // 标签
  const tags = [];
  document.querySelectorAll('.tag a, .hashtag a').forEach(tag => {
    tags.push(tag.textContent.trim());
  });
  result.tags = tags;

  return result;
})()
"""

        extract_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--eval", js_code
        ])

        if extract_result.returncode != 0:
            print(f"[错误] 详情提取失败: {extract_result.stderr[:200]}")
            return {}

        try:
            detail = json.loads(extract_result.stdout)
            detail['source'] = 'weibo'
            detail['url'] = url
            detail['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return detail
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="微博搜索自动化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python weibo_search.py --type hot
    python weibo_search.py --type keyword --query "AI Agent" --max-results 10
    python weibo_search.py --type hot --output-dir ./weibo_results
"""
    )

    parser.add_argument("--type", type=str, default="hot",
                       choices=["hot", "keyword"],
                       help="搜索类型 (默认: hot)")
    parser.add_argument("--query", type=str, default="",
                       help="搜索关键词 (keyword 模式必需)")
    parser.add_argument("--max-results", type=int, default=20,
                       help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", type=str, default=None,
                       help="输出目录")
    parser.add_argument("--port", type=int, default=9333,
                       help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None,
                       help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True,
                       help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth",
                       help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30,
                       help="等待超时时间 (默认: 30秒)")

    args = parser.parse_args()

    # 创建搜索器
    searcher = WeiboSearcher()

    # 执行搜索
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout
    )

    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条结果")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到结果")


if __name__ == "__main__":
    main()
