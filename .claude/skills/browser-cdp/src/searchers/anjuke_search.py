#!/usr/bin/env python3
"""
安居客房产搜索器
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


class AnjukeSearcher(BaseSearcher):
    """安居客房产搜索器"""
    
    @property
    def source_name(self) -> str:
        return "anjuke"
    
    @property
    def supported_types(self) -> List[str]:
        return ["xiaoqu", "house"]
    
    async def search(self, city: str, config: SearcherConfig, 
                     search_type: str = "xiaoqu") -> List[Dict]:
        """搜索房产"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 根据类型选择搜索页
            if search_type == "xiaoqu":
                search_url = f"https://{city}.anjuke.com/xiaoqu/"
            else:
                search_url = f"https://{city}.anjuke.com/sale/"
            
            await browser.get(search_url)
            await asyncio.sleep(3)
            
            # 提取结果
            js_code = """
            (() => {
                const results = [];
                document.querySelectorAll('.list-item, .house-item, .xiaoqu-item').forEach(el => {
                    const titleEl = el.querySelector('h2, h3, .title, .name');
                    const linkEl = el.querySelector('a[href*="anjuke"]');
                    const priceEl = el.querySelector('.price, .avg-price, .j_price');
                    const districtEl = el.querySelector('.district, .area');
                    
                    if (titleEl && linkEl) {
                        results.push({
                            name: titleEl.innerText.trim(),
                            url: linkEl.href,
                            price: priceEl ? priceEl.innerText.trim() : '',
                            district: districtEl ? districtEl.innerText.trim() : '',
                            city: city,
                            source: 'anjuke'
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
        """获取详情"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            await browser.get(url)
            await asyncio.sleep(3)
            
            js_code = """
            (() => {
                const title = document.querySelector('h1, .title')?.innerText || '';
                const price = document.querySelector('.price, .avg-price')?.innerText || '';
                const info = document.querySelector('.info, .detail')?.innerText || '';
                
                return {
                    title: title,
                    price: price,
                    info: info,
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
    parser = argparse.ArgumentParser(description="安居客房产搜索")
    parser.add_argument("city", help="城市名称")
    parser.add_argument("--type", choices=["xiaoqu", "house"], default="xiaoqu", help="搜索类型")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/anjuke", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    async def run():
        searcher = AnjukeSearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir
        )
        
        results = await searcher.search(args.city, config, args.type)
        
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "anjuke")
            print(f"\n结果已保存到: {args.output_dir}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
