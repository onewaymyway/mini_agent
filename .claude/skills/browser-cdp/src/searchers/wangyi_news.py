#!/usr/bin/env python
"""
wangyi_news.py - 网易新闻抓取脚本

支持：
- 新闻列表页抓取（滚动加载）
- 新闻详情页抓取
- 分类导航（新闻/财经/科技/体育等）
- 时间范围筛选

技术特点：
- 反爬强度低（⭐⭐）
- 动态渲染简单（传统HTML + 少量AJAX）
- 无需登录即可访问大部分内容
- 可直接使用 browser_extract.py 抓取

适配优先级：高（P0）
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.searchers.base import BaseSearcher, SearcherConfig, SearchResult, SearchResults
from src.searchers.utils import save_results


class WangyiNewsSearcher(BaseSearcher):
    """网易新闻搜索器"""
    
    SOURCE_NAME = "wangyi_news"
    SUPPORTED_TYPES = ["news", "article", "list"]
    
    # 网易新闻URL模板
    BASE_URL = "https://news.163.com"
    CATEGORY_URLS = {
        "news": "https://news.163.com/",
        "finance": "https://money.163.com/",
        "tech": "https://tech.163.com/",
        "sports": "https://sports.163.com/",
        "entertainment": "https://ent.163.com/",
        "war": "https://war.163.com/",
    }
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        super().__init__(config)
        self.session_name = "wangyi_session"
    
    @property
    def source_name(self) -> str:
        return self.SOURCE_NAME
    
    @property
    def supported_types(self) -> List[str]:
        return self.SUPPORTED_TYPES
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """搜索网易新闻"""
        cfg = config or self.config
        
        # 使用百度搜索网易新闻
        search_url = f"https://www.baidu.com/s?wd=site:news.163.com+{query}"
        
        results = []
        try:
            # 启动浏览器
            from src.core.browser_launch import launch_browser
            port, tab_id = await launch_browser(
                dedicated=True,
                name=self.session_name,
                start_url=search_url,
                stealth=cfg.stealth
            )
            
            # 等待搜索结果加载
            from src.core.browser_nav import wait_for_load
            await wait_for_load(port, tab_id, strategy=cfg.wait_strategy, timeout=cfg.wait_timeout)
            
            # 提取搜索结果
            from src.core.browser_extract import extract_search_results
            results_data = await extract_search_results(
                port, tab_id, 
                source="wangyi",
                max_results=cfg.max_results
            )
            
            results = [SearchResult(**r) for r in results_data]
            
            # 关闭浏览器
            from src.core.browser_launch import close_browser
            await close_browser(port)
            
        except Exception as e:
            print(f"[wangyi_news] 搜索失败: {e}", file=sys.stderr)
        
        return results
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取新闻详情"""
        cfg = config or self.config
        
        try:
            from src.core.browser_launch import launch_browser
            port, tab_id = await launch_browser(
                dedicated=True,
                name=self.session_name,
                start_url=url,
                stealth=cfg.stealth
            )
            
            # 等待页面加载
            from src.core.browser_nav import wait_for_load
            await wait_for_load(port, tab_id, strategy=cfg.wait_strategy, timeout=cfg.wait_timeout)
            
            # 提取内容
            from src.core.browser_extract import extract_article
            article = await extract_article(port, tab_id)
            
            # 关闭浏览器
            from src.core.browser_launch import close_browser
            await close_browser(port)
            
            return article
            
        except Exception as e:
            print(f"[wangyi_news] 获取详情失败: {e}", file=sys.stderr)
            return {}
    
    async def fetch_category(self, category: str, max_results: int = 20, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """获取分类新闻列表"""
        cfg = config or self.config
        
        if category not in self.CATEGORY_URLS:
            raise ValueError(f"不支持的分类: {category}")
        
        results = []
        try:
            from src.core.browser_launch import launch_browser
            port, tab_id = await launch_browser(
                dedicated=True,
                name=self.session_name,
                start_url=self.CATEGORY_URLS[category],
                stealth=cfg.stealth
            )
            
            # 等待加载
            from src.core.browser_nav import wait_for_load
            await wait_for_load(port, tab_id, strategy=cfg.wait_strategy, timeout=cfg.wait_timeout)
            
            # 提取列表
            from src.core.browser_extract import extract_news_list
            news_list = await extract_news_list(
                port, tab_id,
                max_results=max_results,
                source=f"wangyi_{category}"
            )
            
            results = [SearchResult(**r) for r in news_list]
            
            # 关闭浏览器
            from src.core.browser_launch import close_browser
            await close_browser(port)
            
        except Exception as e:
            print(f"[wangyi_news] 获取分类失败: {e}", file=sys.stderr)
        
        return results
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            from src.core.browser_launch import launch_browser
            port, tab_id = await launch_browser(
                dedicated=True,
                name=self.session_name,
                start_url=self.BASE_URL,
                stealth=True
            )

            from src.core.browser_nav import wait_for_load
            await wait_for_load(port, tab_id, timeout=10)

            from src.core.browser_launch import close_browser
            await close_browser(port)

            return True
        except Exception:
            return False


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="网易新闻抓取工具")
    parser.add_argument("--query", "-q", help="搜索关键词")
    parser.add_argument("--category", "-c", choices=list(WangyiNewsSearcher.CATEGORY_URLS.keys()),
                        help="新闻分类")
    parser.add_argument("--max-results", "-n", type=int, default=20, help="最大结果数")
    parser.add_argument("--output", "-o", help="输出文件路径")
    parser.add_argument("--format", "-f", choices=["json", "markdown"], default="json", help="输出格式")
    parser.add_argument("--stealth", action="store_true", help="启用反检测模式")
    parser.add_argument("--port", type=int, default=9333, help="浏览器调试端口")

    args = parser.parse_args()

    async def run():
        searcher = WangyiNewsSearcher(SearcherConfig(stealth=args.stealth))

        if args.query:
            results = await searcher.search(args.query)
        elif args.category:
            results = await searcher.fetch_category(args.category, args.max_results)
        else:
            # 默认获取新闻首页
            results = await searcher.fetch_category("news", args.max_results)

        # 输出结果
        if args.output:
            save_results(results, args.output, args.format)
            print(f"结果已保存到: {args.output}")
        else:
            print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))

    import asyncio
    asyncio.run(run())


if __name__ == "__main__":
    main()
