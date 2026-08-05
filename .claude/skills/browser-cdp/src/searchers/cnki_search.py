#!/usr/bin/env python3
"""
中国知网搜索器
反爬较强，仅用于低频查询
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


class CnkSearcher(BaseSearcher):
    """中国知网搜索器 - 高难度"""
    
    @property
    def source_name(self) -> str:
        return "cnki"
    
    async def search(self, query: str, config: SearcherConfig,
                     db: str = "SCIDB") -> List[Dict]:
        """搜索论文（谨慎使用）"""
        try:
            from browser_cdp import Browser
            
            # 必须使用 stealth 模式
            browser = Browser(port=config.port, stealth=True)
            await browser.start()
            
            # 访问搜索页
            search_url = f"https://kns.cnki.net/kns8s/search?kw={query}&db={db}"
            await browser.get(search_url)
            await asyncio.sleep(5)  # 更长等待时间
            
            # 检查是否需要登录
            if "登录" in await browser.get_text():
                print("⚠️ 需要登录，请使用 --dedicated 模式")
                await browser.close()
                return []
            
            # 提取结果
            js_code = """
            (() => {
                const results = [];
                document.querySelectorAll('.tablelist tr, .result-item').forEach((el, i) => {
                    if (i === 0) return; // 跳过表头
                    const cells = el.querySelectorAll('td');
                    if (cells.length >= 3) {
                        const titleEl = cells[0].querySelector('a');
                        const authorsEl = cells[1];
                        const journalEl = cells[2];
                        
                        if (titleEl) {
                            results.push({
                                title: titleEl.innerText.trim(),
                                url: titleEl.href,
                                authors: authorsEl ? authorsEl.innerText.trim() : '',
                                journal: journalEl ? journalEl.innerText.trim() : '',
                                source: 'cnki'
                            });
                        }
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
        """获取论文详情"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=True)
            await browser.start()
            
            await browser.get(url)
            await asyncio.sleep(5)
            
            js_code = """
            (() => {
                const title = document.querySelector('h1, .title')?.innerText || '';
                const authors = document.querySelector('.authors, .author')?.innerText || '';
                const abstract = document.querySelector('.abstract, .summary')?.innerText || '';
                
                return {
                    title: title,
                    authors: authors,
                    abstract: abstract,
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
    parser = argparse.ArgumentParser(description="中国知网搜索（谨慎使用）")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--db", default="SCIDB", choices=["SCIDB", "CPFD", "CCND"], help="数据库")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/cnki", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--dedicated", action="store_true", help="使用专用浏览器实例")
    parser.add_argument("--name", help="浏览器实例名称")
    
    args = parser.parse_args()
    
    async def run():
        searcher = CnkSearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir,
            stealth=True,
            session_name=args.name if args.dedicated else None
        )
        
        print("⚠️  知网反爬较强，建议低频使用")
        print("⚠️  请求间隔将设置为 5-10 秒")
        
        results = await searcher.search(args.query, config, args.db)
        
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "cnki")
            print(f"\n结果已保存到: {args.output_dir}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
