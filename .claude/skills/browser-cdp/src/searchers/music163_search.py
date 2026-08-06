#!/usr/bin/env python3
"""
网易云音乐搜索器
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


class Music163Searcher(BaseSearcher):
    """网易云音乐搜索器"""
    
    @property
    def source_name(self) -> str:
        return "music163"

    @property
    def supported_types(self) -> List[str]:
        return ["song", "artist", "album", "playlist"]

    async def search(self, query: str, config: SearcherConfig) -> List[Dict]:
        """搜索音乐"""
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 访问搜索页
            search_url = f"https://music.163.com/search?type=1&s={query}"
            await browser.get(search_url)
            await asyncio.sleep(3)
            
            # 提取结果
            js_code = """
            (() => {
                const results = [];
                document.querySelectorAll('.fm-item, .item, li[data-type="song"]').forEach(el => {
                    const titleEl = el.querySelector('.name, .f-name');
                    const artistEl = el.querySelector('.s-fc3, .artist');
                    const albumEl = el.querySelector('.sub, .album');
                    const durationEl = el.querySelector('.dur');
                    const linkEl = el.querySelector('a');
                    
                    if (titleEl && linkEl) {
                        results.push({
                            title: titleEl.innerText.trim(),
                            artist: artistEl ? artistEl.innerText.trim() : '',
                            album: albumEl ? albumEl.innerText.trim() : '',
                            duration: durationEl ? durationEl.innerText.trim() : '',
                            url: linkEl.href,
                            source: 'music163'
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
        """获取歌曲详情"""
        try:
            from browser_cdp import Browser

            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()

            await browser.get(url)
            await asyncio.sleep(3)

            js_code = """
            (() => {
                const title = document.querySelector('h1, .title')?.innerText || '';
                const artist = document.querySelector('.artist, .s-fc3')?.innerText || '';
                const album = document.querySelector('.album, .sub')?.innerText || '';
                const duration = document.querySelector('.dur')?.innerText || '';

                return {
                    title: title,
                    artist: artist,
                    album: album,
                    duration: duration,
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

    async def _smart_wait(self, browser, config: SearcherConfig):
        """智能等待页面加载"""
        from src.core.smart_wait import SmartWait
        wait_handler = SmartWait(browser.session)
        await wait_handler.wait_for(config.wait_strategy, idle_timeout=config.wait_timeout)

    async def _extract_results(self, browser, query: str) -> List[Dict]:
        """提取搜索结果"""
        js_code = """
        (() => {
            const results = [];
            document.querySelectorAll('.fm-item, .item, li[data-type="song"]').forEach(el => {
                const titleEl = el.querySelector('.name, .f-name');
                const artistEl = el.querySelector('.s-fc3, .artist');
                const albumEl = el.querySelector('.sub, .album');
                const durationEl = el.querySelector('.dur');
                const linkEl = el.querySelector('a');

                if (titleEl && linkEl) {
                    results.push({
                        title: titleEl.innerText.trim(),
                        artist: artistEl ? artistEl.innerText.trim() : '',
                        album: albumEl ? albumEl.innerText.trim() : '',
                        duration: durationEl ? durationEl.innerText.trim() : '',
                        url: linkEl.href,
                        source: 'music163'
                    });
                }
            });
            return results;
        })()
        """
        return await browser.evaluate(js_code)


def main():
    parser = argparse.ArgumentParser(description="网易云音乐搜索")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", choices=["song", "artist", "album"], default="song", help="搜索类型")
    parser.add_argument("--max-results", type=int, default=10, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/music163", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    async def run():
        searcher = Music163Searcher()
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
            save_results(results, args.output_dir, "music163")
            print(f"\n结果已保存到: {args.output_dir}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
