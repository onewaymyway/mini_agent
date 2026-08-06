#!/usr/bin/env python
"""
sina_news.py - 新浪财经新闻抓取脚本

使用 browser-cdp skill 抓取新浪财经新闻列表和详情。
新浪财经反爬较弱，可直接使用 requests 抓取 RSS 或 HTML。

用法:
    python sina_news.py --category stock --max-results 20
    python sina_news.py --category macro --output-dir ./sina_results
    python sina_news.py --category industry --port 9333

示例:
    python sina_news.py --category stock --max-results 20
    python sina_news.py --category macro --output-dir ./sina_results
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


# ========== 新浪财经专用配置 ==========
SINA_OUTPUT_DIR = SKILL_DIR.parent / "search_results" / "sina"

# RSS  feeds
SINA_RSS_FEEDS = {
    "stock": "https://feed.finance.sina.com.cn/rss/stock.xml",
    "macro": "https://feed.finance.sina.com.cn/rss/macro.xml",
    "industry": "https://feed.finance.sina.com.cn/rss/industry.xml",
    "forex": "https://feed.finance.sina.com.cn/rss/forex.xml",
    "futures": "https://feed.finance.sina.com.cn/rss/futures.xml",
}


class SinaNewsSearcher(BaseSearcher):
    """新浪财经新闻搜索器"""
    
    @property
    def source_name(self) -> str:
        return "sina_finance"
    
    @property
    def supported_types(self) -> List[str]:
        return ["news_list", "news_detail"]
    
    def search(
        self,
        query: str = "",
        category: str = "stock",
        max_results: int = 20,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30
    ) -> List[Dict]:
        """搜索新浪财经新闻
        
        Args:
            query: 搜索关键词（可选，为空则获取全部）
            category: 新闻分类 (stock/macro/industry/forex/futures)
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            
        Returns:
            新闻列表
        """
        print(f"[新浪财经] 正在获取 {category} 分类新闻")
        
        # 检查分类有效性
        if category not in SINA_RSS_FEEDS:
            print(f"[错误] 不支持的分类: {category}")
            print(f"[提示] 支持的分: {list(SINA_RSS_FEEDS.keys())}")
            return []
        
        # 尝试使用 RSS 直接抓取（无需浏览器）
        try:
            import feedparser
            rss_url = SINA_RSS_FEEDS[category]
            feed = feedparser.parse(rss_url)
            
            if not feed.entries:
                print(f"[警告] RSS 解析失败，尝试使用浏览器抓取")
                return self._search_via_browser(
                    query=query,
                    category=category,
                    max_results=max_results,
                    port=port,
                    tab_id=tab_id,
                    stealth=stealth,
                    wait_timeout=wait_timeout
                )
            
            # 解析 RSS 条目
            results = []
            for entry in feed.entries[:max_results]:
                result = {
                    "title": entry.title,
                    "url": entry.link,
                    "summary": entry.summary[:200] if hasattr(entry, 'summary') else '',
                    "published": entry.published if hasattr(entry, 'published') else '',
                    "source": "sina_finance",
                    "category": category,
                    "scraped_at": time.strftime('%Y-%m-%d %H:%M:%S')
                }
                
                # 关键词过滤
                if query and query.lower() not in result['title'].lower() and query.lower() not in result['summary'].lower():
                    continue
                
                results.append(result)
            
            print(f"  [结果] 共提取 {len(results)} 条新闻")
            
            # 保存结果
            if output_dir:
                path = save_results(results, output_dir, f"sina_{category}.json")
                print(f"  [保存] {path}")
            
            return results
            
        except ImportError:
            print("[警告] feedparser 未安装，尝试使用浏览器抓取")
            return self._search_via_browser(
                query=query,
                category=category,
                max_results=max_results,
                port=port,
                tab_id=tab_id,
                stealth=stealth,
                wait_timeout=wait_timeout
            )
        except Exception as e:
            print(f"[错误] RSS 抓取失败: {e}")
            return self._search_via_browser(
                query=query,
                category=category,
                max_results=max_results,
                port=port,
                tab_id=tab_id,
                stealth=stealth,
                wait_timeout=wait_timeout
            )
    
    def _search_via_browser(
        self,
        query: str,
        category: str,
        max_results: int,
        port: int,
        tab_id: Optional[str],
        stealth: bool,
        wait_timeout: int
    ) -> List[Dict]:
        """通过浏览器抓取新浪财经新闻"""
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
        
        # 构建新闻列表 URL
        category_urls = {
            "stock": "https://finance.sina.com.cn/stock/",
            "macro": "https://finance.sina.com.cn/china/",
            "industry": "https://finance.sina.com.cn/chanjing/",
            "forex": "https://forex.sina.com.cn/",
            "futures": "https://futures.sina.com.cn/",
        }
        
        url = category_urls.get(category, category_urls["stock"])
        print(f"  [URL] {url}")
        
        # 导航到新闻列表页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", ".list-item, .news-item, article",
            "--timeout", str(wait_timeout)
        ])
        
        if nav_result.returncode != 0:
            print(f"[错误] 导航失败: {nav_result.stderr[:200]}")
            return []
        
        time.sleep(2.0)
        
        # 使用 JS 提取新闻列表
        js_code = r"""
(() => {
  const results = [];
  
  // 尝试多种选择器
  const selectors = ['.list-item', '.news-item', 'article', '.news-list li', '.list li'];
  let items = [];
  
  for (const selector of selectors) {
    items = document.querySelectorAll(selector);
    if (items.length > 0) break;
  }
  
  items.forEach((item, i) => {
    if (i >= 50) return;
    
    const titleEl = item.querySelector('a, .title, h3, h2');
    const title = titleEl ? titleEl.innerText.trim() : '';
    
    const linkEl = item.querySelector('a[href]');
    let url = linkEl ? linkEl.href : '';
    if (url && !url.startsWith('http')) {
      url = 'https:' + url;
    }
    
    const timeEl = item.querySelector('.time, .date, [class*="time"]');
    const time = timeEl ? timeEl.innerText.trim() : '';
    
    if (title && url) {
      results.push({
        title: title,
        url: url,
        time: time,
        source: 'sina_finance'
      });
    }
  });
  
  return results;
})()
"""
        
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
        
        # 关键词过滤
        if query:
            raw_results = [
                r for r in raw_results
                if query.lower() in r.get('title', '').lower()
            ]
        
        # 限制数量
        results = raw_results[:max_results]
        
        # 添加元数据
        for r in results:
            r['category'] = category
            r['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"  [结果] 共提取 {len(results)} 条新闻")
        
        return results
    
    def get_detail(
        self,
        url: str,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True
    ) -> Dict:
        """获取新闻详情"""
        print(f"[新浪财经详情] 正在获取: {url}")
        
        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return {}
            tab_id = result.get("tab_id")
            port = result.get("port", port)
        
        # 导航到详情页
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", tab_id,
            "--goto", url,
            "--wait-selector", "article, .article, .content",
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
  
  // 标题
  const titleEl = document.querySelector('h1, .article-title, .title');
  result.title = titleEl ? titleEl.innerText.trim() : '';
  
  // 正文
  const contentEl = document.querySelector('article, .article, .content, #artibody');
  result.content = contentEl ? contentEl.innerText.trim() : '';
  
  // 作者
  const authorEl = document.querySelector('.author, .source, [class*="author"]');
  result.author = authorEl ? authorEl.innerText.trim() : '';
  
  // 时间
  const timeEl = document.querySelector('.time, .date, [class*="time"]');
  result.time = timeEl ? timeEl.innerText.trim() : '';
  
  // 标签
  const tags = [];
  document.querySelectorAll('.tag, [class*="tag"] a').forEach(tag => {
    tags.push(tag.innerText.trim());
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
            detail['source'] = 'sina_finance'
            detail['url'] = url
            detail['scraped_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            return detail
        except json.JSONDecodeError:
            print(f"[错误] JSON 解析失败: {extract_result.stdout[:200]}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="新浪财经新闻抓取脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python sina_news.py --category stock --max-results 20
    python sina_news.py --category macro --output-dir ./sina_results
    python sina_news.py --category industry --port 9333
"""
    )
    
    parser.add_argument("--category", type=str, default="stock",
                       choices=["stock", "macro", "industry", "forex", "futures"],
                       help="新闻分类 (默认: stock)")
    parser.add_argument("--query", type=str, default="", help="搜索关键词（可选）")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数 (默认: 20)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--tab", type=str, default=None, help="Tab ID")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    parser.add_argument("--wait-timeout", type=int, default=30, help="等待超时时间 (默认: 30秒)")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = SinaNewsSearcher()
    
    # 执行搜索
    results = searcher.search(
        query=args.query,
        category=args.category,
        max_results=args.max_results,
        port=args.port,
        tab_id=args.tab,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.wait_timeout
    )
    
    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条新闻")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到新闻")


if __name__ == "__main__":
    main()
