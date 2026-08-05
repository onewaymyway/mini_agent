#!/usr/bin/env python3
"""
携程搜索器 - 酒店/机票/旅游产品搜索

适配策略：
1. 优先使用用户已登录的浏览器会话（绕过 sign 签名验证）
2. 使用 stealth 模式降低检测风险
3. 添加随机延迟模拟人类行为
4. 支持滑块验证码处理
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
        return ["hotel", "flight", "attraction", "travel_guide"]
    
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
            else:
                url = f"https://www.ctrip.com/search?keyword={query}"
            
            await browser.get(url)
            # 添加随机延迟模拟人类行为
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            
            # 等待页面加载完成
            await browser.wait_for_network_idle(timeout=config.wait_timeout)
            
            # 提取搜索结果
            results = await self._extract_results(browser, search_type, query)
            
            await browser.close()
            return results
            
        except Exception as e:
            print(f"搜索失败: {e}")
            return []
    
    async def _extract_results(self, browser, search_type: str, query: str) -> List[SearchResult]:
        """提取搜索结果"""
        js_code = f"""
        (() => {{
            const results = [];
            const type = '{search_type}';
            
            if (type === 'hotel') {{
                // 酒店搜索结果
                document.querySelectorAll('.hotel-item, .hotel-list-item, [class*="hotel"]').forEach(el => {{
                    const nameEl = el.querySelector('.hotel-name, .name, h3, h4');
                    const linkEl = el.querySelector('a[href*="hotel"]');
                    const priceEl = el.querySelector('.price, .current-price, .sale-price');
                    const ratingEl = el.querySelector('.rating, .score, .review-score');
                    const locationEl = el.querySelector('.location, .address');
                    
                    if (nameEl && linkEl) {{
                        results.push({{
                            source: 'ctrip',
                            title: nameEl.innerText.trim(),
                            url: linkEl.href,
                            snippet: locationEl ? locationEl.innerText.trim() : '',
                            metadata: {{
                                price: priceEl ? priceEl.innerText.trim() : '',
                                rating: ratingEl ? ratingEl.innerText.trim() : '',
                                type: 'hotel'
                            }},
                            scraped_at: new Date().toISOString()
                        }});
                    }}
                }});
            }} else if (type === 'flight') {{
                // 机票搜索结果
                document.querySelectorAll('.flight-item, .flight-list-item').forEach(el => {{
                    const airlineEl = el.querySelector('.airline, .carrier');
                    const routeEl = el.querySelector('.route, .flight-route');
                    const priceEl = el.querySelector('.price, .fare');
                    const timeEl = el.querySelector('.time, .departure-time');
                    
                    if (airlineEl && routeEl) {{
                        results.push({{
                            source: 'ctrip',
                            title: `${airlineEl.innerText.trim()} ${routeEl.innerText.trim()}`,
                            url: el.querySelector('a')?.href || '',
                            snippet: timeEl ? timeEl.innerText.trim() : '',
                            metadata: {{
                                price: priceEl ? priceEl.innerText.trim() : '',
                                type: 'flight'
                            }},
                            scraped_at: new Date().toISOString()
                        }});
                    }}
                }});
            }} else {{
                // 通用搜索结果
                document.querySelectorAll('.result-item, .list-item, [class*="result"]').forEach(el => {{
                    const titleEl = el.querySelector('.title, h3, h4, a');
                    const linkEl = el.querySelector('a[href]');
                    const snippetEl = el.querySelector('.snippet, .desc, .summary');
                    
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
            await browser.wait_for_network_idle(timeout=config.wait_timeout)
            
            js_code = """
            (() => {
                const result = {
                    title: document.querySelector('h1, .title, .hotel-name')?.innerText || '',
                    content: document.querySelector('.content, .detail, .description')?.innerText || '',
                    price: document.querySelector('.price, .current-price')?.innerText || '',
                    rating: document.querySelector('.rating, .score')?.innerText || '',
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
    
    async def handle_captcha(self, browser, config: SearcherConfig) -> bool:
        """处理滑块验证码"""
        try:
            from browser_cdp.captcha_handler import CaptchaHandler
            
            handler = CaptchaHandler(browser)
            return await handler.handle_geetest()
        except Exception as e:
            print(f"验证码处理失败: {e}")
            return False


def main():
    parser = argparse.ArgumentParser(description="携程搜索器")
    parser.add_argument("query", help="搜索关键词（城市名或目的地）")
    parser.add_argument("--type", choices=["hotel", "flight", "attraction", "guide"],
                        default="hotel", help="搜索类型")
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
