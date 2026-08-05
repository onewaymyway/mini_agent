#!/usr/bin/env python3
"""
今日头条搜索器 - 新闻/文章搜索

适配策略：
1. 通过百度搜索 site:so.html5.qq.com 或 site:toutiao.com 获取头条文章
2. 使用 stealth 模式降低检测风险
3. 支持关键词搜索和热榜抓取
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


class ToutiaoSearcher(BaseSearcher):
    """今日头条搜索器"""
    
    @property
    def source_name(self) -> str:
        return "toutiao"
    
    @property
    def supported_types(self) -> List[str]:
        return ["news", "article", "hot"]
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = "https://www.toutiao.com"
        self.search_base = "https://www.google.com/search?q=site:toutiao.com+{}"
    
    async def search(self, query: str, search_type: str = "news",
                     config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索今日头条内容"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 构建搜索 URL（通过 Google 搜索 site:toutiao.com）
            search_url = f"https://www.google.com/search?q=site:toutiao.com+{query}"
            
            await browser.get(search_url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await browser.wait_for_network_idle(timeout=config.wait_timeout)
            
            # 提取搜索结果
            results = await self._extract_results(browser, query)
            
            await browser.close()
            return results
            
        except Exception as e:
            print(f"搜索失败: {e}")
            return []
    
    async def _extract_results(self, browser, query: str) -> List[SearchResult]:
        """提取搜索结果"""
        js_code = f"""
        (() => {{
            const results = [];
            const query = '{query}';
            
            // Google 搜索结果
            document.querySelectorAll('.g, [data-sncf], .MjjYud').forEach(el => {{
                const titleEl = el.querySelector('h3, .r, [data-attrindex]');
                const linkEl = el.querySelector('a[href]');
                const snippetEl = el.querySelector('.st, [data-sncf], .aCOpRe');
                
                if (titleEl && linkEl) {{
                    const url = linkEl.href;
                    // 过滤出 toutiao.com 的链接
                    if (url.includes('toutiao.com') || url.includes('so.html5.qq.com')) {{
                        results.push({{
                            source: 'toutiao',
                            title: titleEl.innerText.replace(/\\s+/g, ' ').trim(),
                            url: url,
                            snippet: snippetEl ? snippetEl.innerText.trim() : '',
                            metadata: {{
                                query: query,
                                type: 'news'
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
                    source=r.get('source', 'toutiao'),
                    title=r.get('title', ''),
                    url=r.get('url', ''),
                    snippet=r.get('snippet', ''),
                    metadata=r.get('metadata', {}),
                    scraped_at=r.get('scraped_at', datetime.now().isoformat())
                ))
        
        return search_results[:config.max_results]
    
    async def get_hot(self, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """获取今日头条热榜"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 访问热榜页面
            hot_url = f"{self.base_url}/hot"
            await browser.get(hot_url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await browser.wait_for_network_idle(timeout=config.wait_timeout)
            
            js_code = """
            (() => {
                const results = [];
                
                // 热榜条目
                document.querySelectorAll('.feed-item, .hot-item, [class*="feed"]').forEach((el, index) => {
                    const titleEl = el.querySelector('.title, h3, .feed-title');
                    const linkEl = el.querySelector('a[href]');
                    
                    if (titleEl && linkEl) {
                        results.push({
                            source: 'toutiao',
                            title: titleEl.innerText.trim(),
                            url: linkEl.href,
                            snippet: '',
                            metadata: {
                                rank: index + 1,
                                type: 'hot'
                            },
                            scraped_at: new Date().toISOString()
                        });
                    }
                });
                
                return results;
            })()
            """
            
            raw_results = await browser.evaluate(js_code)
            await browser.close()
            
            # 转换为 SearchResult 对象
            search_results = []
            seen_urls = set()
            
            for r in raw_results:
                if r.get('url') and r['url'] not in seen_urls:
                    seen_urls.add(r['url'])
                    search_results.append(SearchResult(
                        source=r.get('source', 'toutiao'),
                        title=r.get('title', ''),
                        url=r.get('url', ''),
                        snippet=r.get('snippet', ''),
                        metadata=r.get('metadata', {}),
                        scraped_at=r.get('scraped_at', datetime.now().isoformat())
                    ))
            
            return search_results[:config.max_results]
            
        except Exception as e:
            print(f"获取热榜失败: {e}")
            return []
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取文章详情"""
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
                    title: document.querySelector('h1, .article-title, .title')?.innerText || '',
                    content: document.querySelector('.article-content, .content, #js_article')?.innerText || '',
                    author: document.querySelector('.author, .source, .nickname')?.innerText || '',
                    publish_time: document.querySelector('.publish-time, .time, [class*="time"]')?.innerText || '',
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
        description="今日头条搜索自动化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python toutiao_search.py "AI 新闻" --max-results 10
    python toutiao_search.py "科技" --hot
    python toutiao_search.py "经济" --output-dir ./toutiao_results
"""
    )
    
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数 (默认: 10)")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口 (默认: 9333)")
    parser.add_argument("--hot", action="store_true", help="获取热榜")
    parser.add_argument("--detail", action="store_true", help="获取文章详情")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    parser.add_argument("--no-stealth", action="store_false", dest="stealth", help="禁用反检测模式")
    
    args = parser.parse_args()
    
    # 创建搜索器
    searcher = ToutiaoSearcher(port=args.port, stealth=args.stealth)
    
    # 执行搜索
    if args.hot:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(searcher.get_hot())
    else:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(searcher.search(args.query))
    
    # 输出结果
    if results:
        print(f"\n[结果] 共找到 {len(results)} 条内容")
        print_results(results)
        
        if args.output_dir:
            save_results([r.to_dict() for r in results], args.output_dir, f"toutiao_{args.query.replace(' ', '_')}.json")
    else:
        print("[结果] 未找到内容")


if __name__ == "__main__":
    main()
