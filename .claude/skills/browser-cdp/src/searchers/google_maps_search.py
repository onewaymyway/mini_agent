#!/usr/bin/env python
"""
google_maps_search.py - Google Maps 地点搜索器

使用 browser-cdp skill 搜索 Google Maps 地点信息，支持地点搜索、商家信息、路线查询。

用法:
    python google_maps_search.py --query "restaurants in Tokyo" --max-results 10
    python google_maps_search.py --query "coffee near Central Park" --max-results 20
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


# ========== Google Maps 专用配置 ==========
MAPS_BASE = "https://www.google.com/maps"
MAPS_SEARCH_URL = "https://www.google.com/maps/search/{query}"


class GoogleMapsSearcher(BaseSearcher):
    """Google Maps 地点搜索器"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self._search_type = "query"  # query/place_id
        self._extra_param = ""
    
    @property
    def source_name(self) -> str:
        return "google_maps"
    
    @property
    def supported_types(self) -> List[str]:
        return ["query", "place_id", "nearby"]
    
    @property
    def requires_login(self) -> bool:
        return False
    
    @property
    def rate_limit(self) -> float:
        return 2.0
    
    def search(self, query: str, search_type: str = "query", 
               max_results: int = 10, language: str = "en", 
               port: int = 9333, **kwargs) -> List[Dict]:
        """搜索 Google Maps 地点"""
        encoded_query = quote(query)
        
        if search_type == "query":
            url = MAPS_SEARCH_URL.format(query=encoded_query)
        elif search_type == "place_id":
            url = f"https://www.google.com/maps/place/?q=place_id:{query}"
        elif search_type == "nearby":
            url = f"https://www.google.com/maps/search/nearby/{encoded_query}"
        else:
            url = MAPS_SEARCH_URL.format(query=encoded_query)
        
        js_code = f'''
(function() {{
    var results = [];
    var cards = document.querySelectorAll('[data-item-id], [role="article"], .section-result-item');
    cards.forEach(function(card, index) {{
        if (index >= {max_results}) return;
        var titleEl = card.querySelector('h3, [data-item-title], .section-result-title');
        var addressEl = card.querySelector('[data-item-subtitle], .section-result-address, [data-item-address]');
        var ratingEl = card.querySelector('[data-rating], .section-result-rating');
        var categoryEl = card.querySelector('[data-item-category], .section-result-category');
        
        var result = {{
            title: titleEl ? titleEl.textContent.trim() : '',
            address: addressEl ? addressEl.textContent.trim() : '',
            rating: ratingEl ? ratingEl.textContent.trim() : '',
            category: categoryEl ? categoryEl.textContent.trim() : '',
            url: window.location.href,
            source: 'google_maps'
        }};
        
        if (result.title) {{
            results.push(result);
        }}
    }});
    
    // 也尝试从 URL 参数提取
    var urlParams = new URLSearchParams(window.location.search);
    var q = urlParams.get('q');
    if (q && results.length === 0) {{
        results.push({{
            title: decodeURIComponent(q),
            address: '',
            rating: '',
            category: '',
            url: window.location.href,
            source: 'google_maps'
        }});
    }}
    
    return results;
}})()
        '''
        
        results = self._execute_search(url, js_code, query, **kwargs)
        return results
    
    def get_place_details(self, place_id: str, port: int = 9333, **kwargs) -> Optional[Dict]:
        """获取地点详细信息"""
        url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
        
        js_code = '''
(function() {
    var result = {
        name: '',
        address: '',
        rating: '',
        reviews: '',
        hours: '',
        phone: '',
        website: '',
        category: '',
        coordinates: '',
        source: 'google_maps'
    };
    
    // 提取名称
    var nameEl = document.querySelector('h1, [data-item-title], .section-result-title');
    if (nameEl) result.name = nameEl.textContent.trim();
    
    // 提取地址
    var addrEl = document.querySelector('[data-item-address], .section-result-address');
    if (addrEl) result.address = addrEl.textContent.trim();
    
    // 提取评分
    var ratingEl = document.querySelector('[data-rating], .section-result-rating');
    if (ratingEl) result.rating = ratingEl.textContent.trim();
    
    // 提取营业时间
    var hoursEl = document.querySelector('[data-hours], .section-result-hours');
    if (hoursEl) result.hours = hoursEl.textContent.trim();
    
    // 提取电话
    var phoneEl = document.querySelector('[data-phone], .section-result-phone');
    if (phoneEl) result.phone = phoneEl.textContent.trim();
    
    // 提取网站
    var websiteEl = document.querySelector('a[href*="website"], [data-website]');
    if (websiteEl) result.website = websiteEl.href || websiteEl.textContent.trim();
    
    return result;
})()
        '''
        
        try:
            response = run_cmd('navigate', url=url, port=port)
            time.sleep(3)
            data = run_cmd('evaluate', js=js_code, port=port)
            if data and 'result' in data:
                return data['result']
        except Exception as e:
            self.logger.error(f"Failed to get place details: {e}")
        
        return None
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            import requests
            resp = requests.get("https://www.google.com/maps", timeout=10)
            return resp.status_code == 200
        except Exception:
            return False
    
    def _execute_search(self, url: str, js_code: str, query: str, **kwargs) -> List[Dict]:
        """执行搜索"""
        results = []
        
        try:
            # 导航到搜索页面
            response = run_cmd('navigate', url=url, port=kwargs.get('port', 9333))
            
            # 等待页面加载
            time.sleep(random.uniform(2, 4))
            
            # 执行 JavaScript 提取数据
            data = run_cmd('evaluate', js=js_code, port=kwargs.get('port', 9333))
            
            if data and 'result' in data:
                results = data['result']
                if isinstance(results, list):
                    for r in results:
                        r['query'] = query
                        r['source'] = 'google_maps'
                elif isinstance(results, dict):
                    results = [results]
                    for r in results:
                        r['query'] = query
                        r['source'] = 'google_maps'
        except Exception as e:
            self.logger.error(f"Google Maps search failed: {e}")
            results = [{
                'title': f"Search failed: {query}",
                'address': str(e),
                'rating': '',
                'category': '',
                'url': url,
                'source': 'google_maps',
                'query': query
            }]
        
        return results


# ========== 命令行接口 ==========
def main():
    parser = argparse.ArgumentParser(description='Google Maps 地点搜索器')
    parser.add_argument('--query', '-q', required=True, help='搜索关键词')
    parser.add_argument('--type', '-t', default='query', 
                       choices=['query', 'place_id', 'nearby'],
                       help='搜索类型')
    parser.add_argument('--max-results', '-n', type=int, default=10,
                       help='最大结果数')
    parser.add_argument('--output', '-o', help='输出文件路径')
    parser.add_argument('--port', '-p', type=int, default=9333, help='浏览器端口')
    
    args = parser.parse_args()
    
    searcher = GoogleMapsSearcher()
    results = searcher.search(
        query=args.query,
        search_type=args.type,
        max_results=args.max_results,
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
