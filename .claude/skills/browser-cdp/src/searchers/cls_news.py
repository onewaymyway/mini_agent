#!/usr/bin/env python
"""
cls_news.py - 财联社电报新闻搜索自动化脚本

使用 browser-cdp skill 抓取财联社电报和新闻数据。

用法:
    python cls_news.py --category telegraph --max-results 50
    python cls_news.py --category finance --query "茅台" --max-results 20
    python cls_news.py --category tech --output-dir ./results
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


# ========== 财联社专用配置 ==========
CLS_BASE = "https://www.cls.cn"
CLS_API_TELEGRAPH = "https://www.cls.cn/nodeapi/updateTelegraph"
CLS_API_ROLL = "https://www.cls.cn/v3/roll/home/get/roll_data"
CLS_API_SEARCH = "https://www.cls.cn/searchpage/abc"

# 新闻分类映射
CATEGORY_MAP = {
    "telegraph": "telegraph",      # 电报（实时）
    "finance": "finance",          # 财经
    "tech": "tech",                # 科技
    "stock": "stock",              # 股票
    "crypto": "crypto",            # 加密货币
    "macro": "macro",              # 宏观
    "world": "world",              # 国际
}

# 重要性评级映射
IMPORTANCE_MAP = {
    "0": "低",
    "1": "中",
    "2": "高",
    "3": "极高",
}


class ClsNewsSearcher(BaseSearcher):
    """财联社新闻搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._category = "telegraph"
        self._query = ""
    
    @property
    def source_name(self) -> str:
        return "cls"
    
    @property
    def supported_types(self) -> List[str]:
        return ["telegraph", "finance", "tech", "stock", "crypto", "macro", "world"]
    
    def search(
        self,
        query: str = "",
        category: str = "telegraph",
        max_results: int = 50,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
    ) -> List[Dict]:
        """搜索财联社新闻
        
        Args:
            query: 搜索关键词
            category: 新闻分类（telegraph/finance/tech/stock等）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            
        Returns:
            新闻列表
        """
        self._category = category
        self._query = query
        
        print(f"[财联社搜索] 分类: {category}, 关键词: {query}")
        
        # 确保浏览器连接
        if tab_id is None:
            result = ensure_browser(port=port, stealth=stealth)
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")
        
        # 导航到财联社
        nav_result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", CLS_BASE,
            "--wait-for", "networkidle",
            "--timeout", str(wait_timeout),
        ] + (["--stealth"] if stealth else []))
        
        if nav_result.get("error"):
            print(f"[错误] 导航失败: {nav_result['error']}")
            return []
        
        # 抓取数据
        if category == "telegraph":
            results = self._fetch_telegraph(port, tab_id, max_results)
        elif query:
            results = self._search_news(port, tab_id, query, max_results)
        else:
            results = self._fetch_category(port, tab_id, category, max_results)
        
        # 限制结果数量
        results = results[:max_results]
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"cls_{category}", "json")
            save_results(results, output_dir, f"cls_{category}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 条新闻")
        return results
    
    def _fetch_telegraph(self, port: int, tab_id: str, limit: int) -> List[Dict]:
        """抓取电报流"""
        # 电报流 API（无需登录）
        url = f"{CLS_API_TELEGRAPH}?app=CailianpressWeb&os=web&sv=7.7.5&rn={limit}"
        
        js_code = f'''
        (function() {{
            var results = [];
            fetch('{url}', {{
                method: 'GET',
                headers: {{
                    'Accept': 'application/json',
                    'User-Agent': navigator.userAgent
                }}
            }})
            .then(function(resp) {{ return resp.json(); }})
            .then(function(data) {{
                if (data.data && data.data.roll_data) {{
                    data.data.roll_data.forEach(function(item) {{
                        results.push({{
                            id: item.id,
                            title: item.title,
                            content: item.content,
                            publish_time: item.update_time,
                            category: item.channel?.title || '',
                            importance: IMPORTANCE_MAP[item.importance] || '中',
                            tags: item.tags ? item.tags.map(function(t) {{ return t.title; }}).join(', ') : '',
                            url: '{CLS_BASE}/detail/{item.id}',
                            source: 'cls',
                            scraped_at: new Date().toISOString()
                        }});
                    }});
                }}
                return results;
            }})
            .catch(function(err) {{ return [{{"error": err.message}}]; }});
        }})()
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
    
    def _fetch_category(self, port: int, tab_id: str, category: str, limit: int) -> List[Dict]:
        """抓取分类新闻"""
        # 分类新闻 API
        url = f"{CLS_API_ROLL}?channel={category}&page=1&rn={limit}"
        
        js_code = f'''
        (function() {{
            var results = [];
            fetch('{url}', {{
                method: 'GET',
                headers: {{
                    'Accept': 'application/json',
                    'User-Agent': navigator.userAgent
                }}
            }})
            .then(function(resp) {{ return resp.json(); }})
            .then(function(data) {{
                if (data.data && data.data.data) {{
                    data.data.data.forEach(function(item) {{
                        results.push({{
                            id: item.id,
                            title: item.title,
                            content: item.content,
                            publish_time: item.update_time,
                            category: item.channel?.title || '{category}',
                            importance: IMPORTANCE_MAP[item.importance] || '中',
                            tags: item.tags ? item.tags.map(function(t) {{ return t.title; }}).join(', ') : '',
                            url: '{CLS_BASE}/detail/{item.id}',
                            source: 'cls',
                            scraped_at: new Date().toISOString()
                        }});
                    }});
                }}
                return results;
            }})
            .catch(function(err) {{ return [{{"error": err.message}}]; }});
        }})()
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
    
    def _search_news(self, port: int, tab_id: str, query: str, limit: int) -> List[Dict]:
        """搜索新闻"""
        search_url = f"{CLS_BASE}/searchpage/abc?keyword={quote(query)}"
        
        # 导航到搜索结果页
        run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--goto", search_url,
            "--wait-for", "networkidle",
            "--timeout", "30",
        ])
        
        # 提取搜索结果
        js_code = f'''
        (function() {{
            var results = [];
            var items = document.querySelectorAll('.search-result-item, .news-item, [class*="result"][class*="item"]');
            items.forEach(function(item) {{
                var titleEl = item.querySelector('.title, h3, [class*="title"]');
                var linkEl = item.querySelector('a[href]');
                var timeEl = item.querySelector('.time, [class*="time"]');
                
                if (titleEl && linkEl) {{
                    results.push({{
                        title: titleEl.textContent.trim(),
                        url: linkEl.href,
                        publish_time: timeEl ? timeEl.textContent.trim() : '',
                        source: 'cls',
                        scraped_at: new Date().toISOString()
                    }});
                }}
            }});
            return results.slice(0, {limit});
        }})()
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
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取新闻详情"""
        raise NotImplementedError("财联社新闻通过 API 获取完整内容")
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--goto", CLS_BASE,
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="财联社新闻搜索器")
    parser.add_argument("--category", default="telegraph", 
                        choices=["telegraph", "finance", "tech", "stock", "crypto", "macro", "world"])
    parser.add_argument("--query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    
    args = parser.parse_args()
    
    searcher = ClsNewsSearcher()
    results = searcher.search(
        query=args.query or "",
        category=args.category,
        max_results=args.max_results,
        port=args.port,
        stealth=args.stealth,
        output_dir=args.output_dir,
        wait_timeout=args.timeout,
    )
    
    if results:
        print("\n" + json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
