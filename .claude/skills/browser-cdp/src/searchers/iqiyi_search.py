#!/usr/bin/env python3
"""
爱奇艺搜索器 - 影视内容搜索

适配策略：
1. 通过百度搜索 site:iqiyi.com 获取爱奇艺内容
2. 使用 stealth 模式降低检测风险
3. 支持电影/电视剧/综艺搜索
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


class IqiyiSearcher(BaseSearcher):
    """爱奇艺搜索器"""
    
    @property
    def source_name(self) -> str:
        return "iqiyi"
    
    @property
    def supported_types(self) -> List[str]:
        return ["movie", "tv", "variety", "search"]
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.iqiyi.com"
    
    async def search(self, query: str, content_type: str = "all",
                     config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索爱奇艺内容"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 构建搜索 URL
            search_url = f"https://so.iqiyi.com/so/q_{query}"
            
            await browser.get(search_url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await browser.wait_for_network_idle(timeout=config.wait_timeout)
            
            # 提取搜索结果
            results = await self._extract_results(browser, query, content_type)
            
            await browser.close()
            return results
            
        except Exception as e:
            print(f"搜索失败: {e}")
            return []
    
    async def _extract_results(self, browser, query: str, content_type: str) -> List[SearchResult]:
        """提取搜索结果"""
        js_code = f"""
        (() => {{
            const results = [];
            const query = '{query}';
            const type = '{content_type}';
            
            // 爱奇艺搜索结果
            document.querySelectorAll('.mod_item, .mod-item, .list_item, [class*="item"]').forEach(el => {{
                const titleEl = el.querySelector('.title, h3, .text, .mod_item-title');
                const linkEl = el.querySelector('a[href*="iqiyi.com"]');
                const coverEl = el.querySelector('img[data-src], img[src]');
                const descEl = el.querySelector('.desc, .summary, .text');
                
                if (titleEl && linkEl) {{
                    const url = linkEl.href;
                    if (url.includes('iqiyi.com')) {{
                        results.push({{
                            source: 'iqiyi',
                            title: titleEl.innerText.replace(/\\s+/g, ' ').trim(),
                            url: url,
                            snippet: descEl ? descEl.innerText.trim() : '',
                            metadata: {{
                                query: query,
                                type: type,
                                cover: coverEl ? (coverEl.getAttribute('data-src') || coverEl.getAttribute('src')) : ''
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
                    source=r.get('source', 'iqiyi'),
                    title=r.get('title', ''),
                    url=r.get('url', ''),
                    snippet=r.get('snippet', ''),
                    metadata=r.get('metadata', {}),
                    scraped_at=r.get('scraped_at', datetime.now().isoformat())
                ))
        
        return search_results[:config.max_results]
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取视频详情"""
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
                    title: document.querySelector('h1, .video-title, .title')?.innerText || '',
                    description: document.querySelector('.desc, .summary, .intro')?.innerText || '',
                    author: document.querySelector('.author, .up-name, .nick-name')?.innerText || '',
                    play_count: document.querySelector('.play-count, .count')?.innerText || '',
                    publish_time: document.querySelector('.time, .date')?.innerText || '',
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
        description="爱奇艺搜索自动化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python iqiyi_search.py "狂飙" --max-results 10
    python iqiyi_search.py "综艺" --type variety
    python iqiyi_search.py "电影" --type movie --output-dir ./iqiyi_results
"""
    )
    
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数 (默认: 10)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--type", type=str, default="all", choices=["all", "movie", "tv", "variety"], help="内容类型")
    parser.add_argument("--detail", action="store_true", help="获取视频详情")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = IqiyiSearcher(port=args.port, stealth=args.stealth)
    
    # 执行搜索
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    results = loop.run_until_complete(searcher.search(args.query, args.type))
    
    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条内容")
        print_results(results)
        
        if args.output_dir:
            save_results([r.to_dict() for r in results], args.output_dir, f"iqiyi_{args.query.replace(' ', '_')}.json")
    else:
        print("[结果] 未找到内容")


if __name__ == "__main__":
    main()
