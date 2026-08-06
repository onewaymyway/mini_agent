#!/usr/bin/env python
"""
必应搜索器 - 真实 CDP 搜索实现

通过必应搜索获取结构化结果，支持分页、去重、排序。
"""

import sys
import json
import time
import urllib.parse
from pathlib import Path
from typing import List, Optional, Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.searchers.base import BaseSearcher, SearchResult, SearchResults, SearcherConfig
from src.searchers.utils import random_delay, get_random_ua, save_results
from src.searchers.browser_utils import ensure_browser, run_cmd, PYTHON_CMD, SKILL_DIR


class BingSearcher(BaseSearcher):
    """必应搜索器 - 真实 CDP 实现"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.bing.com"
        self.search_url_template = "https://www.bing.com/search?q={query}"
        self._port = kwargs.get('port', 9333)
        self._tab_id = None
    
    @property
    def source_name(self) -> str:
        return "必应"
    
    @property
    def supported_types(self) -> list:
        return ["web", "news", "image", "video"]
    
    def _get_tab_id(self) -> Optional[str]:
        """获取或创建 tab ID"""
        if self._tab_id:
            return self._tab_id
        
        result = ensure_browser(port=self._port, stealth=self.config.stealth)
        if result.get("error"):
            print(f"[错误] 浏览器启动失败: {result['error']}")
            return None
        self._tab_id = result.get("tab_id")
        self._port = result.get("port", self._port)
        return self._tab_id
    
    def _build_search_url(self, query: str, page: int = 1) -> str:
        """构建搜索 URL"""
        encoded_query = urllib.parse.quote(query)
        if page > 1:
            return f"{self.base_url}/search?q={encoded_query}&first={(page-1)*10+1}"
        return self.search_url_template.format(query=encoded_query)
    
    def _extract_results_js(self) -> str:
        """提取搜索结果的 JavaScript 代码"""
        return '''
        (function() {
            var results = [];
            var containers = document.querySelectorAll('.b_algo, .b_algoContainer, li.b_algo, .b_algoWrapper');
            
            containers.forEach(function(container) {
                var titleEl = container.querySelector('h2 a, .b_title a, a[href]');
                var linkEl = container.querySelector('h2 a, .b_title a');
                var snippetEl = container.querySelector('.b_caption, p, .b_lineBreak');
                
                if (titleEl || linkEl) {
                    var title = titleEl ? titleEl.textContent.trim() : '';
                    var url = linkEl ? linkEl.href : '';
                    var snippet = snippetEl ? snippetEl.textContent.trim() : '';
                    
                    if (title && title.length > 2 && url && url.startsWith('http')) {
                        results.push({
                            title: title.substring(0, 200),
                            url: url,
                            snippet: snippet.substring(0, 500)
                        });
                    }
                }
            });
            
            return results;
        })()
        '''
    
    def search(self, query: str, max_results: int = 20, **kwargs) -> SearchResults:
        """执行必应搜索"""
        results = SearchResults(source=self.source_name, query=query)
        
        tab_id = self._get_tab_id()
        if not tab_id:
            results.error = "浏览器连接失败"
            return results
        
        try:
            search_url = self._build_search_url(query)
            
            nav_cmd = [
                PYTHON_CMD, str(SKILL_DIR / "core" / "browser_nav.py"),
                "--port", str(self._port),
                "--tab", tab_id,
                "--goto", search_url,
                "--wait-for", "networkidle",
                "--timeout", str(self.config.wait_timeout),
            ]
            if self.config.stealth:
                nav_cmd.append("--stealth")
            
            nav_result = run_cmd(nav_cmd)
            if nav_result.returncode != 0:
                results.error = f"导航失败: {nav_result.stderr}"
                return results
            
            random_delay(self.config.random_delay_range)
            
            eval_cmd = [
                PYTHON_CMD, str(SKILL_DIR / "core" / "browser_console.py"),
                "--port", str(self._port),
                "--tab", tab_id,
                "--eval", self._extract_results_js(),
            ]
            eval_result = run_cmd(eval_cmd)
            
            if eval_result.returncode == 0:
                try:
                    result_data = json.loads(eval_result.stdout)
                    for item in result_data[:max_results]:
                        search_result = SearchResult(
                            source=self.source_name,
                            title=item.get('title', ''),
                            url=item.get('url', ''),
                            snippet=item.get('snippet', ''),
                            scraped_at=time.strftime('%Y-%m-%dT%H:%M:%S')
                        )
                        results.add(search_result)
                except json.JSONDecodeError:
                    results.error = "结果解析失败"
            else:
                results.error = f"提取失败: {eval_result.stderr}"
                
        except Exception as e:
            results.error = str(e)
            self.process_error(e, {'query': query})
        
        return results
    
    def paginate(self, query: str, page: int = 1, max_pages: int = 3,
                 config: Optional[SearcherConfig] = None) -> SearchResults:
        """分页搜索"""
        cfg = config or self.config
        all_results = []
        
        for p in range(page, page + max_pages):
            page_results = self._search_page(query, p, cfg)
            all_results.extend(page_results)
            
            if len(page_results) < cfg.page_size:
                break
            
            random_delay(self.config.random_delay_range)
        
        return SearchResults(
            source=self.source_name,
            query=query,
            total_results=len(all_results),
            results=all_results,
            metadata={'pages_scraped': len(all_results) // cfg.page_size + 1},
        )
    
    def _search_page(self, query: str, page: int, config: SearcherConfig) -> List[SearchResult]:
        """搜索单页结果"""
        results = SearchResults(source=self.source_name, query=query)
        
        tab_id = self._get_tab_id()
        if not tab_id:
            return []
        
        try:
            search_url = self._build_search_url(query, page)
            
            nav_cmd = [
                PYTHON_CMD, str(SKILL_DIR / "core" / "browser_nav.py"),
                "--port", str(self._port),
                "--tab", tab_id,
                "--goto", search_url,
                "--wait-for", "networkidle",
                "--timeout", str(config.wait_timeout),
            ]
            if config.stealth:
                nav_cmd.append("--stealth")
            
            nav_result = run_cmd(nav_cmd)
            if nav_result.returncode != 0:
                return []
            
            random_delay(config.random_delay_range)
            
            eval_cmd = [
                PYTHON_CMD, str(SKILL_DIR / "core" / "browser_console.py"),
                "--port", str(self._port),
                "--tab", tab_id,
                "--eval", self._extract_results_js(),
            ]
            eval_result = run_cmd(eval_cmd)
            
            if eval_result.returncode == 0:
                try:
                    result_data = json.loads(eval_result.stdout)
                    for item in result_data:
                        search_result = SearchResult(
                            source=self.source_name,
                            title=item.get('title', ''),
                            url=item.get('url', ''),
                            snippet=item.get('snippet', ''),
                            scraped_at=time.strftime('%Y-%m-%dT%H:%M:%S')
                        )
                        results.add(search_result)
                except json.JSONDecodeError:
                    pass
                
        except Exception as e:
            self.process_error(e, {'query': query, 'page': page})
        
        return results.results
    
    def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取详情页内容"""
        cfg = config or self.config
        detail = {'url': url, 'source': self.source_name, 'type': 'web'}
        
        tab_id = self._get_tab_id()
        if not tab_id:
            detail['error'] = "浏览器连接失败"
            return detail
        
        try:
            nav_cmd = [
                PYTHON_CMD, str(SKILL_DIR / "core" / "browser_nav.py"),
                "--port", str(self._port),
                "--tab", tab_id,
                "--goto", url,
                "--wait-for", "networkidle",
                "--timeout", str(cfg.wait_timeout),
            ]
            if cfg.stealth:
                nav_cmd.append("--stealth")
            
            nav_result = run_cmd(nav_cmd)
            if nav_result.returncode != 0:
                detail['error'] = f"导航失败: {nav_result.stderr}"
                return detail
            
            random_delay(cfg.random_delay_range)
            
            extract_cmd = [
                PYTHON_CMD, str(SKILL_DIR / "core" / "browser_extract.py"),
                "--port", str(self._port),
                "--tab", tab_id,
                "--mode", "text",
                "--max-chars", str(cfg.max_chars if hasattr(cfg, 'max_chars') else 5000),
            ]
            extract_result = run_cmd(extract_cmd)
            
            if extract_result.returncode == 0:
                detail['content'] = extract_result.stdout
            
        except Exception as e:
            detail['error'] = str(e)
        
        return detail
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            tab_id = self._get_tab_id()
            if not tab_id:
                return False
            
            nav_cmd = [
                PYTHON_CMD, str(SKILL_DIR / "core" / "browser_nav.py"),
                "--port", str(self._port),
                "--tab", tab_id,
                "--goto", self.base_url,
                "--wait-for", "networkidle",
                "--timeout", "10",
            ]
            result = run_cmd(nav_cmd)
            return result.returncode == 0
        except Exception:
            return False
    
    async def close(self):
        """关闭资源"""
        self._tab_id = None


def search_bing(query: str, max_results: int = 20, **kwargs) -> SearchResults:
    """必应搜索便捷函数"""
    return BingSearcher(**kwargs).search(query, max_results=max_results, **kwargs)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='必应搜索器')
    parser.add_argument('query', help='搜索关键词')
    parser.add_argument('--max-results', type=int, default=20)
    parser.add_argument('--output-dir', default='./search_results')
    parser.add_argument('--port', type=int, default=9333)
    parser.add_argument('--name', default='bing_search')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    
    searcher = BingSearcher(port=args.port, headless=args.headless)
    results = searcher.search(args.query, max_results=args.max_results)
    
    print(f"找到 {len(results.results)} 个结果")
    for i, r in enumerate(results.results[:5], 1):
        print(f"{i}. {r.title}")
        print(f"   {r.url}")
        if r.snippet:
            print(f"   {r.snippet[:100]}...")
