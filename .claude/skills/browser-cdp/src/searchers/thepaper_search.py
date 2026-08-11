#!/usr/bin/env python
"""
thepaper_search.py - 澎湃新闻搜索器

使用 browser-cdp skill 搜索澎湃新闻，获取新闻标题、链接、摘要、发布时间等信息。
澎湃新闻是上海报业集团旗下新媒体平台，以时政新闻见长。

用法:
    python thepaper_search.py "人工智能" --max-results 10
    python thepaper_search.py "经济政策" --max-results 20 --output-dir ./results
    python thepaper_search.py "国际关系" --port 9333 --stealth
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


# ========== 澎湃新闻专用配置 ==========
THEPAPER_BASE = "https://www.thepaper.cn"
THEPAPER_SEARCH_URL = "https://search.thepaper.cn/search?q={query}"
THEPAPER_CHANNEL_URL = "https://www.thepaper.cn/channel_{channel_id}"


class ThePaperSearcher(BaseSearcher):
    """澎湃新闻搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "query"  # query/channel
        self._extra_param = ""
    
    @property
    def source_name(self) -> str:
        return "thepaper"
    
    @property
    def supported_types(self) -> List[str]:
        return ["query", "channel"]
    
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
        session_name: Optional[str] = "thepaper_session",
    ) -> List[Dict]:
        """搜索澎湃新闻新闻
        
        Args:
            query: 搜索关键词
            search_type: 搜索类型（query/channel）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            session_name: 浏览器会话名称
            
        Returns:
            新闻列表
        """
        self._search_type = search_type
        self._extra_param = query
        
        print(f"[澎湃新闻] 搜索类型: {search_type}, 关键词: {query}")
        
        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(
                port=port,
                stealth=stealth,
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
        elif search_type == "channel":
            results = self._search_channel(port, tab_id, query, max_results)
        else:
            results = self._search_news(port, tab_id, query, max_results)
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"thepaper_{search_type}_{query[:20]}", "json")
            save_results(results, output_dir, f"thepaper_{search_type}_{query[:20]}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 条新闻")
        return results
    
    def _search_news(self, port: int, tab_id: str, query: str, limit: int) -> List[Dict]:
        """搜索新闻"""
        encoded_query = quote(query)
        url = THEPAPER_SEARCH_URL.format(query=encoded_query)
        
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
        delay = random_delay(1.5, 3.0)
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
        
        # 提取新闻列表
        js_code = '''
(function() {
    var results = [];
    var items = document.querySelectorAll('.search-result-item, .news-item, [class*="result"], [class*="news"]');
    
    items.forEach(function(item, index) {
        if (index >= ''' + str(limit) + ''') return;
        
        var titleEl = item.querySelector('a, h3, .title');
        var linkEl = item.querySelector('a');
        var summaryEl = item.querySelector('.summary, .abstract, p');
        var dateEl = item.querySelector('.date, .time, [class*="date"]');
        
        if (!titleEl) return;
        
        var title = titleEl.textContent.trim();
        var url = linkEl ? linkEl.href : '';
        var summary = summaryEl ? summaryEl.textContent.trim() : '';
        var date = dateEl ? dateEl.textContent.trim() : '';
        
        if (title && url) {
            results.push({
                title: title,
                url: url,
                summary: summary.substring(0, 200),
                date: date,
                source: 'thepaper',
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
    
    def _search_channel(self, port: int, tab_id: str, channel: str, limit: int) -> List[Dict]:
        """搜索频道新闻"""
        # 澎湃新闻频道ID映射
        channel_ids = {
            'news': '1',
            'politics': '2',
            'finance': '3',
            'world': '4',
            'tech': '5',
            'sports': '6',
            'culture': '7',
        }
        
        channel_id = channel_ids.get(channel.lower(), channel)
        url = THEPAPER_CHANNEL_URL.format(channel_id=channel_id)
        
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", url,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ])
        
        # 提取频道新闻
        js_code = '''
(function() {
    var results = [];
    var items = document.querySelectorAll('.news-list-item, .list-item, [class*="list"] li');
    
    items.forEach(function(item, index) {
        if (index >= ''' + str(limit) + ''') return;
        
        var titleEl = item.querySelector('a, h3, .title');
        var linkEl = item.querySelector('a');
        var dateEl = item.querySelector('.date, .time');
        
        if (!titleEl) return;
        
        var title = titleEl.textContent.trim();
        var url = linkEl ? linkEl.href : '';
        var date = dateEl ? dateEl.textContent.trim() : '';
        
        if (title && url) {
            results.push({
                title: title,
                url: url,
                channel: ''' + json.dumps(channel) + ''',
                date: date,
                source: 'thepaper',
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
        """获取新闻详情"""
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
    var titleEl = document.querySelector('h1, .title, #title');
    info.title = titleEl ? titleEl.textContent.trim() : '';
    
    // 作者
    var authorEl = document.querySelector('.author, [class*="author"]');
    info.author = authorEl ? authorEl.textContent.trim() : '';
    
    // 发布时间
    var dateEl = document.querySelector('.date, .time, [class*="date"]');
    info.publish_date = dateEl ? dateEl.textContent.trim() : '';
    
    // 正文
    var contentEl = document.querySelector('.content, #content, .article-content, [class*="article"]');
    info.content = contentEl ? contentEl.textContent.trim().substring(0, 1000) : '';
    
    // 来源
    var sourceEl = document.querySelector('.source, [class*="source"]');
    info.source_name = sourceEl ? sourceEl.textContent.trim() : '澎湃新闻';
    
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
                "--goto", THEPAPER_BASE,
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="澎湃新闻搜索器")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", default="query", choices=["query", "channel"])
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--session", default="thepaper_session", help="浏览器会话名称")
    
    args = parser.parse_args()
    
    searcher = ThePaperSearcher()
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
