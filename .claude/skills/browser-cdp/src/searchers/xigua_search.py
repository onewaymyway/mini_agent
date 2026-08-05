#!/usr/bin/env python3
"""
西瓜视频搜索器
反爬较弱，适合批量抓取
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import List, Dict
from datetime import datetime
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.searchers.base import BaseSearcher, SearcherConfig
from src.searchers.utils import save_results, print_results


class XiguaSearcher(BaseSearcher):
    """西瓜视频搜索器"""
    
    @property
    def source_name(self) -> str:
        return "xigua"
    
    async def search(self, query: str, config: SearcherConfig) -> List[Dict]:
        """搜索视频"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 访问搜索页
            search_url = f"https://www.ixigua.com/search/{query}/video"
            await browser.get(search_url)
            await asyncio.sleep(3)
            
            # 提取结果
            js_code = """
            (() => {
                const results = [];
                document.querySelectorAll('.video-item, .item, .card').forEach(el => {
                    const titleEl = el.querySelector('.title, h3, .video-title');
                    const linkEl = el.querySelector('a[href*="ixigua"]');
                    const authorEl = el.querySelector('.author, .user-name');
                    const playEl = el.querySelector('.play-count, .views');
                    
                    if (titleEl && linkEl) {
                        results.push({
                            title: titleEl.innerText.trim(),
                            url: linkEl.href,
                            author: authorEl ? authorEl.innerText.trim() : '',
                            play_count: playEl ? playEl.innerText.trim() : '',
                            source: 'xigua'
                        });
                    }
                });
                return results;
            })()
            """
            
            results = await browser.evaluate(js_code)
            await browser.close()
            
            # 去重
            seen_urls = set()
            unique_results = []
            for r in results:
                if r['url'] not in seen_urls:
                    seen_urls.add(r['url'])
                    r['scraped_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    unique_results.append(r)
            
            return unique_results[:config.max_results]
            
        except Exception as e:
            print(f"搜索失败: {e}")
            return []
    
    async def get_detail(self, url: str, config: SearcherConfig) -> Dict:
        """获取视频详情"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            await browser.get(url)
            await asyncio.sleep(3)
            
            js_code = """
            (() => {
                const title = document.querySelector('h1, .video-title')?.innerText || '';
                const author = document.querySelector('.author, .user-name')?.innerText || '';
                const play_count = document.querySelector('.play-count')?.innerText || '';
                const like_count = document.querySelector('.like-count')?.innerText || '';
                
                return {
                    title: title,
                    author: author,
                    play_count: play_count,
                    like_count: like_count,
                    url: url
                };
            })()
            """
            
            result = await browser.evaluate(js_code)
            await browser.close()
            
            return result
            
        except Exception as e:
            print(f"获取详情失败: {e}")
            return {}


def main():
    parser = argparse.ArgumentParser(description="西瓜视频搜索")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/xigua", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    async def run():
        searcher = XiguaSearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir
        )
        
        results = await searcher.search(args.query, config)
        
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "xigua")
            print(f"\n结果已保存到: {args.output_dir}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
