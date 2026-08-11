#!/usr/bin/env python
"""
bbc_news_search.py - BBC 新闻搜索器

使用 browser-cdp skill 搜索 BBC 新闻，支持关键词搜索、分类浏览。

用法:
    python bbc_news_search.py --query "climate change" --max-results 10
    python bbc_news_search.py --category "world" --max-results 20
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
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.searchers.base import SearcherConfig, SearchResult, BaseSearcher
from src.searchers.utils import (
    random_delay, get_random_ua, save_results, clean_text, truncate_text
)
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== BBC 专用配置 ==========
BBC_BASE = "https://www.bbc.com"
BBC_SEARCH_URL = "https://www.bbc.com/search?q={query}"
BBC_NEWS_URL = "https://www.bbc.com/news"
BBC_CATEGORIES = {
    "world": "https://www.bbc.com/news/world",
    "business": "https://www.bbc.com/news/business",
    "technology": "https://www.bbc.com/news/technology",
    "science": "https://www.bbc.com/news/science_and_environment",
    "entertainment": "https://www.bbc.com/news/entertainment_and_arts",
    "politics": "https://www.bbc.com/news/politics",
}


class BBCNewsSearcher(BaseSearcher):
    """BBC 新闻搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "query"  # query/category
        self._extra_param = ""
    
    @property
    def source_name(self) -> str:
        return "bbc_news"
    
    @property
    def supported_types(self) -> List[str]:
        return ["query", "category"]
    
    def search(
        self,
        query: str = "",
        search_type: str = "query",
        max_results: int = 10,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        session_name: Optional[str] = "bbc_session",
    ) -> List[Dict]:
        """搜索 BBC 新闻
        
        Args:
            query: 搜索关键词或分类名称
            search_type: 搜索类型（query/category）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            session_name: 浏览器会话名称
            
        Returns:
            新闻数据列表
        """
        self._search_type = search_type
        self._extra_param = query
        
        print(f"[BBC新闻搜索] 类型: {search_type}, 关键词: {query}")
        
        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(
                port=port,
                stealth=stealth,
                session_name=session_name,
                dedicated=True,
            )
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")
        
        # 根据类型执行搜索
        if search_type == "query":
            results = self._search_news(port, tab_id, query, max_results)
        elif search_type == "category":
            results = self._search_category(port, tab_id, query, max_results)
        else:
            results = self._search_news(port, tab_id, query, max_results)
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"bbc_{search_type}_{query[:20]}", "json")
            save_results(results, output_dir, f"bbc_{search_type}_{query[:20]}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 条新闻")
        return results
    
    def _search_news(self, port: int, tab_id: str, query: str, limit: int) -> List[Dict]:
        """搜索新闻"""
        encoded_query = quote(query)
        url = BBC_SEARCH_URL.format(query=encoded_query)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 提取新闻信息
        js_code = '''
        (function() {
            var results = [];
            var newsItems = document.querySelectorAll('a[data-testid="card-link"], .gs-c-promo-link');
            
            newsItems.forEach(function(link, index) {
                if (index >= ''' + str(limit) + ''') return;
                
                var titleEl = link.querySelector('h2, h3, .gs-c-reel-heading__title');
                if (!titleEl) return;
                
                var title = titleEl.textContent.trim();
                var url = link.href || '';
                
                // 获取摘要
                var summaryEl = link.querySelector('p, .gs-c-promo-summary');
                var summary = summaryEl ? summaryEl.textContent.trim() : '';
                
                // 获取时间
                var timeEl = link.querySelector('time, .gs-c-meta__date');
                var published_time = timeEl ? timeEl.getAttribute('datetime') || timeEl.textContent.trim() : '';
                
                // 获取分类
                var categoryEl = link.querySelector('.os-role a, .gs-c-meta__category');
                var category = categoryEl ? categoryEl.textContent.trim() : '';
                
                results.push({
                    title: title,
                    url: url,
                    summary: truncate_text(summary, 200),
                    published_time: published_time,
                    category: category,
                    source: 'bbc_news',
                    scraped_at: new Date().toISOString()
                });
            });
            
            return results;
        })()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        try:
            data = json.loads(result.get("result", "[]"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            print(f"[警告] JSON 解析失败: {result.get('result')}")
            return []
    
    def _search_category(self, port: int, tab_id: str, category: str, limit: int) -> List[Dict]:
        """浏览分类页面"""
        category = category.lower()
        if category not in BBC_CATEGORIES:
            print(f"[警告] 未知分类: {category}，使用默认新闻页")
            url = BBC_NEWS_URL
        else:
            url = BBC_CATEGORIES[category]
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 提取分类新闻
        js_code = '''
        (function() {
            var results = [];
            var newsItems = document.querySelectorAll('a[data-testid="card-link"], .gs-c-promo-link');
            
            newsItems.forEach(function(link, index) {
                if (index >= ''' + str(limit) + ''') return;
                
                var titleEl = link.querySelector('h2, h3, .gs-c-reel-heading__title');
                if (!titleEl) return;
                
                var title = titleEl.textContent.trim();
                var url = link.href || '';
                
                var summaryEl = link.querySelector('p, .gs-c-promo-summary');
                var summary = summaryEl ? summaryEl.textContent.trim() : '';
                
                var timeEl = link.querySelector('time, .gs-c-meta__date');
                var published_time = timeEl ? timeEl.getAttribute('datetime') || timeEl.textContent.trim() : '';
                
                results.push({
                    title: title,
                    url: url,
                    summary: truncate_text(summary, 200),
                    published_time: published_time,
                    category: ''' + json.dumps(category) + ''',
                    source: 'bbc_news',
                    scraped_at: new Date().toISOString()
                });
            });
            
            return results;
        })()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        try:
            data = json.loads(result.get("result", "[]"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            print(f"[警告] JSON 解析失败: {result.get('result')}")
            return []
    
    def get_article_detail(self, url: str, port: int, tab_id: str) -> Dict:
        """获取文章详情"""
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        js_code = '''
        (function() {
            var article = {};
            
            // 标题
            var titleEl = document.querySelector('h1[data-testid="metadata-wrapper"]');
            article.title = titleEl ? titleEl.textContent.trim() : '';
            
            // 正文
            var contentEl = document.querySelector('[data-testid="article-body"]');
            article.content = contentEl ? contentEl.textContent.trim() : '';
            
            // 作者
            var authorEl = document.querySelector('[data-testid="article-byline"]');
            article.author = authorEl ? authorEl.textContent.trim() : '';
            
            // 发布时间
            var timeEl = document.querySelector('time');
            article.published_time = timeEl ? timeEl.getAttribute('datetime') : '';
            
            // 分类
            var categoryEl = document.querySelector('[data-testid="taxonomy"]');
            article.category = categoryEl ? categoryEl.textContent.trim() : '';
            
            article.url = window.location.href;
            article.source = 'bbc_news';
            article.scraped_at = new Date().toISOString();
            
            return article;
        })()
        '''
        
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        
        try:
            return json.loads(result.get("result", "{}"))
        except json.JSONDecodeError:
            return {}
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取文章详情"""
        raise NotImplementedError("请使用 get_article_detail 方法")
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--goto", BBC_BASE,
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="BBC 新闻搜索器")
    parser.add_argument("query", help="搜索关键词或分类名称")
    parser.add_argument("--type", default="query", choices=["query", "category"])
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--session", default="bbc_session", help="浏览器会话名称")
    
    args = parser.parse_args()
    
    searcher = BBCNewsSearcher()
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
        port=args.port,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.timeout,
        session_name=args.session,
    )
    
    if results:
        print("\n" + json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
