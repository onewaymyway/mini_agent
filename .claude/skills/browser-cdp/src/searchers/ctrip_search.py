#!/usr/bin/env python3
"""
携程搜索器 - 酒店/机票/旅游产品搜索

适配策略：
1. 优先使用用户已登录的浏览器会话（绕过 sign 签名验证）
2. 使用 stealth 模式降低检测风险
3. 添加随机延迟模拟人类行为
4. 支持滑块验证码处理
5. 智能等待策略（networkidle/route/stable）
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


class CtripSearcher(BaseSearcher):
    """携程搜索器"""
    
    @property
    def source_name(self) -> str:
        return "ctrip"
    
    @property
    def supported_types(self) -> List[str]:
        return ["hotel", "flight", "attraction", "travel_guide", "vacation"]
    
    def __init__(self, config: Optional[SearcherConfig] = None, **kwargs):
        # 处理 kwargs 中的配置参数
        if config is None:
            config_kwargs = {}
            if 'port' in kwargs:
                config_kwargs['port'] = kwargs.pop('port')
            if 'stealth' in kwargs:
                config_kwargs['stealth'] = kwargs.pop('stealth')
            if config_kwargs:
                config = SearcherConfig(**config_kwargs)
        super().__init__(config)
        self._session_cookies = {}
    
    async def search(self, query: str, search_type: str = "hotel",
                     config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索携程内容"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 根据搜索类型构建 URL
            if search_type == "hotel":
                url = f"https://hotels.ctrip.com/hotel/{query.lower()}.html"
            elif search_type == "flight":
                url = f"https://flight.ctrip.com/online/oneway-list?depcity={query}"
            elif search_type == "attraction":
                url = f"https://you.ctrip.com/sight/{query.lower()}.html"
            elif search_type == "vacation":
                url = f"https://vacation.ctrip.com/tours/{query.lower()}.html"
            else:
                url = f"https://www.ctrip.com/search?keyword={query}"
            
            await browser.get(url)
            # 添加随机延迟模拟人类行为
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            
            # 智能等待页面加载
            await self._smart_wait(browser, config)
            
            # 提取搜索结果
            results = await self._extract_results(browser, search_type, query)
            
            await browser.close()
            return results
            
        except Exception as e:
            print(f"搜索失败: {e}")
            return []
    
    async def _smart_wait(self, browser, config: SearcherConfig):
        """智能等待策略"""
        strategy = config.wait_strategy
        
        if strategy == "networkidle":
            await browser.wait_for_network_idle(timeout=config.wait_timeout)
        elif strategy == "route":
            await browser.wait_for_route(timeout=config.wait_timeout)
        elif strategy == "stable":
            await browser.wait_for_stable(timeout=config.wait_timeout)
        else:
            await asyncio.sleep(random.uniform(3, 5))
    
    async def _extract_results(self, browser, search_type: str, query: str) -> List[SearchResult]:
        """提取搜索结果"""
        js_code = f"""
        (() => {{
            const results = [];
            const type = '{search_type}';
            
            if (type === 'hotel') {{
                // 酒店搜索结果
                document.querySelectorAll('.hotel-item, .hotel-list-item, [class*="hotel"], .hotel-card').forEach(el => {{
                    const nameEl = el.querySelector('.hotel-name, .name, h3, h4, .hotel-title');
                    const linkEl = el.querySelector('a[href*="hotel"], a[href*="ctrip.com/hotel"]');
                    const priceEl = el.querySelector('.price, .current-price, .sale-price, .price-num');
                    const ratingEl = el.querySelector('.rating, .score, .review-score, .star');
                    const locationEl = el.querySelector('.location, .address, .district');
                    const imgEl = el.querySelector('img[data-src], img[src*="img4.ctrip"]');
                    
                    if (nameEl && linkEl) {{
                        results.push({{
                            source: 'ctrip',
                            title: nameEl.innerText.trim(),
                            url: linkEl.href,
                            snippet: locationEl ? locationEl.innerText.trim() : '',
                            metadata: {{
                                price: priceEl ? priceEl.innerText.trim() : '',
                                rating: ratingEl ? ratingEl.innerText.trim() : '',
                                image: imgEl ? (imgEl.getAttribute('data-src') || imgEl.getAttribute('src')) : '',
                                type: 'hotel'
                            }},
                            scraped_at: new Date().toISOString()
                        }});
                    }}
                }});
            }} else if (type === 'flight') {{
                // 机票搜索结果
                document.querySelectorAll('.flight-item, .flight-list-item, .flight-card, [class*="flight"]').forEach(el => {{
                    const airlineEl = el.querySelector('.airline, .carrier, .flight-airline');
                    const routeEl = el.querySelector('.route, .flight-route, .flight-path');
                    const priceEl = el.querySelector('.price, .fare, .flight-price');
                    const timeEl = el.querySelector('.time, .departure-time, .flight-time');
                    const dateEl = el.querySelector('.date, .flight-date');
                    
                    if (airlineEl && routeEl) {{
                        results.push({{
                            source: 'ctrip',
                            title: `${airlineEl.innerText.trim()} ${routeEl.innerText.trim()}`,
                            url: el.querySelector('a')?.href || '',
                            snippet: timeEl ? timeEl.innerText.trim() : '',
                            metadata: {{
                                price: priceEl ? priceEl.innerText.trim() : '',
                                time: timeEl ? timeEl.innerText.trim() : '',
                                date: dateEl ? dateEl.innerText.trim() : '',
                                type: 'flight'
                            }},
                            scraped_at: new Date().toISOString()
                        }});
                    }}
                }});
            }} else if (type === 'attraction') {{
                // 景点搜索结果
                document.querySelectorAll('.attraction-item, .sight-item, [class*="attraction"], [class*="sight"]').forEach(el => {{
                    const nameEl = el.querySelector('.name, .title, h3, h4, .sight-name');
                    const linkEl = el.querySelector('a[href*="ctrip.com"], a[href*="sight"]');
                    const ratingEl = el.querySelector('.rating, .score, .star');
                    const locationEl = el.querySelector('.location, .address, .district');
                    const imgEl = el.querySelector('img[data-src], img[src*="img4.ctrip"]');
                    
                    if (nameEl && linkEl) {{
                        results.push({{
                            source: 'ctrip',
                            title: nameEl.innerText.trim(),
                            url: linkEl.href,
                            snippet: locationEl ? locationEl.innerText.trim() : '',
                            metadata: {{
                                rating: ratingEl ? ratingEl.innerText.trim() : '',
                                image: imgEl ? (imgEl.getAttribute('data-src') || imgEl.getAttribute('src')) : '',
                                type: 'attraction'
                            }},
                            scraped_at: new Date().toISOString()
                        }});
                    }}
                }});
            }} else {{
                // 通用搜索结果
                document.querySelectorAll('.result-item, .list-item, [class*="result"], [class*="list"]').forEach(el => {{
                    const titleEl = el.querySelector('.title, h3, h4, a');
                    const linkEl = el.querySelector('a[href*="ctrip.com"]');
                    const snippetEl = el.querySelector('.snippet, .desc, .summary, .text');
                    
                    if (titleEl && linkEl) {{
                        results.push({{
                            source: 'ctrip',
                            title: titleEl.innerText.trim(),
                            url: linkEl.href,
                            snippet: snippetEl ? snippetEl.innerText.trim() : '',
                            metadata: {{ type: '{search_type}' }},
                            scraped_at: new Date().toISOString()
                        }});
                    }}
                }});
            }}
            
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
                    source=r.get('source', 'ctrip'),
                    title=r.get('title', ''),
                    url=r.get('url', ''),
                    snippet=r.get('snippet', ''),
                    metadata=r.get('metadata', {}),
                    scraped_at=r.get('scraped_at', datetime.now().isoformat())
                ))
        
        return search_results[:config.max_results]
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取详情页内容"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            await browser.get(url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await self._smart_wait(browser, config)
            
            js_code = """
            (() => {
                const result = {
                    title: document.querySelector('h1, .title, .hotel-name, .flight-title')?.innerText || '',
                    content: document.querySelector('.content, .detail, .description, .info')?.innerText || '',
                    price: document.querySelector('.price, .current-price, .sale-price')?.innerText || '',
                    rating: document.querySelector('.rating, .score, .review-score')?.innerText || '',
                    address: document.querySelector('.address, .location, .addr')?.innerText || '',
                    phone: document.querySelector('.phone, .tel, .contact')?.innerText || '',
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
    
    async def search_hotel(self, city: str, check_in: str = "", 
                           check_out: str = "", config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索酒店（带日期）"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 构建带日期的酒店搜索 URL
            url = f"https://hotels.ctrip.com/hotels/list?destination={city}"
            if check_in:
                url += f"&checkin={check_in}"
            if check_out:
                url += f"&checkout={check_out}"
            
            await browser.get(url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await self._smart_wait(browser, config)
            
            results = await self._extract_results(browser, "hotel", city)
            await browser.close()
            
            return results
            
        except Exception as e:
            print(f"酒店搜索失败: {e}")
            return []
    
    async def search_flight(self, from_city: str, to_city: str, 
                            date: str = "", config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索机票"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 构建机票搜索 URL
            url = f"https://flight.ctrip.com/online/oneway-list?depcity={from_city}&arvcity={to_city}"
            if date:
                url += f"&depdate={date}"
            
            await browser.get(url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await self._smart_wait(browser, config)
            
            results = await self._extract_results(browser, "flight", f"{from_city}-{to_city}")
            await browser.close()
            
            return results
            
        except Exception as e:
            print(f"机票搜索失败: {e}")
            return []
    
    async def handle_captcha(self, browser, config: SearcherConfig) -> bool:
        """处理滑块验证码"""
        try:
            from browser_cdp.captcha_handler import CaptchaHandler
            
            handler = CaptchaHandler(browser)
            return await handler.handle_geetest()
        except Exception as e:
            print(f"验证码处理失败: {e}")
            return False
    
    async def _simulate_human_behavior(self, browser):
        """模拟人类浏览行为"""
        # 随机滚动
        for _ in range(random.randint(2, 5)):
            scroll_amount = random.randint(200, 600)
            await browser.evaluate(f"window.scrollBy(0, {scroll_amount})")
            await asyncio.sleep(random.uniform(0.3, 0.8))
        
        # 随机回滚
        if random.random() > 0.5:
            await browser.evaluate("window.scrollBy(0, -200)")
            await asyncio.sleep(random.uniform(0.2, 0.5))


def main():
    parser = argparse.ArgumentParser(description="携程搜索器")
    parser.add_argument("query", help="搜索关键词（城市名或目的地）")
    parser.add_argument("--type", choices=["hotel", "flight", "attraction", "guide", "vacation"],
                        default="hotel", help="搜索类型")
    parser.add_argument("--check-in", type=str, default="", help="入住日期 (YYYY-MM-DD)")
    parser.add_argument("--check-out", type=str, default="", help="退房日期 (YYYY-MM-DD)")
    parser.add_argument("--from-city", type=str, default="", help="出发城市")
    parser.add_argument("--to-city", type=str, default="", help="到达城市")
    parser.add_argument("--date", type=str, default="", help="出行日期 (YYYY-MM-DD)")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/ctrip", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--port", type=int, default=9333, help="CDP 端口")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    
    args = parser.parse_args()
    
    async def run():
        searcher = CtripSearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir,
            port=args.port,
            stealth=args.stealth
        )
        
        print(f"正在搜索携程 {args.type}... 关键词: {args.query}")
        
        if args.type == "hotel":
            results = await searcher.search_hotel(args.query, args.check_in, args.check_out, config)
        elif args.type == "flight":
            results = await searcher.search_flight(args.from_city or args.query, args.to_city, args.date, config)
        else:
            results = await searcher.search(args.query, args.type, config)
        
        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "ctrip")
            print(f"\n结果已保存到: {args.output_dir}")
        else:
            print("未找到结果，可能需要登录或触发验证码")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
