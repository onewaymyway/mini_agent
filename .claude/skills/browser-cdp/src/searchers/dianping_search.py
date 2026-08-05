#!/usr/bin/env python3
"""
大众点评搜索器 - 商户/评价搜索

适配策略：
1. 通过百度搜索 site:dianping.com 获取大众点评内容
2. 使用 stealth 模式降低检测风险
3. 支持商户搜索、评价抓取
4. 注意：大众点评有较强反爬，建议使用已登录会话
"""

import asyncio
import json
import sys
import random
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult
from src.searchers.utils import save_results, print_results


class DianpingSearcher(BaseSearcher):
    """大众点评搜索器"""
    
    @property
    def source_name(self) -> str:
        return "dianping"
    
    @property
    def supported_types(self) -> List[str]:
        return ["shop", "review", "search"]
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.dianping.com"
    
    async def search(self, query: str, city: str = "", 
                     config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索大众点评商户"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 构建搜索 URL
            if city:
                search_url = f"https://www.dianping.com/search/keyword/{query}/{city}"
            else:
                search_url = f"https://www.dianping.com/search/keyword/{query}"
            
            await browser.get(search_url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await browser.wait_for_network_idle(timeout=config.wait_timeout)
            
            # 提取搜索结果
            results = await self._extract_results(browser, query, city)
            
            await browser.close()
            return results
            
        except Exception as e:
            print(f"搜索失败: {e}")
            return []
    
    async def _extract_results(self, browser, query: str, city: str) -> List[SearchResult]:
        """提取搜索结果"""
        js_code = f"""
        (() => {{
            const results = [];
            const query = '{query}';
            const city = '{city}';
            
            // 大众点评搜索结果
            document.querySelectorAll('.shop-list-item, .shop-item, [class*="shop"], .result-item').forEach(el => {{
                const titleEl = el.querySelector('.shop-name, .name, h3, .title');
                const linkEl = el.querySelector('a[href*="dianping.com"]');
                const ratingEl = el.querySelector('.rating, .score, .star');
                const addressEl = el.querySelector('.address, .addr, .location');
                const categoryEl = el.querySelector('.category, .type, .tag');
                
                if (titleEl && linkEl) {{
                    const url = linkEl.href;
                    if (url.includes('dianping.com')) {{
                        results.push({{
                            source: 'dianping',
                            title: titleEl.innerText.replace(/\\s+/g, ' ').trim(),
                            url: url,
                            snippet: addressEl ? addressEl.innerText.trim() : '',
                            metadata: {{
                                query: query,
                                city: city,
                                rating: ratingEl ? ratingEl.innerText.trim() : '',
                                address: addressEl ? addressEl.innerText.trim() : '',
                                category: categoryEl ? categoryEl.innerText.trim() : ''
                            }},
                            scraped_at: new Date().toISOString()
                        }});
                    }}
                }}
            }});
            
            return results;
        }})()
        """
        
        raw_results = await browser.evaluate(js_code)
        
        # 转换为 SearchResult 对象
        search_results = []
        seen_urls = set()
        
        for r in raw_results:
            if r.get('url') and r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                search_results.append(SearchResult(
                    source=r.get('source', 'dianping'),
                    title=r.get('title', ''),
                    url=r.get('url', ''),
                    snippet=r.get('snippet', ''),
                    metadata=r.get('metadata', {}),
                    scraped_at=r.get('scraped_at', datetime.now().isoformat())
                ))
        
        return search_results[:config.max_results]
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取商户详情"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            await browser.get(url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await browser.wait_for_network_idle(timeout=config.wait_timeout)
            
            js_code = """
            (() => {
                const result = {
                    title: document.querySelector('h1, .shop-name, .title')?.innerText || '',
                    rating: document.querySelector('.rating, .score, .star')?.innerText || '',
                    address: document.querySelector('.address, .addr')?.innerText || '',
                    phone: document.querySelector('.phone, .tel')?.innerText || '',
                    category: document.querySelector('.category, .type')?.innerText || '',
                    price: document.querySelector('.price, .avg-price')?.innerText || '',
                    url: window.location.href
                };
                return result;
            })()
            """
            
            detail = await browser.evaluate(js_code)
            await browser.close()
            
            return detail
            
        except Exception as e:
            print(f"获取详情失败: {e}")
            return {}


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="大众点评搜索自动化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python dianping_search.py "火锅" --city "北京"
    python dianping_search.py "咖啡店" --city "上海" --max-results 20
    python dianping_search.py "餐厅" --output-dir ./dianping_results
"""
    )
    
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--city", type=str, default="", help="城市名称")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数 (默认: 10)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--detail", action="store_true", help="获取商户详情")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = DianpingSearcher(port=args.port, stealth=args.stealth)
    
    # 执行搜索
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    results = loop.run_until_complete(searcher.search(args.query, args.city))
    
    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条商户")
        print_results(results)
        
        if args.output_dir:
            save_results([r.to_dict() for r in results], args.output_dir, f"dianping_{args.query.replace(' ', '_')}.json")
    else:
        print("[结果] 未找到商户")


if __name__ == "__main__":
    main()
