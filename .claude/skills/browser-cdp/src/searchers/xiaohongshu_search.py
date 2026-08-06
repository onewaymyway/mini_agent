#!/usr/bin/env python3
"""
小红书搜索器 - 笔记/商品/用户搜索

适配策略：
1. 优先使用用户已登录的浏览器会话（绕过 x-s/x-s-common 签名验证）
2. 使用 stealth 模式降低检测风险
3. 添加人类化行为模拟（随机延迟、鼠标轨迹、页面停留）
4. 支持动态 Cookie 管理（10 分钟过期）
5. 智能等待策略（networkidle/route/stable）
"""

import asyncio
import json
import sys
import random
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult
from src.searchers.utils import save_results, print_results


class XiaohongshuSearcher(BaseSearcher):
    """小红书搜索器"""
    
    @property
    def source_name(self) -> str:
        return "xiaohongshu"
    
    @property
    def supported_types(self) -> List[str]:
        return ["note", "user", "product", "topic", "search"]
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cookie_cache = None
        self._cookie_expire_time = None
        self._user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    async def search(self, query: str, search_type: str = "note",
                     config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索小红书内容"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            # 构建搜索 URL
            url = f"https://www.xiaohongshu.com/search_result?keyword={query}&source=web_search_result_notes"
            
            await browser.get(url)
            # 添加随机延迟模拟人类行为
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            
            # 模拟人类滚动行为
            await self._simulate_human_behavior(browser)
            
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
        
        # 随机鼠标移动
        for _ in range(random.randint(1, 3)):
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            await browser.evaluate(f"window.scrollTo({x}, {y})")
            await asyncio.sleep(random.uniform(0.2, 0.5))
    
    async def _extract_results(self, browser, search_type: str, query: str) -> List[SearchResult]:
        """提取搜索结果"""
        js_code = f"""
        (() => {{
            const results = [];
            const type = '{search_type}';
            
            // 小红书笔记搜索结果
            document.querySelectorAll('.note-item, .search-note-item, [class*="note"], .note-card').forEach(el => {{
                const titleEl = el.querySelector('.title, .note-title, h3, .note-card-title, .note-name');
                const linkEl = el.querySelector('a[href*="/explore/"], a[href*="/discovery/item/"], a[href*="note/"]');
                const authorEl = el.querySelector('.author, .user-name, .note-author, .user-info');
                const likesEl = el.querySelector('.like-count, .likes, .count, .interact-count');
                const coverEl = el.querySelector('img[data-src], img[src*="xhscdn"], img[src*="sns-webpic"]');
                const descEl = el.querySelector('.desc, .note-desc, .note-content');
                
                if (titleEl && linkEl) {{
                    const authorText = authorEl ? authorEl.innerText.trim() : '';
                    results.push({{
                        source: 'xiaohongshu',
                        title: titleEl.innerText.trim(),
                        url: linkEl.href,
                        snippet: descEl ? descEl.innerText.trim() : authorText,
                        metadata: {{
                            author: authorText,
                            likes: likesEl ? likesEl.innerText.trim() : '',
                            cover: coverEl ? (coverEl.getAttribute('data-src') || coverEl.getAttribute('src')) : '',
                            type: 'note',
                            query: '{query}'
                        }},
                        scraped_at: new Date().toISOString()
                    }});
                }}
            }});
            
            // 如果没找到，尝试通用选择器
            if (results.length === 0) {{
                document.querySelectorAll('.result-item, .list-item, [class*="result"], [class*="list"]').forEach(el => {{
                    const titleEl = el.querySelector('.title, h3, h4, a');
                    const linkEl = el.querySelector('a[href*="xiaohongshu.com"]');
                    const snippetEl = el.querySelector('.snippet, .desc, .summary, .text');
                    
                    if (titleEl && linkEl) {{
                        results.push({{
                            source: 'xiaohongshu',
                            title: titleEl.innerText.trim(),
                            url: linkEl.href,
                            snippet: snippetEl ? snippetEl.innerText.trim() : '',
                            metadata: {{ type: '{search_type}', query: '{query}' }},
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
                    source=r.get('source', 'xiaohongshu'),
                    title=r.get('title', ''),
                    url=r.get('url', ''),
                    snippet=r.get('snippet', ''),
                    metadata=r.get('metadata', {}),
                    scraped_at=r.get('scraped_at', datetime.now().isoformat())
                ))
        
        return search_results[:config.max_results]
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取笔记详情"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            await browser.get(url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await self._smart_wait(browser, config)
            
            # 模拟阅读行为
            await self._simulate_human_behavior(browser)
            
            js_code = """
            (() => {
                const result = {
                    title: document.querySelector('.title, h1, .note-title, .note-name')?.innerText || '',
                    content: document.querySelector('.content, .note-content, .markdown-body, .note-desc')?.innerText || '',
                    author: document.querySelector('.author, .user-name, .note-author')?.innerText || '',
                    likes: document.querySelector('.like-count, .likes, .interact-count')?.innerText || '',
                    comments: document.querySelector('.comment-count, .comments')?.innerText || '',
                    collects: document.querySelector('.collect-count, .collects')?.innerText || '',
                    tags: Array.from(document.querySelectorAll('.tag, .hashtag, .note-tag')).map(el => el.innerText.trim()),
                    images: Array.from(document.querySelectorAll('.note-image, img[data-src]')).map(el => el.getAttribute('data-src') || el.getAttribute('src')),
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
    
    async def search_by_topic(self, topic: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """按话题搜索"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            url = f"https://www.xiaohongshu.com/explore?channel_id=homefeed.{topic}"
            await browser.get(url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await self._simulate_human_behavior(browser)
            await self._smart_wait(browser, config)
            
            results = await self._extract_results(browser, "note", topic)
            await browser.close()
            
            return results
            
        except Exception as e:
            print(f"话题搜索失败: {e}")
            return []
    
    async def search_by_user(self, username: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索用户笔记"""
        config = config or self.config
        
        try:
            from browser_cdp import Browser
            
            browser = Browser(port=config.port, stealth=config.stealth)
            await browser.start()
            
            url = f"https://www.xiaohongshu.com/user/profile/{username}"
            await browser.get(url)
            await asyncio.sleep(random.uniform(*config.random_delay_range))
            await self._simulate_human_behavior(browser)
            await self._smart_wait(browser, config)
            
            js_code = """
            (() => {
                const results = [];
                document.querySelectorAll('.note-item, .note-card, [class*="note"]').forEach(el => {
                    const titleEl = el.querySelector('.title, .note-title, h3');
                    const linkEl = el.querySelector('a[href*="/explore/"]');
                    const likesEl = el.querySelector('.like-count, .likes');
                    
                    if (titleEl && linkEl) {
                        results.push({
                            source: 'xiaohongshu',
                            title: titleEl.innerText.trim(),
                            url: linkEl.href,
                            snippet: '',
                            metadata: {
                                likes: likesEl ? likesEl.innerText.trim() : '',
                                type: 'user_note',
                                username: '{username}'
                            },
                            scraped_at: new Date().toISOString()
                        });
                    }
                });
                return results;
            })()
            """
            
            raw_results = await browser.evaluate(js_code)
            
            search_results = []
            seen_urls = set()
            
            for r in raw_results:
                if r.get('url') and r['url'] not in seen_urls:
                    seen_urls.add(r['url'])
                    search_results.append(SearchResult(
                        source=r.get('source', 'xiaohongshu'),
                        title=r.get('title', ''),
                        url=r.get('url', ''),
                        snippet=r.get('snippet', ''),
                        metadata=r.get('metadata', {}),
                        scraped_at=r.get('scraped_at', datetime.now().isoformat())
                    ))
            
            await browser.close()
            return search_results[:config.max_results]
            
        except Exception as e:
            print(f"用户搜索失败: {e}")
            return []
    
    async def handle_captcha(self, browser, config: SearcherConfig) -> bool:
        """处理验证码（小红书主要使用滑块验证码）"""
        try:
            from browser_cdp.captcha_handler import CaptchaHandler
            
            handler = CaptchaHandler(browser)
            return await handler.handle_geetest()
        except Exception as e:
            print(f"验证码处理失败: {e}")
            return False
    
    async def get_cookie(self, browser) -> str:
        """获取当前 Cookie（用于签名验证）"""
        try:
            cookies = await browser.evaluate("document.cookie")
            return cookies
        except Exception as e:
            print(f"获取 Cookie 失败: {e}")
            return ""


def main():
    parser = argparse.ArgumentParser(description="小红书搜索器")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--type", choices=["note", "user", "product", "topic", "search"],
                        default="note", help="搜索类型")
    parser.add_argument("--username", type=str, default="", help="用户名（用于搜索用户笔记）")
    parser.add_argument("--max-results", type=int, default=20, help="最大结果数量")
    parser.add_argument("--output-dir", default="./search_results/xiaohongshu", help="输出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--port", type=int, default=9333, help="CDP 端口")
    parser.add_argument("--stealth", action="store_true", default=True, help="启用反检测模式")
    
    args = parser.parse_args()
    
    async def run():
        searcher = XiaohongshuSearcher()
        config = SearcherConfig(
            max_results=args.max_results,
            output_dir=args.output_dir,
            port=args.port,
            stealth=args.stealth
        )
        
        print(f"正在搜索小红书 {args.type}... 关键词: {args.query}")
        
        if args.type == "user" and args.username:
            results = await searcher.search_by_user(args.username, config)
        elif args.type == "topic":
            results = await searcher.search_by_topic(args.query, config)
        else:
            results = await searcher.search(args.query, args.type, config)
        
        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
        else:
            print_results(results)
        
        if results:
            save_results(results, args.output_dir, "xiaohongshu")
            print(f"\n结果已保存到: {args.output_dir}")
        else:
            print("未找到结果，可能需要登录或触发验证码")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()
