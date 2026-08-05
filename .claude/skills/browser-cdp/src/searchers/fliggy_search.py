#!/usr/bin/env python3
"""
飞猪酒店搜索器
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


class FliggySearcher(BaseSearcher):
    """飞猪酒店搜索器"""
    
    @property
    def source_name(self) -> str:
        return "fliggy"
    
    async def search(self, city: str, config: SearcherConfig,
                     checkin: str = None, checkout: str = None) -> List[Dict]:
        """搜索酒店"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 构建搜索URL
            base_url = f"https://hotels.fliggy.com/search?city={city}"
            if checkin:
                base_url += f"&checkIn={checkin}"
            if checkout:
                base_url += f"&checkOut={checkout}"
            
            await browser.get(base_url)
            await asyncio.sleep(3)
            
            # 提取结果
            js_code = """
            (() => {
                const results = [];
                document.querySelectorAll('.hotel-item, .list-item').forEach(el => {
                    const nameEl = el.querySelector('.hotel-name, .name');
                    const linkEl = el.querySelector('a[href*="hotel"]');
                    const priceEl = el.querySelector('.price, .fliggy-price');
                    const ratingEl = el.querySelector('.rating, .score');
                    
                    if (nameEl && linkEl) {
                        results.push({
                            name: nameEl.innerText.trim(),
                            url: linkEl.href,
                            price: priceEl ? priceEl.innerText.trim() : '',
                            rating: ratingEl ? ratingEl.innerText.trim() : '',
                            city: city,
                            source: 'fliggy'
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
        """获取酒店详情"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            await browser.get(url)
            await asyncio.sleep(3)
            
            js_code = """
            (() => {
                const title = document.querySelector('h1, .hotel-name')?.innerText || '';
                const price = document.querySelector('.price, .current-price')?.innerText || '';
                
                return {
                    name: title,
                    price: price,
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
    parser = argparse.ArgumentParser(description="飞猪酒店搜索")
    parser.add_argument("city", help="城市名称")
    parser.add_argument("--checkin", help="入住日期 (YYYY-MM-DD)")
    parser.add_argument("--checkout", help="退房日期 (YYYY-MM-DD)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/fliggy", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    async def run():
        searcher = FliggySearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir
        )
        
        results = await searcher.search(args.city, config, args.checkin, args.checkout)
        
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "fliggy")
            print(f"\n结果已保存到: {args.output_dir}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
