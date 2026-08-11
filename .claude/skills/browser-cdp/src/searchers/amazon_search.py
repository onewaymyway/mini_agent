#!/usr/bin/env python
"""
amazon_search.py - Amazon 商品搜索器

使用 browser-cdp skill 搜索 Amazon 商品，获取标题、价格、评分、评论数等信息。
Amazon 有反爬机制，建议使用 stealth 模式并控制请求频率。

用法:
    python amazon_search.py "wireless headphones" --max-results 10
    python amazon_search.py "laptop" --max-results 20 --output-dir ./results
    python amazon_search.py "mechanical keyboard" --port 9333 --stealth
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
from src.searchers.utils import random_delay, save_results, dedup_results
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


# ========== Amazon 专用配置 ==========
AMAZON_BASE = "https://www.amazon.com"
AMAZON_SEARCH_URL = "https://www.amazon.com/s?k={query}"


class AmazonSearcher(BaseSearcher):
    """Amazon 商品搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "query"
        self._extra_param = ""
    
    @property
    def source_name(self) -> str:
        return "amazon"
    
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
        session_name: Optional[str] = "amazon_session",
    ) -> List[Dict]:
        """搜索 Amazon 商品"""
        self._search_type = search_type
        self._extra_param = query
        
        print(f"[Amazon] 搜索类型: {search_type}, 关键词: {query}")
        
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
            results = self._search_products(port, tab_id, query, max_results)
        elif search_type == "category":
            results = self._search_category(port, tab_id, query, max_results)
        else:
            results = self._search_products(port, tab_id, query, max_results)
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"amazon_{search_type}_{query[:20]}", "json")
            save_results(results, output_dir, f"amazon_{search_type}_{query[:20]}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 件商品")
        return results
    
    def _search_products(self, port: int, tab_id: str, query: str, limit: int) -> List[Dict]:
        """搜索商品"""
        encoded_query = quote(query)
        url = AMAZON_SEARCH_URL.format(query=encoded_query)
        
        # 导航到搜索结果页
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 随机延迟
        delay = random_delay(2.0, 4.0)
        time.sleep(delay)
        
        # 检查是否触发验证码
        js_check = r"""
(() => {
  const captcha = document.querySelector('#captcha, .g-recaptcha, [class*="captcha"]');
  return captcha ? 'captcha_detected' : 'ok';
})()
"""
        check_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_check
        ])
        
        if check_result.returncode == 0 and 'captcha_detected' in check_result.stdout:
            print("[警告] 检测到验证码，请手动完成验证后重试")
            return []
        
        # 提取商品列表
        js_code = '''
(function() {
    var results = [];
    var items = document.querySelectorAll('[data-component-type="s-search-result"]');
    
    items.forEach(function(item, index) {
        if (index >= ''' + str(limit) + ''') return;
        
        var titleEl = item.querySelector('h2 a, .s-title-instructions-style a');
        var linkEl = item.querySelector('h2 a');
        var priceEl = item.querySelector('.a-price-whole, .a-offscreen');
        var ratingEl = item.querySelector('.a-icon-alt, [class*="rating"]');
        var reviewEl = item.querySelector('[class*="reviewCount"]');
        
        if (!titleEl) return;
        
        var title = titleEl.textContent.trim();
        var url = linkEl ? linkEl.href : '';
        var price = priceEl ? priceEl.textContent.trim() : '';
        var rating = ratingEl ? ratingEl.textContent.trim() : '';
        var reviews = reviewEl ? reviewEl.textContent.trim() : '';
        
        if (title && url) {
            results.push({
                title: title,
                url: url,
                price: price,
                rating: rating,
                reviews: reviews,
                source: 'amazon',
                scraped_at: new Date().toISOString()
            });
        }
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
        """搜索分类商品"""
        url = f"{AMAZON_BASE}/s?k={quote(category)}&i=aps"
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 提取分类商品
        js_code = '''
(function() {
    var results = [];
    var items = document.querySelectorAll('[data-component-type="s-search-result"]');
    
    items.forEach(function(item, index) {
        if (index >= ''' + str(limit) + ''') return;
        
        var titleEl = item.querySelector('h2 a');
        var linkEl = item.querySelector('h2 a');
        var priceEl = item.querySelector('.a-price-whole');
        
        if (!titleEl) return;
        
        var title = titleEl.textContent.trim();
        var url = linkEl ? linkEl.href : '';
        var price = priceEl ? priceEl.textContent.trim() : '';
        
        if (title && url) {
            results.push({
                title: title,
                url: url,
                price: price,
                category: ''' + json.dumps(category) + ''',
                source: 'amazon',
                scraped_at: new Date().toISOString()
            });
        }
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
    
    def get_detail(self, url: str, port: int, tab_id: str) -> Dict:
        """获取商品详情"""
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
    var info = {};
    
    // 标题
    var titleEl = document.querySelector('#productTitle');
    info.title = titleEl ? titleEl.textContent.trim() : '';
    
    // 价格
    var priceEl = document.querySelector('#priceblock_ourprice, #priceblock_dealprice');
    info.price = priceEl ? priceEl.textContent.trim() : '';
    
    // 评分
    var ratingEl = document.querySelector('#acrCustomerReviewText');
    info.rating = ratingEl ? ratingEl.textContent.trim() : '';
    
    // 库存
    var stockEl = document.querySelector('#availability span');
    info.stock = stockEl ? stockEl.textContent.trim() : '';
    
    // 描述
    var descEl = document.querySelector('#productDescription');
    info.description = descEl ? descEl.textContent.trim().substring(0, 500) : '';
    
    info.url = window.location.href;
    info.scraped_at = new Date().toISOString();
    
    return info;
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
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--goto", AMAZON_BASE,
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="Amazon 商品搜索器")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", default="query", choices=["query", "category"])
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--session", default="amazon_session", help="浏览器会话名称")
    
    args = parser.parse_args()
    
    searcher = AmazonSearcher()
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
