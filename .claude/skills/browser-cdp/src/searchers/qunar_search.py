#!/usr/bin/env python3
"""
去哪儿酒店搜索器
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


class QunarSearcher(BaseSearcher):
    """去哪儿酒店搜索器"""
    
    @property
    def source_name(self) -> str:
        return "qunar"
    
    async def search(self, city: str, config: SearcherConfig,
                     checkin: str = None, checkout: str = None) -> List[Dict]:
        """搜索酒店"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 构建搜索URL
            base_url = f"https://hotels.qunar.com/?destination={city}"
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
                document.querySelectorAll('.hotel-item, .list-item, .result-item').forEach(el => {
                    const nameEl = el.querySelector('.hotel-name, .name, h3');
                    const linkEl = el.querySelector('a[href*="hotel"]');
                    const priceEl = el.querySelector('.price, .j_price');
                    const ratingEl = el.querySelector('.rating, .score');
                    const addressEl = el.querySelector('.address, .location');
                    
                    if (nameEl && linkEl) {
                        results.push({
                            name: nameEl.innerText.trim(),
                            url: linkEl.href,
                            price: priceEl ? priceEl.innerText.trim() : '',
                            rating: ratingEl ? ratingEl.innerText.trim() : '',
                            address: addressEl ? addressEl.innerText.trim() : '',
                            city: city,
                            source: 'qunar'
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
                const rating = document.querySelector('.rating, .score')?.innerText || '';
                const address = document.querySelector('.address, .location')?.innerText || '';
                
                return {
                    name: title,
                    price: price,
                    rating: rating,
                    address: address,
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
    parser = argparse.ArgumentParser(description="去哪儿酒店搜索")
    parser.add_argument("city", help="城市名称")
    parser.add_argument("--checkin", help="入住日期 (YYYY-MM-DD)")
    parser.add_argument("--checkout", help="退房日期 (YYYY-MM-DD)")
    parser.add_argument("--min-price", type=int, default=0, help="最低价格")
    parser.add_argument("--max-price", type=int, default=9999, help="最高价格")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/qunar", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    async def run():
        searcher = QunarSearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir
        )
        
        results = await searcher.search(args.city, config, args.checkin, args.checkout)
        
        # 价格过滤
        if args.min_price > 0 or args.max_price < 9999:
            filtered = []
            for r in results:
                price_str = r.get('price', '').replace('¥', '').replace(',', '').strip()
                try:
                    price = int(price_str)
                    if args.min_price <= price <= args.max_price:
                        filtered.append(r)
                except:
                    filtered.append(r)
            results = filtered
        
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "qunar")
            print(f"\n结果已保存到: {args.output_dir}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
