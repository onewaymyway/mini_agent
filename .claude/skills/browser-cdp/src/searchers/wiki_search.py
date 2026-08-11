#!/usr/bin/env python
"""
wiki_search.py - Wikipedia 搜索器

使用 browser-cdp skill 搜索 Wikipedia 条目，支持多语言搜索和页面内容提取。

用法:
    python wiki_search.py --query "Artificial Intelligence" --lang en --max-results 10
    python wiki_search.py --query "人工智能" --lang zh --max-results 5
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


# ========== Wikipedia 专用配置 ==========
WIKI_BASE_URLS = {
    "en": "https://en.wikipedia.org",
    "zh": "https://zh.wikipedia.org",
    "ja": "https://ja.wikipedia.org",
    "de": "https://de.wikipedia.org",
    "fr": "https://fr.wikipedia.org",
    "es": "https://es.wikipedia.org",
}
WIKI_SEARCH_URL = "https://{lang}.wikipedia.org/w/index.php?search={query}&title=Special:Search&go=Go"


class WikipediaSearcher(BaseSearcher):
    """Wikipedia 搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "query"
        self._extra_param = ""
        self._lang = "en"
    
    @property
    def source_name(self) -> str:
        return "wikipedia"
    
    @property
    def supported_types(self) -> List[str]:
        return ["query", "title", "fulltext"]
    
    @property
    def requires_login(self) -> bool:
        return False
    
    @property
    def rate_limit(self) -> float:
        return 1.0
    
    def search(self, query: str, search_type: str = "query", 
               max_results: int = 10, language: str = "en",
               port: int = 9333, **kwargs) -> List[Dict]:
        """搜索 Wikipedia 条目"""
        self._lang = language
        lang_url = WIKI_BASE_URLS.get(language, WIKI_BASE_URLS["en"])
        encoded_query = quote(query)
        url = WIKI_SEARCH_URL.format(lang=language, query=encoded_query)
        
        js_code = f'''
(function() {{
    var results = [];
    var searchResults = document.querySelectorAll('.searchresult, .mw-search-result');
    
    searchResults.forEach(function(item, index) {{
        if (index >= {max_results}) return;
        
        var titleEl = item.querySelector('a, .searchresult-title, .mw-search-result-heading a');
        var snippetEl = item.querySelector('.searchresult-snippet, .mw-search-result-text');
        var urlEl = item.querySelector('a');
        
        var result = {{
            title: titleEl ? titleEl.textContent.trim() : '',
            snippet: snippetEl ? snippetEl.textContent.trim() : '',
            url: urlEl ? urlEl.href : '',
            language: '{language}',
            source: 'wikipedia'
        }};
        
        if (result.title) {{
            results.push(result);
        }}
    }});
    
    // 备用选择器
    if (results.length === 0) {{
        var allLinks = document.querySelectorAll('a[href*="/wiki/"]');
        var seen = new Set();
        allLinks.forEach(function(link) {{
            var href = link.href;
            if (href.includes('/wiki/') && !href.includes(':') && !seen.has(href)) {{
                seen.add(href);
                var title = link.textContent.trim();
                if (title && results.length < {max_results}) {{
                    results.push({{
                        title: title,
                        snippet: '',
                        url: href,
                        language: '{language}',
                        source: 'wikipedia'
                    }});
                }}
            }}
        }});
    }}
    
    return results;
}})()
        '''
        
        results = self._execute_search(url, js_code, query, language, **kwargs)
        return results
    
    def get_page_content(self, title: str, language: str = "en", 
                         port: int = 9333, **kwargs) -> Optional[Dict]:
        """获取页面完整内容"""
        url = f"https://{language}.wikipedia.org/wiki/{quote(title)}"
        
        js_code = '''
(function() {
    var result = {
        title: '',
        content: '',
        sections: [],
        categories: [],
        source: 'wikipedia'
    };
    
    // 获取页面标题
    var titleEl = document.querySelector('h1#firstHeading, .firstHeading');
    if (titleEl) result.title = titleEl.textContent.trim();
    
    // 获取主要内容
    var contentEl = document.querySelector('#content, #mw-content-text, .mw-parser-output');
    if (contentEl) {
        // 移除导航模板和分类
        var navTemplates = contentEl.querySelectorAll('.navbox, .metadata, .ambox, .cmbox, .tmbox, .fmbox');
        navTemplates.forEach(function(el) { el.remove(); });
        
        // 提取纯文本内容（限制长度）
        var text = contentEl.innerText || contentEl.textContent;
        result.content = text.substring(0, 5000);
    }
    
    // 提取章节
    var headings = document.querySelectorAll('h2, h3');
    headings.forEach(function(h) {
        result.sections.push(h.textContent.trim());
    });
    
    // 提取分类
    var catEl = document.querySelector('#categories, .catlinks');
    if (catEl) {
        var links = catEl.querySelectorAll('a');
        links.forEach(function(link) {
            result.categories.push(link.textContent.trim());
        });
    }
    
    return result;
})()
        '''
        
        try:
            response = run_cmd('navigate', url=url, port=port)
            time.sleep(random.uniform(2, 3))
            data = run_cmd('evaluate', js=js_code, port=port)
            if data and 'result' in data:
                return data['result']
        except Exception as e:
            self.logger.error(f"Failed to get page content: {e}")
        
        return None
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            import requests
            resp = requests.get("https://en.wikipedia.org", timeout=10)
            return resp.status_code == 200
        except Exception:
            return False
    
    def _execute_search(self, url: str, js_code: str, query: str, 
                        language: str, **kwargs) -> List[Dict]:
        """执行搜索"""
        results = []
        
        try:
            # 导航到搜索页面
            response = run_cmd('navigate', url=url, port=kwargs.get('port', 9333))
            
            # 等待页面加载
            time.sleep(random.uniform(2, 3))
            
            # 执行 JavaScript 提取数据
            data = run_cmd('evaluate', js=js_code, port=kwargs.get('port', 9333))
            
            if data and 'result' in data:
                results = data['result']
                if isinstance(results, list):
                    for r in results:
                        r['query'] = query
                        r['language'] = language
                        r['source'] = 'wikipedia'
                elif isinstance(results, dict):
                    results = [results]
                    for r in results:
                        r['query'] = query
                        r['language'] = language
                        r['source'] = 'wikipedia'
        except Exception as e:
            self.logger.error(f"Wikipedia search failed: {e}")
            results = [{
                'title': f"Search failed: {query}",
                'snippet': str(e),
                'url': url,
                'language': language,
                'source': 'wikipedia',
                'query': query
            }]
        
        return results


# ========== 命令行接口 ==========
def main():
    parser = argparse.ArgumentParser(description='Wikipedia 搜索器')
    parser.add_argument('--query', '-q', required=True, help='搜索关键词')
    parser.add_argument('--lang', '-l', default='en', 
                       choices=list(WIKI_BASE_URLS.keys()),
                       help='语言代码 (默认: en)')
    parser.add_argument('--type', '-t', default='query', 
                       choices=['query', 'title', 'fulltext'],
                       help='搜索类型')
    parser.add_argument('--max-results', '-n', type=int, default=10,
                       help='最大结果数')
    parser.add_argument('--output', '-o', help='输出文件路径')
    parser.add_argument('--port', '-p', type=int, default=9333, help='浏览器端口')
    parser.add_argument('--get-content', '-c', action='store_true',
                       help='获取完整页面内容')
    
    args = parser.parse_args()
    
    searcher = WikipediaSearcher()
    
    if args.get_content:
        # 先搜索获取标题，再获取内容
        results = searcher.search(
            query=args.query,
            search_type=args.type,
            max_results=1,
            language=args.lang,
            port=args.port
        )
        if results:
            content = searcher.get_page_content(
                title=results[0]['title'],
                language=args.lang,
                port=args.port
            )
            if content:
                output = {**results[0], 'content': content}
                if args.output:
                    save_results([output], args.output)
                    print(f"Content saved to {args.output}")
                else:
                    print(json.dumps(output, indent=2, ensure_ascii=False))
        return
    
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
        language=args.lang,
        port=args.port
    )
    
    if args.output:
        save_results(results, args.output)
        print(f"Results saved to {args.output}")
    else:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    
    print(f"\nFound {len(results)} results")


if __name__ == "__main__":
    main()
