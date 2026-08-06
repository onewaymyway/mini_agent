#!/usr/bin/env python
"""
xueqiu_search.py - 雪球金融数据搜索自动化脚本

使用 browser-cdp skill 搜索雪球股票数据，支持行情、讨论、组合持仓抓取。

用法:
    python xueqiu_search.py --symbol AAPL --max-results 10
    python xueqiu_search.py --symbol 00700 --type discussion --max-results 20
    python xueqiu_search.py --portfolio P123456 --output-dir ./results
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


# ========== 雪球专用配置 ==========
XQ_BASE = "https://xueqiu.com"
XQ_API_QUOTE = "https://stock.xueqiu.com/v5/stock/batch/quote.json"
XQ_API_STATUS = "https://xueqiu.com/statuses/original/list.json"
XQ_API_USER = "https://xueqiu.com/v4/statuses/user_timeline.json"

# 股票代码前缀映射
SYMBOL_PREFIX = {
    "sh": "",      # 上交所
    "sz": "",      # 深交所
    "hk": "",      # 港股（需加 .HK）
    "us": "",      # 美股
}


class XueqiuSearcher(BaseSearcher):
    """雪球金融数据搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._symbol = ""
        self._type = "quote"  # quote/discussion/portfolio
    
    @property
    def source_name(self) -> str:
        return "xueqiu"
    
    @property
    def supported_types(self) -> List[str]:
        return ["quote", "discussion", "portfolio"]
    
    def search(
        self,
        query: str = "",
        symbol: Optional[str] = None,
        type: str = "quote",
        max_results: int = 10,
        port: int = 9333,
        tab_id: Optional[str] = None,
        stealth: bool = True,
        output_dir: Optional[str] = None,
        wait_timeout: int = 30,
        session_name: Optional[str] = "xueqiu_session",
    ) -> List[Dict]:
        """搜索雪球数据
        
        Args:
            query: 搜索关键词（股票名/代码）
            symbol: 股票代码（如 AAPL, 00700, sh600519）
            type: 数据类型（quote/discussion/portfolio）
            max_results: 最大结果数
            port: 浏览器调试端口
            tab_id: Tab ID
            stealth: 是否启用反检测模式
            output_dir: 输出目录
            wait_timeout: 等待超时时间
            session_name: 浏览器会话名称（用于登录态持久化）
            
        Returns:
            数据列表
        """
        self._symbol = symbol or query
        self._type = type
        
        print(f"[雪球搜索] 代码: {self._symbol}, 类型: {type}")
        
        # 确保浏览器连接（雪球必须登录）
        if tab_id is None:
            result = ensure_browser(
                port=port, 
                stealth=stealth,
                session_name=session_name,
                dedicated=True,
            )
            if result.get("error"):
                print(f"[错误] 浏览器启动失败: {result['error']}")
                print("[提示] 雪球需要登录态，请首次使用时手动登录")
                return []
            tab_id = result.get("tab_id")
            port = result.get("port", port)
            print(f"[浏览器] 端口: {port}, Tab: {tab_id}")
        
        # 等待用户登录（如果是新会话）
        if not self._check_login_status(port, tab_id):
            print("[提示] 请先在浏览器中登录雪球，然后按回车继续...")
            input()
        
        # 根据类型执行搜索
        if type == "quote":
            results = self._fetch_quote(port, tab_id)
        elif type == "discussion":
            results = self._fetch_discussion(port, tab_id, max_results)
        elif type == "portfolio":
            results = self._fetch_portfolio(port, tab_id, query)
        else:
            results = self._fetch_quote(port, tab_id)
        
        # 限制结果数量
        results = results[:max_results]
        
        # 保存结果
        if output_dir:
            save_results(results, output_dir, f"xueqiu_{self._symbol}_{type}", "json")
            save_results(results, output_dir, f"xueqiu_{self._symbol}_{type}", "csv")
        
        print(f"[完成] 共抓取 {len(results)} 条数据")
        return results
    
    def _check_login_status(self, port: int, tab_id: str) -> bool:
        """检查登录状态"""
        js_code = '''
        (function() {
            var cookie = document.cookie;
            return cookie.indexOf('xq_a_token') !== -1 || cookie.indexOf('u') !== -1;
        })()
        '''
        result = run_cmd([
            PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_console.py"),
            "--port", str(port),
            "--tab", str(tab_id),
            "--eval", js_code,
        ])
        try:
            return json.loads(result.get("result", "false"))
        except:
            return False
    
    def _fetch_quote(self, port: int, tab_id: str) -> List[Dict]:
        """获取行情数据"""
        # 使用雪球 API 获取行情
        symbol = self._format_symbol(self._symbol)
        url = f"{XQ_API_QUOTE}?symbol={symbol}"
        
        # 通过浏览器执行 API 请求
        js_code = f'''
        (function() {{
            var results = [];
            fetch('{url}', {{
                method: 'GET',
                headers: {{
                    'Accept': 'application/json',
                    'Cookie': document.cookie,
                    'User-Agent': navigator.userAgent
                }}
            }})
            .then(function(resp) {{ return resp.json(); }})
            .then(function(data) {{
                if (data.data && data.data.items) {{
                    data.data.items.forEach(function(item) {{
                        results.push({{
                            symbol: item.symbol,
                            name: item.name,
                            current: item.current,
                            change_percent: item.percent,
                            change_amount: item.change_amount,
                            volume: item.volume,
                            market_cap: item.capitalization,
                            pe_ratio: item.pe_ttm,
                            high: item.high,
                            low: item.low,
                            open: item.open,
                            prev_close: item.prev_close,
                            source: 'xueqiu',
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
    
    def _fetch_discussion(self, port: int, tab_id: str, limit: int) -> List[Dict]:
        """获取讨论数据"""
        symbol = self._format_symbol(self._symbol)
        url = f"{XQ_API_STATUS}?symbol={symbol}&page=1&type={limit}"
        
        js_code = f'''
        (function() {{
            var results = [];
            fetch('{url}', {{
                method: 'GET',
                headers: {{
                    'Accept': 'application/json',
                    'Cookie': document.cookie,
                    'User-Agent': navigator.userAgent
                }}
            }})
            .then(function(resp) {{ return resp.json(); }})
            .then(function(data) {{
                if (data.data && data.data.list) {{
                    data.data.list.forEach(function(item) {{
                        results.push({{
                            id: item.id,
                            text: item.text,
                            title: item.title,
                            user: item.user ? item.user.screen_name : '',
                            user_id: item.user ? item.user.id : '',
                            created_at: item.created_at,
                            like_count: item.like_count,
                            reply_count: item.replies_count,
                            source: 'xueqiu',
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
    
    def _fetch_portfolio(self, port: int, tab_id: str, portfolio_id: str) -> List[Dict]:
        """获取组合持仓数据"""
        url = f"{XQ_BASE}/p/{portfolio_id}.json"
        
        js_code = f'''
        (function() {{
            var results = [];
            fetch('{url}', {{
                method: 'GET',
                headers: {{
                    'Accept': 'application/json',
                    'Cookie': document.cookie,
                    'User-Agent': navigator.userAgent
                }}
            }})
            .then(function(resp) {{ return resp.json(); }})
            .then(function(data) {{
                if (data.data && data.data.holdings) {{
                    data.data.holdings.forEach(function(item) {{
                        results.push({{
                            symbol: item.symbol,
                            name: item.name,
                            weight: item.weight,
                            shares: item.shares,
                            cost: item.cost_price,
                            current: item.current_price,
                            profit: item.profit_ratio,
                            source: 'xueqiu',
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
    
    def _format_symbol(self, symbol: str) -> str:
        """格式化股票代码"""
        symbol = symbol.upper().strip()
        # 处理 A 股代码
        if len(symbol) == 6 and symbol.isdigit():
            # 判断沪市/深市
            if symbol.startswith(('6', '5')):
                return f"sh{symbol}"
            else:
                return f"sz{symbol}"
        return symbol
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取详情页"""
        raise NotImplementedError("雪球数据通过 API 获取")
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            result = run_cmd([
                PYTHON_CMD, str(SKILL_DIR.parent / "core" / "browser_nav.py"),
                "--goto", XQ_BASE,
                "--wait-for", "stable",
                "--timeout", 10,
            ])
            return "error" not in result
        except Exception:
            return False


def main():
    parser = argparse.ArgumentParser(description="雪球金融数据搜索器")
    parser.add_argument("query", nargs="?", help="股票代码或名称")
    parser.add_argument("--symbol", help="股票代码（如 AAPL, 00700, sh600519）")
    parser.add_argument("--type", default="quote", choices=["quote", "discussion", "portfolio"])
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--stealth", action="store_true", default=True)
    parser.add_argument("--no-stealth", dest="stealth", action="store_false")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--session", default="xueqiu_session", help="浏览器会话名称")
    
    args = parser.parse_args()
    
    searcher = XueqiuSearcher()
    results = searcher.search(
        query=args.query or "",
        symbol=args.symbol,
        type=args.type,
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