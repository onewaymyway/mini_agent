#!/usr/bin/env python
"""
bilibili_search.py - B站视频搜索器

支持：
- 视频搜索（标题、UP主、标签）
- 视频详情抓取（播放量、弹幕数、时长）
- UP主搜索
- 动态内容处理（无限滚动、懒加载）
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from pathlib import Path

from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult, SearchResults
from src.core.smart_wait import SmartWait, WaitConfig
from src.core.dynamic_loader import DynamicLoader, ScrollConfig
from src.core.stealth import StealthConfig

logger = logging.getLogger(__name__)


@dataclass
class BilibiliConfig(SearcherConfig):
    """B站搜索器专用配置"""
    # 搜索类型
    search_type: str = "all"  # all/video/user/dynamic
    
    # 排序方式
    order: str = "totalrank"  # totalrank/play/comment/pubdate
    
    # 分页
    page: int = 1
    page_size: int = 20
    
    # 时间范围
    duration: int = 0  # 0=全部, 1=1分钟以内, 2=1-5分钟, 3=5-30分钟, 4=30分钟以上
    
    # 动态加载
    enable_infinite_scroll: bool = False
    max_scroll_pages: int = 5
    
    # 详情抓取
    fetch_details: bool = True
    
    def __post_init__(self):
        if self.session_name is None:
            self.session_name = "bilibili_session"


class BilibiliSearcher(BaseSearcher):
    """
    B站视频搜索器
    
    特性：
    - 视频搜索（支持多种排序和筛选）
    - 视频详情抓取（播放量、弹幕数、时长、标签）
    - UP主搜索
    - 动态内容处理（无限滚动、懒加载）
    - 反检测模式
    """
    
    BASE_URL = "https://search.bilibili.com"
    API_BASE = "https://api.bilibili.com"
    
    @property
    def source_name(self) -> str:
        return "bilibili"
    
    @property
    def supported_types(self) -> List[str]:
        return ["video_search", "video_detail", "user_search"]
    
    def __init__(self, config: BilibiliConfig = None):
        super().__init__(config or BilibiliConfig())
        self._smart_wait = None
        self._dynamic_loader = None
        self._stealth_manager = None
        self._session = None

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, value):
        self._session = value
    
    async def search(
        self,
        query: str,
        search_type: str = None,
        order: str = None,
        page: int = 1,
        max_results: int = None
    ) -> SearchResults:
        """
        搜索视频
        
        Args:
            query: 搜索关键词
            search_type: 搜索类型 (all/video/user/dynamic)
            order: 排序方式 (totalrank/play/comment/pubdate)
            page: 页码
            max_results: 最大结果数
        
        Returns:
            SearchResults: 搜索结果
        """
        self.config.query = query
        if search_type:
            self.config.search_type = search_type
        if order:
            self.config.order = order
        if max_results:
            self.config.max_results = max_results
        
        logger.info(f"开始搜索 B站视频: {query}, 类型: {self.config.search_type}, 排序: {self.config.order}")
        
        results = SearchResults(
            source="bilibili",
            query=query
        )
        results.metadata['search_type'] = self.config.search_type
        
        try:
            # 确保浏览器连接
            await self._ensure_browser()
            
            # 构建搜索 URL
            search_url = self._build_search_url(query, page)
            logger.info(f"导航到搜索页: {search_url}")
            
            # 导航并等待页面加载
            await self._navigate_and_wait(search_url)
            
            # 处理动态内容（无限滚动）
            if self.config.enable_infinite_scroll:
                await self._handle_infinite_scroll(results)
            else:
                # 抓取当前页结果
                await self._extract_search_results(results)
            
            # 抓取详情（可选）
            if self.config.fetch_details and len(results.results) < self.config.max_results:
                await self._fetch_details(results)
            
            # 去重
            results.deduplicate(by=self.config.dedup_by, threshold=self.config.dedup_threshold)
            
            logger.info(f"搜索完成，共获取 {len(results.results)} 条结果")
            
        except Exception as e:
            logger.error(f"搜索失败: {e}", exc_info=True)
            results.error = str(e)
        
        return results
    
    async def search_batch(self, queries: List[str], **kwargs) -> SearchResults:
        """
        批量搜索
        
        Args:
            queries: 关键词列表
            **kwargs: 其他参数
        
        Returns:
            SearchResults: 合并后的搜索结果
        """
        all_results = SearchResults(source="bilibili", query="batch")
        
        for i, query in enumerate(queries):
            logger.info(f"批量搜索 [{i+1}/{len(queries)}]: {query}")
            
            # 搜索
            results = await self.search(query, **kwargs)
            all_results.results.extend(results.results)
            
            # 随机延迟
            await asyncio.sleep(random.uniform(1.0, 3.0))
        
        # 去重
        all_results.deduplicate(by=self.config.dedup_by, threshold=self.config.dedup_threshold)
        
        return all_results
    
    async def search_user(
        self,
        query: str,
        max_results: int = 10
    ) -> SearchResults:
        """
        搜索 UP 主
        
        Args:
            query: UP 主名称
            max_results: 最大结果数
        
        Returns:
            SearchResults: UP 主列表
        """
        self.config.query = query
        self.config.search_type = "user"
        self.config.max_results = max_results
        
        logger.info(f"开始搜索 UP 主: {query}")
        
        results = SearchResults(
            source="bilibili",
            query=query,
            search_type="user"
        )
        
        try:
            await self._ensure_browser()
            
            # B站用户搜索 URL
            search_url = f"{self.BASE_URL}?keyword={query}&search_type=user"
            await self._navigate_and_wait(search_url)
            
            # 抓取用户结果
            await self._extract_user_results(results)
            
            results.deduplicate(by="url", threshold=0.9)
            
            logger.info(f"UP 主搜索完成，共获取 {len(results.results)} 条结果")
            
        except Exception as e:
            logger.error(f"UP 主搜索失败: {e}", exc_info=True)
            results.error = str(e)
        
        return results
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """
        获取视频详情（实现 BaseSearcher 抽象方法）

        Args:
            url: 视频 URL 或 BV 号
            config: 可选配置覆盖

        Returns:
            Dict: 视频详情
        """
        # 从 URL 提取 BV 号
        bv_id = url
        if "/video/" in url:
            bv_id = url.split("/video/")[-1].split("?")[0]
        return await self.get_video_detail(bv_id)

    async def get_video_detail(self, bv_id: str) -> Optional[Dict]:
        """
        获取视频详情

        Args:
            bv_id: 视频 BV 号

        Returns:
            Dict: 视频详情（播放量、弹幕数、时长、标签等）
        """
        logger.info(f"获取视频详情: {bv_id}")
        
        try:
            # 方法 1: 通过 API 获取（更稳定）
            api_url = f"{self.API_BASE}/x/web-interface/view?bvid={bv_id}"
            
            # 使用 CDP 执行 JS 获取数据
            detail = await self._fetch_via_api(api_url)
            
            if detail:
                return detail
            
            # 方法 2: 通过页面解析（备用）
            page_url = f"https://www.bilibili.com/video/{bv_id}"
            await self._navigate_and_wait(page_url)
            return await self._extract_video_detail_from_page(bv_id)
            
        except Exception as e:
            logger.error(f"获取视频详情失败: {e}", exc_info=True)
            return None
    
    # =========================================================================
    # 内部方法
    # =========================================================================
    
    def _build_search_url(self, query: str, page: int = 1) -> str:
        """构建搜索 URL"""
        params = {
            "keyword": query,
            "search_type": self.config.search_type,
            "order": self.config.order,
            "page": page,
        }
        
        # 添加筛选条件
        if self.config.duration > 0:
            params["duration"] = self.config.duration
        
        # 构建 URL
        url_parts = [f"{self.BASE_URL}"]
        url_parts.append("?" + "&".join(f"{k}={v}" for k, v in params.items()))
        
        return "".join(url_parts)
    
    async def _navigate_and_wait(self, url: str):
        """导航并等待页面加载完成"""
        # 导航
        await self.session.goto(url)
        
        # 智能等待
        if self._smart_wait is None:
            self._smart_wait = SmartWait(
                self.session,
                WaitConfig(
                    timeout=self.config.wait_timeout,
                )
            )
        
        await self._smart_wait.wait_for("networkidle")
    
    async def _extract_search_results(self, results: SearchResults):
        """从搜索结果页提取数据"""
        # 使用 JS 提取搜索结果
        js_code = """
        () => {
            const items = [];
            const elements = document.querySelectorAll('.video-item, .result-item, [class*="video"]');
            
            elements.forEach(el => {
                const titleEl = el.querySelector('.title, .video-title, a');
                const linkEl = el.querySelector('a[href*="video/"]');
                const statsEl = el.querySelector('.stat, .data-box');
                
                if (titleEl && linkEl) {
                    items.push({
                        title: titleEl.textContent.trim(),
                        url: linkEl.href,
                        stats: statsEl ? statsEl.textContent.trim() : '',
                        author: el.querySelector('.author, .up-name')?.textContent.trim() || '',
                    });
                }
            });
            
            return items;
        }
        """
        
        try:
            data = await self.session.execute_js(js_code)
            if data and isinstance(data, list):
                for item in data:
                    result = SearchResult(
                        source="bilibili",
                        title=item.get('title', ''),
                        url=item.get('url', ''),
                        author=item.get('author', ''),
                        snippet=item.get('stats', ''),
                        scraped_at=datetime.now().isoformat()
                    )
                    results.results.append(result)
        except Exception as e:
            logger.warning(f"提取搜索结果失败: {e}")
    
    async def _extract_user_results(self, results: SearchResults):
        """从用户搜索结果页提取数据"""
        js_code = """
        () => {
            const items = [];
            const elements = document.querySelectorAll('.user-item, .result-item');
            
            elements.forEach(el => {
                const nameEl = el.querySelector('.name, .user-name, a');
                const linkEl = el.querySelector('a[href*="space.bilibili.com"]');
                const fansEl = el.querySelector('.fans, .data-content');
                
                if (nameEl && linkEl) {
                    items.push({
                        name: nameEl.textContent.trim(),
                        url: linkEl.href,
                        fans: fansEl ? fansEl.textContent.trim() : '',
                    });
                }
            });
            
            return items;
        }
        """
        
        try:
            data = await self.session.execute_js(js_code)
            if data and isinstance(data, list):
                for item in data:
                    result = SearchResult(
                        source="bilibili",
                        title=item.get('name', ''),
                        url=item.get('url', ''),
                        snippet=f"粉丝：{item.get('fans', '')}",
                        scraped_at=datetime.now().isoformat()
                    )
                    results.results.append(result)
        except Exception as e:
            logger.warning(f"提取用户结果失败: {e}")
    
    async def _fetch_via_api(self, api_url: str) -> Optional[Dict]:
        """通过 API 获取数据"""
        try:
            # 使用 CDP 获取页面数据
            response = await self.session.execute_js(f"""
                fetch('{api_url}', {{
                    headers: {{
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }}
                }}).then(r => r.json())
            """)
            
            if response and response.get('code') == 0:
                return response.get('data', {})
            return None
            
        except Exception as e:
            logger.warning(f"API 请求失败: {e}")
            return None
    
    async def _extract_video_detail_from_page(self, bv_id: str) -> Optional[Dict]:
        """从视频详情页提取数据"""
        js_code = f"""
        () => {{
            const title = document.querySelector('#viewbox_report > h1 > span')?.textContent?.trim() || '';
            const play = document.querySelector('#viewbox_report > div > span')?.textContent?.trim() || '';
            const date = document.querySelector('#viewbox_report > div > span:last-child')?.textContent?.trim() || '';
            const desc = document.querySelector('#desc')?.textContent?.trim() || '';
            
            return {{
                title: title,
                play: play,
                date: date,
                description: desc,
                bv_id: '{bv_id}'
            }};
        }}
        """
        
        try:
            return await self.session.execute_js(js_code)
        except Exception as e:
            logger.warning(f"提取视频详情失败: {e}")
            return None
    
    async def _handle_infinite_scroll(self, results: SearchResults):
        """处理无限滚动加载"""
        if self._dynamic_loader is None:
            self._dynamic_loader = DynamicLoader(self.session)
        
        try:
            await self._dynamic_loader.scroll_to_load(
                max_pages=self.config.max_scroll_pages,
                scroll_delay=1.0,
                callback=lambda pages, height: logger.info(f"已滚动 {pages} 页，高度: {height}")
            )
            
            # 重新提取结果
            await self._extract_search_results(results)
            
        except Exception as e:
            logger.warning(f"无限滚动失败: {e}")
    
    async def _fetch_details(self, results: SearchResults):
        """批量抓取视频详情"""
        for i, result in enumerate(results.results[:self.config.max_results]):
            if 'bv_id' not in result.metadata:
                # 从 URL 提取 bv_id
                match = re.search(r'bv(\w+)', result.url)
                if match:
                    bv_id = f"bv{match.group(1)}"
                    detail = await self.get_video_detail(bv_id)
                    if detail:
                        result.metadata.update(detail)
            
            # 随机延迟
            await asyncio.sleep(self.config.random_delay_range[0])
    
    async def _ensure_browser(self):
        """确保浏览器连接"""
        if self.session is None:
            from src.core.browser_launch import BrowserLauncher
            launcher = BrowserLauncher()
            await launcher.ensure_dedicated(self.config.session_name)
            self.session = launcher.get_session(self.config.session_name)
        
        # 启用反检测（懒加载）
        if self.config.stealth and self._stealth_manager is None:
            from src.core.stealth import StealthMode
            self._stealth_manager = StealthMode(self.session, StealthConfig())
            await self._stealth_manager.apply()
    
    def save_results(self, results: SearchResults, output_dir: str = None):
        """保存搜索结果"""
        from src.searchers.utils import save_results as save_results_util
        output_dir = output_dir or self.config.output_dir
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            
            # JSON 格式
            json_path = Path(output_dir) / f"{self.config.query}_bilibili_results.json"
            save_results_util([r.to_dict() for r in results.results], output_dir, f"{self.config.query}_bilibili_results.json", fmt="json")
            logger.info(f"结果已保存到: {json_path}")
            
            # CSV 格式
            csv_path = Path(output_dir) / f"{self.config.query}_bilibili_results.csv"
            save_results_util([r.to_dict() for r in results.results], output_dir, f"{self.config.query}_bilibili_results.csv", fmt="csv")
            logger.info(f"结果已保存到: {csv_path}")
    
    def close(self):
        """关闭浏览器"""
        if self.session:
            try:
                self.session.close()
            except Exception as e:
                logger.warning(f"关闭浏览器失败: {e}")


# =========================================================================
# 命令行接口
# =========================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="B站视频搜索器")
    parser.add_argument("--query", "-q", required=True, help="搜索关键词")
    parser.add_argument("--type", "-t", default="all", choices=["all", "video", "user", "dynamic"], help="搜索类型")
    parser.add_argument("--order", "-o", default="totalrank", choices=["totalrank", "play", "comment", "pubdate"], help="排序方式")
    parser.add_argument("--page", "-p", type=int, default=1, help="页码")
    parser.add_argument("--max-results", "-m", type=int, default=20, help="最大结果数")
    parser.add_argument("--no-details", action="store_true", help="不抓取详情")
    parser.add_argument("--infinite-scroll", action="store_true", help="启用无限滚动")
    parser.add_argument("--output", "-o", help="输出目录")
    parser.add_argument("--name", "-n", default="bilibili_session", help="浏览器实例名")
    
    args = parser.parse_args()
    
    async def main():
        config = BilibiliConfig(
            query=args.query,
            search_type=args.type,
            order=args.order,
            page=args.page,
            max_results=args.max_results,
            fetch_details=not args.no_details,
            enable_infinite_scroll=args.infinite_scroll,
            output_dir=args.output,
            session_name=args.name,
        )
        
        searcher = BilibiliSearcher(config)
        
        try:
            if args.type == "user":
                results = await searcher.search_user(args.query, args.max_results)
            else:
                results = await searcher.search(args.query, args.type, args.order, args.page, args.max_results)
            
            # 打印结果
            print(f"\n搜索关键词: {args.query}")
            print(f"共找到 {len(results.results)} 条结果\n")
            
            for i, result in enumerate(results.results[:10], 1):
                print(f"{i}. {result.title}")
                print(f"   URL: {result.url}")
                if result.author:
                    print(f"   UP主: {result.author}")
                if result.snippet:
                    print(f"   摘要: {result.snippet}")
                print()
            
            # 保存结果
            if args.output:
                searcher.save_results(results)
            
        finally:
            searcher.close()
    
    asyncio.run(main())
