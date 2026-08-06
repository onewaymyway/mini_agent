#!/usr/bin/env python
"""
reddit_search.py - Reddit帖子搜索器

支持：
- 关键词搜索帖子
- 子版块筛选
- 帖子列表和评论抓取
- 排序方式（最新/热门/Top）
- 反检测模式

技术难点：
- Reddit有反爬机制，需启用 stealth 模式
- 部分子版块需要登录才能访问
- 评论树结构较复杂
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
from src.core.browser_nav import cmd_goto
from src.core.browser_console import cmd_eval
from src.core.stealth import StealthMode, StealthConfig

logger = logging.getLogger(__name__)


@dataclass
class RedditConfig(SearcherConfig):
    """Reddit搜索器专用配置"""
    # 搜索参数
    subreddit: str = ""  # 子版块名称
    sort: str = "relevance"  # 排序方式：relevance/new/top/hot
    
    # 详情抓取
    fetch_comments: bool = False
    max_comment_depth: int = 2  # 评论嵌套深度
    
    # 动态加载
    enable_infinite_scroll: bool = True
    max_scroll_pages: int = 3
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'subreddit': self.subreddit,
            'sort': self.sort,
            'fetch_comments': self.fetch_comments,
            'max_comment_depth': self.max_comment_depth,
            'enable_infinite_scroll': self.enable_infinite_scroll,
            'max_scroll_pages': self.max_scroll_pages,
        })
        return data


@dataclass
class PostInfo(SearchResult):
    """帖子信息数据结构"""
    subreddit: str = ""  # 子版块
    author: str = ""  # 作者
    score: str = ""  # 得分
    comment_count: str = ""  # 评论数
    created_time: str = ""  # 发布时间
    upvote_ratio: str = ""  # 点赞率
    flairs: List[str] = field(default_factory=list)  # 标签
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'subreddit': self.subreddit,
            'author': self.author,
            'score': self.score,
            'comment_count': self.comment_count,
            'created_time': self.created_time,
            'upvote_ratio': self.upvote_ratio,
            'flairs': self.flairs,
        })
        return data


@dataclass
class CommentInfo(SearchResult):
    """评论信息数据结构"""
    author: str = ""  # 作者
    score: str = ""  # 得分
    body: str = ""  # 内容
    created_time: str = ""  # 发布时间
    depth: int = 0  # 嵌套深度
    replies: List['CommentInfo'] = field(default_factory=list)  # 回复
    
    def to_dict(self) -> Dict:
        data = super().to_dict()
        data.update({
            'author': self.author,
            'score': self.score,
            'body': self.body,
            'created_time': self.created_time,
            'depth': self.depth,
            'replies': [r.to_dict() for r in self.replies[:5]],  # 最多5层回复
        })
        return data


class RedditSearcher(BaseSearcher):
    """
    Reddit搜索器
    
    使用方式：
        config = RedditConfig(query="Python", subreddit="programming", max_results=20)
        searcher = RedditSearcher(config=config)
        results = searcher.search()
        results.save_json('output/reddit_results.json')
    """
    
    BASE_URL = "https://www.reddit.com"
    SEARCH_URL = "https://www.reddit.com/search/"
    
    @property
    def source_name(self) -> str:
        return "reddit"
    
    @property
    def supported_types(self) -> List[str]:
        return ["post_search", "post_detail", "comment_search"]
    
    def __init__(self, config: RedditConfig = None):
        super().__init__(config or RedditConfig())
        self._session = None
        
    @property
    def session(self):
        """获取 CDP session"""
        if self._session is None:
            from src.core.utils import get_session
            from src.core.utils import add_connection_args
            import argparse
            
            parser = argparse.ArgumentParser()
            add_connection_args(parser)
            args = parser.parse_args([])
            args.port = self.config.port
            args.tab = self.config.tab_id
            self._session = get_session(args)
        return self._session
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """执行搜索"""
        if config:
            self.config = config
        if query:
            self.config.query = query
        
        results = SearchResults(source="reddit", query=query)
        
        try:
            # 构建搜索 URL
            search_url = f"{self.SEARCH_URL}?q={query}"
            
            if self.config.subreddit:
                search_url += f"&sort={self.config.sort}"
            
            logger.info(f"搜索帖子: {query}, 子版块: {self.config.subreddit or '全部'}")
            
            # 导航到搜索页面
            cmd_goto(
                self.session,
                search_url,
                wait_load=True,
                timeout=self.config.wait_timeout,
                wait_for=self.config.wait_strategy,
                enable_stealth=self.config.stealth,
            )
            
            # 等待页面加载
            time.sleep(2)
            
            # 提取帖子列表
            posts = self._extract_posts_from_page()
            results.results.extend(posts)
            
            # 无限滚动加载更多
            results.results = await self._scroll_and_collect(results.results, self.config.max_results)
            
            # 去重
            results.deduplicate(by=self.config.dedup_by, threshold=self.config.dedup_threshold)
            
            logger.info(f"搜索完成，共获取 {len(results.results)} 个帖子")
            
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            results.error = str(e)
        
        return results.results
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取帖子详情"""
        try:
            # 导航到详情页
            cmd_goto(
                self.session,
                url,
                wait_load=True,
                timeout=self.config.wait_timeout,
                wait_for="networkidle",
                enable_stealth=self.config.stealth,
            )
            time.sleep(2)
            
            # 提取详情
            detail = self._extract_post_detail()
            
            # 如果需要评论
            if self.config.fetch_comments:
                comments = self._extract_comments()
                detail['comments'] = comments
            
            return detail
            
        except Exception as e:
            logger.error(f"获取帖子详情失败: {e}")
            return {}
    
    def _extract_posts_from_page(self) -> List[PostInfo]:
        """
        从当前页面提取帖子列表
        
        Returns:
            帖子列表
        """
        js_code = """
        (function() {
            const posts = [];
            const cards = document.querySelectorAll('article[data-testid="post-container"], .PostList__PostContainer');
            
            cards.forEach(card => {
                const post = {
                    title: card.querySelector('a[data-testid="post-title"]')?.textContent?.trim() || 
                           card.querySelector('[data-testid="post-title"]')?.textContent?.trim() || '',
                    subreddit: card.querySelector('[data-testid="subreddit"]')?.textContent?.trim() || 
                              card.querySelector('[class*="subreddit"]')?.textContent?.trim() || '',
                    author: card.querySelector('[data-testid="author"]')?.textContent?.trim() || 
                           card.querySelector('[class*="author"]')?.textContent?.trim() || '',
                    score: card.querySelector('[data-testid="score"]')?.textContent?.trim() || 
                          card.querySelector('[class*="score"]')?.textContent?.trim() || '',
                    comment_count: card.querySelector('[data-testid="comment-count"]')?.textContent?.trim() || '',
                    created_time: card.querySelector('[data-testid="post-time"]')?.textContent?.trim() || '',
                    upvote_ratio: card.querySelector('[class*="upvote"]')?.textContent?.trim() || '',
                    url: card.querySelector('a')?.href || ''
                };
                if (post.title) posts.push(post);
            });
            
            return JSON.stringify(posts);
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                post_data_list = json.loads(result['result'])
                return [self._parse_post_card(data) for data in post_data_list]
        except Exception as e:
            logger.error(f"提取帖子列表失败: {e}")
        
        return []
    
    def _parse_post_card(self, card_data: Dict) -> Optional[PostInfo]:
        """
        解析帖子卡片数据
        
        Args:
            card_data: 帖子卡片数据
            
        Returns:
            PostInfo 对象
        """
        try:
            title = card_data.get('title', '').strip()
            if not title:
                return None
            
            post = PostInfo(
                source="reddit",
                title=title,
                url=card_data.get('url', '').strip(),
                subreddit=card_data.get('subreddit', '').strip(),
                author=card_data.get('author', '').strip(),
                score=card_data.get('score', '').strip(),
                comment_count=card_data.get('comment_count', '').strip(),
                created_time=card_data.get('created_time', '').strip(),
                upvote_ratio=card_data.get('upvote_ratio', '').strip(),
                scraped_at=datetime.now().isoformat()
            )
            
            return post
            
        except Exception as e:
            logger.error(f"解析帖子卡片失败: {e}")
            return None
    
    def _extract_post_detail(self) -> Dict:
        """
        提取帖子详情
        
        Returns:
            详情字典
        """
        js_code = """
        (function() {
            return JSON.stringify({
                title: document.querySelector('[data-testid="post-title"]')?.textContent?.trim() || '',
                author: document.querySelector('[data-testid="author"]')?.textContent?.trim() || '',
                score: document.querySelector('[data-testid="score"]')?.textContent?.trim() || '',
                comment_count: document.querySelector('[data-testid="comment-count"]')?.textContent?.trim() || '',
                content: document.querySelector('[data-testid="post-content"]')?.textContent?.trim() || '',
                created_time: document.querySelector('[data-testid="post-time"]')?.textContent?.trim() || '',
                flairs: Array.from(document.querySelectorAll('[class*="flair"]')).map(f => f.textContent.trim())
            });
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                return json.loads(result['result'])
        except Exception as e:
            logger.error(f"提取帖子详情失败: {e}")
        
        return {}
    
    def _extract_comments(self) -> List[CommentInfo]:
        """
        提取帖子评论
        
        Returns:
            评论列表
        """
        js_code = """
        (function() {
            const comments = [];
            const commentElements = document.querySelectorAll('[data-testid="comment"]');
            
            commentElements.forEach(el => {
                const comment = {
                    author: el.querySelector('[data-testid="author"]')?.textContent?.trim() || '',
                    score: el.querySelector('[data-testid="score"]')?.textContent?.trim() || '',
                    body: el.querySelector('[data-testid="comment-text"]')?.textContent?.trim() || '',
                    created_time: el.querySelector('[data-testid="comment-time"]')?.textContent?.trim() || '',
                    depth: el.getAttribute('data-depth') || 0
                };
                if (comment.body) comments.push(comment);
            });
            
            return JSON.stringify(comments);
        })()
        """
        
        try:
            result = cmd_eval(self.session, js_code)
            if result and 'result' in result:
                comment_data_list = json.loads(result['result'])
                return [self._parse_comment(data) for data in comment_data_list]
        except Exception as e:
            logger.error(f"提取评论失败: {e}")
        
        return []
    
    def _parse_comment(self, comment_data: Dict) -> Optional[CommentInfo]:
        """
        解析评论数据
        
        Args:
            comment_data: 评论数据
            
        Returns:
            CommentInfo 对象
        """
        try:
            body = comment_data.get('body', '').strip()
            if not body:
                return None
            
            comment = CommentInfo(
                source="reddit",
                title=comment_data.get('author', '').strip(),
                url="",
                content=body,
                author=comment_data.get('author', '').strip(),
                score=comment_data.get('score', '').strip(),
                created_time=comment_data.get('created_time', '').strip(),
                depth=int(comment_data.get('depth', 0)),
                scraped_at=datetime.now().isoformat()
            )
            
            return comment
            
        except Exception as e:
            logger.error(f"解析评论失败: {e}")
            return None
    
    async def _scroll_and_collect(self, results: List[PostInfo], max_results: int) -> List[PostInfo]:
        """
        无限滚动加载更多帖子
        
        Args:
            results: 已有结果
            max_results: 最大结果数
            
        Returns:
            更新后的结果列表
        """
        if not self.config.enable_infinite_scroll:
            return results
        
        existing_titles = {p.title for p in results}
        
        for i in range(self.config.max_scroll_pages):
            if len(results) >= max_results:
                break
            
            # 滚动加载
            self.session.eval_js("window.scrollBy(0, 800)")
            time.sleep(1.5)
            
            # 提取新帖子
            new_posts = self._extract_posts_from_page()
            
            # 去重
            for post in new_posts:
                if post.title not in existing_titles:
                    results.append(post)
                    existing_titles.add(post.title)
            
            logger.info(f"滚动加载第 {i+1} 页，当前共 {len(results)} 个帖子")
        
        return results
    
    def _random_delay(self) -> float:
        """
        生成随机延迟时间（1-3秒）
        
        Returns:
            随机延迟秒数
        """
        return random.uniform(1.0, 3.0)
    
    async def search_batch(self, queries: List[str], **kwargs) -> SearchResults:
        """
        批量搜索
        
        Args:
            queries: 关键词列表
            **kwargs: 其他参数
        
        Returns:
            合并后的搜索结果
        """
        all_results = SearchResults(source="reddit", query="batch")
        
        for i, query in enumerate(queries):
            logger.info(f"批量搜索 [{i+1}/{len(queries)}]: {query}")
            
            # 搜索
            results_list = await self.search(query)
            all_results.results.extend(results_list)
            
            # 随机延迟
            time.sleep(self._random_delay())
        
        # 去重
        all_results.deduplicate(by=self.config.dedup_by, threshold=self.config.dedup_threshold)
        
        return all_results
    
    async def close(self):
        """关闭浏览器资源"""
        if self._session:
            try:
                self._session.close()
            except Exception as e:
                logger.warning(f"关闭浏览器时出错: {e}")


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Reddit帖子搜索器')
    parser.add_argument('--port', type=int, default=9333, help='CDP 调试端口')
    parser.add_argument('--tab', type=str, required=True, help='浏览器 tab ID')
    parser.add_argument('--keyword', type=str, required=True, help='搜索关键词')
    parser.add_argument('--subreddit', type=str, default='', help='子版块名称')
    parser.add_argument('--sort', type=str, default='relevance', choices=['relevance', 'new', 'top', 'hot'], help='排序方式')
    parser.add_argument('--max-results', type=int, default=50, help='最大结果数')
    parser.add_argument('--output', type=str, help='输出文件路径')
    parser.add_argument('--no-stealth', action='store_true', help='禁用反检测模式')
    parser.add_argument('--no-scroll', action='store_true', help='禁用无限滚动')
    parser.add_argument('--comments', action='store_true', help='抓取帖子评论')
    
    args = parser.parse_args()
    
    # 创建配置
    config = RedditConfig(
        port=args.port,
        tab_id=args.tab,
        query=args.keyword,
        subreddit=args.subreddit,
        sort=args.sort,
        max_results=args.max_results,
        stealth=not args.no_stealth,
        enable_infinite_scroll=not args.no_scroll,
        fetch_comments=args.comments,
    )
    
    # 创建搜索器
    searcher = RedditSearcher(config=config)
    
    try:
        # 执行搜索
        results = asyncio.run(searcher.search(args.keyword))
        
        # 输出结果
        if results:
            print("\n" + "="*60)
            print(f"搜索关键词: {args.keyword}")
            if args.subreddit:
                print(f"子版块: {args.subreddit}")
            print(f"共找到 {len(results)} 个帖子")
            print("="*60 + "\n")
            
            for i, post in enumerate(results[:20], 1):  # 只显示前20个
                print(f"【{i}】{post.title}")
                print(f"    子版块: {post.subreddit}")
                print(f"    作者: {post.author}")
                print(f"    得分: {post.score} | 评论: {post.comment_count}")
                print(f"    发布时间: {post.created_time}")
                print()
            
            # 保存结果
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump([r.to_dict() for r in results], f, ensure_ascii=False, indent=2)
                print(f"[保存] 结果已保存到: {output_path}")
        else:
            print("[提示] 未找到相关帖子")
    
    finally:
        asyncio.run(searcher.close())


if __name__ == '__main__':
    main()
