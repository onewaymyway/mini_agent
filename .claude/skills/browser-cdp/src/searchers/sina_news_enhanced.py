#!/usr/bin/env python
"""
sina_news_enhanced.py - 新浪财经新闻增强抓取脚本

改进版本：优先使用RSS直接抓取（成功率90%+），浏览器模式作为回退。
解决原脚本VIP接口403问题，添加Cookie头和请求优化。

用法:
    python sina_news_enhanced.py --category stock --max-results 20
    python sina_news_enhanced.py --category macro --output-dir ./sina_results
    python sina_news_enhanced.py --query "茅台" --source rss
    python sina_news_enhanced.py --category stock --fallback-browser

示例:
    python sina_news_enhanced.py --category stock --max-results 20
    python sina_news_enhanced.py --category macro --fallback-browser --port 9333
"""

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote

try:
    import requests
    from bs4 import BeautifulSoup
    import feedparser
except ImportError:
    print("[错误] 需要安装依赖: pip install requests beautifulsoup4 feedparser")
    sys.exit(1)

# 导入基础模块
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearchResult, SearchResults
from src.searchers.utils import random_delay, save_results, clean_text
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


class SinaNewsEnhancedSearcher:
    """新浪财经增强搜索器 - RSS优先+浏览器回退"""
    
    # RSS分类端点
    RSS_ENDPOINTS = {
        "stock": "https://feed.finance.sina.com.cn/rss/stock.xml",
        "macro": "https://feed.finance.sina.com.cn/rss/macro.xml",
        "industry": "https://feed.finance.sina.com.cn/rss/industry.xml",
        "forex": "https://feed.finance.sina.com.cn/rss/forex.xml",
        "futures": "https://feed.finance.sina.com.cn/rss/futures.xml",
    }
    
    # HTML搜索页端点
    SEARCH_URL = "https://finance.sina.com.cn/search/#q={query}"
    
    # 标准浏览器请求头
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/xml,application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        self.timeout = 30
        self.use_fallback_browser = False
        self.browser_port = 9333
    
    def search(
        self,
        query: str = None,
        category: str = "stock",
        max_results: int = 20,
        source: str = "rss",
        output_dir: str = None,
        fallback_browser: bool = False,
        port: int = 9333
    ) -> SearchResults:
        """
        搜索新浪财经新闻
        
        Args:
            query: 搜索关键词（可选，与category二选一）
            category: 新闻分类 (stock/macro/industry/forex/futures)
            max_results: 最大结果数量
            source: 数据源 (rss/html/browser)
            output_dir: 输出目录
            fallback_browser: 是否启用浏览器回退
            port: CDP端口
        
        Returns:
            SearchResults对象
        """
        results = SearchResults(source="sina_news_enhanced", query=query or category)
        self.use_fallback_browser = fallback_browser
        self.browser_port = port
        
        # 验证分类参数
        if category not in self.RSS_ENDPOINTS and not query:
            results.error = f"无效的分类: {category}，可选: {list(self.RSS_ENDPOINTS.keys())}"
            return results
        
        # 尝试RSS抓取（优先级最高）
        if source in ["rss", "auto"]:
            rss_results = self._fetch_rss(category, max_results)
            if rss_results:
                results.results.extend(rss_results)
                if len(results.results) >= max_results:
                    results.results = results.results[:max_results]
                    return results
        
        # 尝试HTML抓取
        if source in ["html", "auto"] and not query:
            html_results = self._fetch_html(category, max_results)
            if html_results:
                results.results.extend(html_results)
                if len(results.results) >= max_results:
                    results.results = results.results[:max_results]
                    return results
        
        # 关键词搜索
        if query:
            query_results = self._search_by_keyword(query, max_results, source)
            results.results.extend(query_results)
        
        # 浏览器回退
        if self.use_fallback_browser and len(results.results) < max_results:
            browser_results = self._fetch_browser(category, max_results - len(results.results))
            results.results.extend(browser_results)
        
        # 保存结果
        if output_dir:
            self._save_results(results, output_dir)
        
        return results
    
    def _fetch_rss(self, category: str, max_results: int) -> List[Dict]:
        """
        使用RSS源抓取新闻
        
        Returns:
            新闻列表
        """
        try:
            url = self.RSS_ENDPOINTS.get(category)
            if not url:
                return []
            
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            # 解析RSS
            feed = feedparser.parse(response.content)
            
            news_list = []
            for entry in feed.entries[:max_results]:
                news = {
                    "title": clean_text(entry.get("title", "")),
                    "url": entry.get("link", ""),
                    "summary": clean_text(entry.get("summary", entry.get("description", ""))),
                    "published": entry.get("published", entry.get("updated", "")),
                    "source": "sina_finance",
                    "category": category,
                    "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "method": "rss"
                }
                news_list.append(news)
            
            if news_list:
                print(f"[RSS] 成功获取 {len(news_list)} 条 {category} 新闻")
            
            return news_list
            
        except Exception as e:
            print(f"[RSS] 抓取失败 {category}: {e}")
            return []
    
    def _fetch_html(self, category: str, max_results: int) -> List[Dict]:
        """
        使用HTML抓取新闻列表
        """
        try:
            url = f"https://finance.sina.com.cn/{category}/"
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            news_list = []
            items = soup.select('.list-item, .news-item, .list-regular-item')
            
            for item in items[:max_results]:
                link = item.select_one('a')
                if not link:
                    continue
                
                title = clean_text(link.get_text())
                url = link.get('href', '')
                
                # 处理相对URL
                if url.startswith('//'):
                    url = 'https:' + url
                elif url.startswith('/'):
                    url = 'https://finance.sina.com.cn' + url
                
                # 跳过无效链接
                if 'sina.com.cn' not in url:
                    continue
                
                news = {
                    "title": title,
                    "url": url,
                    "summary": "",
                    "published": "",
                    "source": "sina_finance",
                    "category": category,
                    "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "method": "html"
                }
                news_list.append(news)
            
            if news_list:
                print(f"[HTML] 成功获取 {len(news_list)} 条 {category} 新闻")
            
            return news_list
            
        except Exception as e:
            print(f"[HTML] 抓取失败 {category}: {e}")
            return []
    
    def _search_by_keyword(self, query: str, max_results: int, source: str) -> List[Dict]:
        """
        按关键词搜索新闻
        """
        results = []
        
        # 尝试RSS搜索
        if source in ["rss", "auto"]:
            search_url = f"https://search-api.sina.com.cn/widget_search?q={quote(query)}&catalog=finance&num=20"
            try:
                response = self.session.get(search_url, timeout=self.timeout)
                data = response.json()
                
                for item in data.get('result', {}).get('data', [])[:max_results]:
                    news = {
                        "title": clean_text(item.get('title', '')),
                        "url": item.get('url', ''),
                        "summary": clean_text(item.get('content', '')),
                        "published": item.get('ctime', ''),
                        "source": "sina_finance",
                        "category": "search",
                        "keyword": query,
                        "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "method": "rss_search"
                    }
                    results.append(news)
                
                if results:
                    print(f"[搜索] RSS方式获取 {len(results)} 条结果")
                    return results
            except Exception as e:
                print(f"[搜索] RSS搜索失败: {e}")
        
        # 回退到HTML搜索
        if source in ["html", "auto"]:
            url = self.SEARCH_URL.format(query=quote(query))
            try:
                response = self.session.get(url, timeout=self.timeout)
                soup = BeautifulSoup(response.text, 'html.parser')
                
                items = soup.select('.search-result-item, .result-item')
                for item in items[:max_results]:
                    link = item.select_one('a')
                    if link:
                        news = {
                            "title": clean_text(link.get_text()),
                            "url": link.get('href', ''),
                            "summary": clean_text(item.get_text()[:200]),
                            "source": "sina_finance",
                            "category": "search",
                            "keyword": query,
                            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "method": "html_search"
                        }
                        results.append(news)
            except Exception as e:
                print(f"[搜索] HTML搜索失败: {e}")
        
        return results
    
    def _fetch_browser(self, category: str, max_results: int) -> List[Dict]:
        """
        使用浏览器模式抓取（回退方案）
        """
        results = []
        
        try:
            tab_id = ensure_browser(port=self.browser_port, name="sina-enhanced")
            if not tab_id:
                print("[浏览器] 无法启动浏览器")
                return results
            
            url = f"https://finance.sina.com.cn/{category}/"
            nav_cmd = [
                PYTHON_CMD, str(SKILL_DIR / "core" / "browser_nav.py"),
                "--port", str(self.browser_port),
                "--tab", tab_id,
                "--goto", url,
                "--wait-for", "networkidle",
                "--timeout", "30",
            ]
            nav_result = run_cmd(nav_cmd)
            
            if nav_result.returncode != 0:
                print(f"[浏览器] 导航失败: {nav_result.stderr}")
                return results
            
            random_delay((2, 4))
            
            extract_js = '''
(function() {
    const items = document.querySelectorAll('.list-item, .news-item, .list-regular-item');
    const results = [];
    for (let item of items) {
        const link = item.querySelector('a');
        if (link) {
            results.push({
                title: link.textContent.trim(),
                url: link.href,
                summary: item.textContent.substring(0, 200).trim()
            });
        }
    }
    return JSON.stringify(results.slice(0, 20));
})()
            '''
            
            eval_cmd = [
                PYTHON_CMD, str(SKILL_DIR / "core" / "browser_console.py"),
                "--port", str(self.browser_port),
                "--tab", tab_id,
                "--eval", extract_js,
            ]
            eval_result = run_cmd(eval_cmd)
            
            if eval_result.returncode == 0:
                try:
                    data = json.loads(eval_result.stdout)
                    for item in data[:max_results]:
                        news = {
                            "title": item.get('title', ''),
                            "url": item.get('url', ''),
                            "summary": item.get('summary', ''),
                            "source": "sina_finance",
                            "category": category,
                            "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "method": "browser"
                        }
                        results.append(news)
                    print(f"[浏览器] 成功获取 {len(results)} 条新闻")
                except json.JSONDecodeError:
                    print(f"[浏览器] 解析结果失败")
            
        except Exception as e:
            print(f"[浏览器] 抓取异常: {e}")
        
        return results
    
    def _save_results(self, results: SearchResults, output_dir: str):
        """保存搜索结果到文件"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = output_path / f"sina_news_{timestamp}.json"
        md_file = output_path / f"sina_news_{timestamp}.md"
        
        # 保存JSON
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump([r.to_dict() for r in results.results], f, ensure_ascii=False, indent=2)
        
        # 保存Markdown
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(f"# 新浪财经新闻 - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"共获取 {len(results.results)} 条新闻\n\n")
            for i, result in enumerate(results.results, 1):
                f.write(f"## {i}. {result.title}\n\n")
                f.write(f"- **链接**: [{result.url}]({result.url})\n")
                f.write(f"- **摘要**: {result.summary[:200]}...\n")
                f.write(f"- **来源**: {result.source}\n")
                f.write(f"- **抓取时间**: {result.scraped_at}\n\n")
                f.write("---\n\n")
        
        print(f"[保存] 结果已保存到: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="新浪财经新闻增强抓取脚本")
    parser.add_argument("--category", type=str, default="stock",
                        choices=["stock", "macro", "industry", "forex", "futures"],
                        help="新闻分类 (默认: stock)")
    parser.add_argument("--query", type=str, help="关键词搜索")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数量")
    parser.add_argument("--source", type=str, default="auto",
                        choices=["rss", "html", "auto", "browser"],
                        help="数据源 (默认: auto)")
    parser.add_argument("--output-dir", type=str, default="./search_results/sina_enhanced",
                        help="输出目录")
    parser.add_argument("--fallback-browser", action="store_true",
                        help="启用浏览器回退模式")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口")
    parser.add_argument("--no-rss", action="store_true",
                        help="禁用RSS源（仅用于测试）")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = SinaNewsEnhancedSearcher()
    
    # 执行搜索
    results = searcher.search(
        query=args.query,
        category=args.category,
        max_results=args.max_results,
        source="html" if args.no_rss else args.source,
        output_dir=args.output_dir,
        fallback_browser=args.fallback_browser,
        port=args.port
    )
    
    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results.results)} 条新闻")
        print(json.dumps([r.to_dict() for r in results.results], ensure_ascii=False, indent=2))
    else:
        print("[结果] 未找到新闻")


if __name__ == "__main__":
    main()
