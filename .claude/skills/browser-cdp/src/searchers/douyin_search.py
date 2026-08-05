#!/usr/bin/env python3
"""
抖音搜索器
反爬极强，仅用于低频监控
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


class DouyinSearcher(BaseSearcher):
    """抖音搜索器 - 高难度"""
    
    @property
    def source_name(self) -> str:
        return "douyin"
    
    async def search(self, query: str, config: SearcherConfig) -> List[Dict]:
        """搜索视频（谨慎使用）"""
        try:
            from browser_cdp import Browser
            
            # 必须使用 stealth 模式
            browser = Browser(port=config.port, stealth=True)
            await browser.start()
            
            # 访问搜索页
            search_url = f"https://www.douyin.com/search/{query}?type=video"
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
                document.querySelectorAll('.feed-item, .video-item').forEach(el => {
                    const titleEl = el.querySelector('.title, .video-title');
                    const linkEl = el.querySelector('a[href*="video"]');
                    const authorEl = el.querySelector('.author, .user-name');
                    const playEl = el.querySelector('.play-count, .views');
                    const likeEl = el.querySelector('.like-count');
                    
                    if (titleEl && linkEl) {
                        results.push({
                            title: titleEl.innerText.trim(),
                            url: linkEl.href,
                            author: authorEl ? authorEl.innerText.trim() : '',
                            play_count: playEl ? playEl.innerText.trim() : '',
                            like_count: likeEl ? likeEl.innerText.trim() : '',
                            source: 'douyin'
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
            
            browser = Browser(port=config.port, stealth=True)
            await browser.start()
            
            await browser.get(url)
            await asyncio.sleep(5)
            
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
    parser = argparse.ArgumentParser(description="抖音搜索（谨慎使用）")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--max-results", type=int, default=5, help="最大结果数量（建议低频）")
    parser.add_argument("--output-dir", default="./search_results/douyin", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--dedicated", action="store_true", help="使用专用浏览器实例")
    parser.add_argument("--name", help="浏览器实例名称")
    
    args = parser.parse_args()
    
    async def run():
        searcher = DouyinSearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir,
            stealth=True,
            session_name=args.name if args.dedicated else None
        )
        
        print("⚠️  抖音反爬极强，建议低频使用")
        print("⚠️  请求间隔将设置为 10-30 秒")
        
        results = await searcher.search(args.query, config)
        
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "douyin")
            print(f"\n结果已保存到: {args.output_dir}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
