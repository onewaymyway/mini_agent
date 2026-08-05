#!/usr/bin/env python3
"""
猎聘搜索器
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


class LiepinSearcher(BaseSearcher):
    """猎聘搜索器"""
    
    @property
    def source_name(self) -> str:
        return "liepin"
    
    async def search(self, query: str, config: SearcherConfig,
                     city: str = None, min_salary: int = 0,
                     max_salary: int = 99999) -> List[Dict]:
        """搜索职位"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 构建搜索URL
            search_url = f"https://www.liepin.com/zhaopin/?key={query}&city={city or '北京'}"
            await browser.get(search_url)
            await asyncio.sleep(3)
            
            # 提取结果
            js_code = """
            (() => {
                const results = [];
                document.querySelectorAll('.job-item, .list-item').forEach(el => {
                    const titleEl = el.querySelector('.job-title, .position');
                    const companyEl = el.querySelector('.company-name');
                    const salaryEl = el.querySelector('.salary');
                    const cityEl = el.querySelector('.city');
                    const linkEl = el.querySelector('a[href*="job"]');
                    
                    if (titleEl && linkEl) {
                        results.push({
                            title: titleEl.innerText.trim(),
                            company: companyEl ? companyEl.innerText.trim() : '',
                            salary: salaryEl ? salaryEl.innerText.trim() : '',
                            city: cityEl ? cityEl.innerText.trim() : (city or '北京'),
                            url: linkEl.href,
                            source: 'liepin'
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
        """获取职位详情"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            await browser.get(url)
            await asyncio.sleep(3)
            
            js_code = """
            (() => {
                const title = document.querySelector('h1, .job-title')?.innerText || '';
                const company = document.querySelector('.company-name')?.innerText || '';
                const salary = document.querySelector('.salary')?.innerText || '';
                
                return {
                    title: title,
                    company: company,
                    salary: salary,
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
    parser = argparse.ArgumentParser(description="猎聘搜索")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--city", default="北京", help="城市名称")
    parser.add_argument("--min-salary", type=int, default=0, help="最低薪资")
    parser.add_argument("--max-salary", type=int, default=99999, help="最高薪资")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/liepin", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    async def run():
        searcher = LiepinSearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir
        )
        
        results = await searcher.search(args.query, config, args.city, args.min_salary, args.max_salary)
        
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "liepin")
            print(f"\n结果已保存到: {args.output_dir}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
