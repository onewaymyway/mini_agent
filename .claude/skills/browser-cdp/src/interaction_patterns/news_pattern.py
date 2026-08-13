"""
news_pattern.py - 新闻类网站交互模式

流程：打开门户/搜索页 → 浏览热点 → 进入文章 → 提取正文 → （可选）抓取评论
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ._base import InteractionPattern

logger = logging.getLogger(__name__)


@dataclass
class ArticleData:
    """文章数据模型"""
    title: str = ""
    url: str = ""
    author: str = ""
    publish_time: str = ""
    content: str = ""
    snippet: str = ""
    source_domain: str = ""
    category: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "url": self.url,
            "author": self.author,
            "publish_time": self.publish_time,
            "content": self.content,
            "snippet": self.snippet,
            "source_domain": self.source_domain,
            "category": self.category,
            "tags": self.tags,
            "metadata": self.metadata,
        }


@dataclass
class ArticleResults:
    """文章搜索结果"""
    success: bool = True
    query: str = ""
    articles: List[ArticleData] = field(default_factory=list)
    total_count: int = 0
    error_message: str = ""
    pattern_used: str = ""
    latency_ms: float = 0.0

    @property
    def is_empty(self) -> bool:
        return len(self.articles) == 0

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "query": self.query,
            "articles": [a.to_dict() for a in self.articles],
            "total_count": self.total_count,
            "error_message": self.error_message,
            "pattern_used": self.pattern_used,
            "latency_ms": self.latency_ms,
        }


class NewsPattern(InteractionPattern):
    """新闻类网站交互模式基类"""

    # 通用新闻选择器
    DEFAULT_SELECTORS = {
        # 搜索相关
        "search_input": None,  # 子类必须定义
        "search_button": None,
        # 结果列表
        "result_item": ".article-item, .news-item, .feed-item",
        "result_title": "h1, h2, h3, .title, a.title",
        "result_url": "a[href]",
        "result_snippet": ".summary, .excerpt, .content, p",
        # 分页
        "next_page": 'a.next, a.pagination-next, [rel="next"]',
        # 文章正文
        "article_content": ".article-content, .post-content, .entry-content, article",
        "article_title": "h1, .article-title",
        "article_author": ".author, .byline, .article-author",
        "article_time": ".time, .publish-time, .date",
        # 评论
        "comment_section": ".comments, .comment-section, .comment-list",
        "comment_item": ".comment-item, .comment",
        "comment_text": ".comment-text, .comment-content, p",
        "comment_author": ".comment-author, .username",
        "comment_time": ".comment-time, .time",
    }

    def __init__(self, session, domain: str, config: Optional[Dict] = None):
        super().__init__(session, domain, config)
        self._max_pages: int = config.get("max_pages", 1) if config else 1
        self._register_default_selectors()

    def _register_default_selectors(self):
        """注册默认选择器到当前域名"""
        from ..core.selector_manager import Selector, SelectorType
        for name, value in self.DEFAULT_SELECTORS.items():
            if value is not None:
                sel = Selector(type=SelectorType.CSS, value=value)
                self._selectors.register(self._domain, name, sel)
            else:
                # search_input/search_button 由子类定义，跳过
                pass

    async def search_articles(self, query: str, max_pages: int = 1, **kwargs) -> ArticleResults:
        """搜索文章"""
        self._record_start()
        try:
            results = await self.execute(query, max_pages=max_pages, **kwargs)
            return results
        except Exception as e:
            logger.error(f"NewsPattern search failed: {e}")
            return ArticleResults(
                success=False,
                query=query,
                error_message=str(e),
                pattern_used="NewsPattern",
            )

    async def load_article(self, url: str) -> ArticleData:
        """加载文章正文"""
        try:
            await self._session.navigate(url)
            await self._wait.wait_for_network_idle(timeout=15.0)

            article = ArticleData(url=url, source_domain=self._domain)

            # 提取标题
            title_sel = self._get_selector("article_title")
            if title_sel:
                title_el = await self._session.query_selector(title_sel.value)
                if title_el:
                    article.title = (await title_el.get_text()).strip()

            # 提取作者
            author_sel = self._get_selector("article_author")
            if author_sel:
                author_el = await self._session.query_selector(author_sel.value)
                if author_el:
                    article.author = (await author_el.get_text()).strip()

            # 提取时间
            time_sel = self._get_selector("article_time")
            if time_sel:
                time_el = await self._session.query_selector(time_sel.value)
                if time_el:
                    article.publish_time = (await time_el.get_text()).strip()

            # 提取正文
            content_sel = self._get_selector("article_content")
            if content_sel:
                content_el = await self._session.query_selector(content_sel.value)
                if content_el:
                    article.content = (await content_el.get_text()).strip()

            return article
        except Exception as e:
            logger.error(f"Failed to load article {url}: {e}")
            return ArticleData(url=url, source_domain=self._domain)

    async def get_comments(self, url: str, max_comments: int = 50) -> List[Dict]:
        """获取评论列表（可选功能）"""
        comments = []
        try:
            # 导航到文章
            if url:
                await self._session.navigate(url)
                await self._wait.wait_for_network_idle(timeout=10.0)

            # 查找评论区域
            comment_sel = self._get_selector("comment_section")
            if not comment_sel:
                return comments

            comment_area = await self._session.query_selector(comment_sel.value)
            if not comment_area:
                return comments

            # 获取评论项
            comment_item_sel = self._get_selector("comment_item")
            if not comment_item_sel:
                comment_item_sel = type('obj', (object,), {'value': '.comment, .item'})()

            items = await comment_area.query_selector_all(comment_item_sel.value)
            
            for item in items[:max_comments]:
                try:
                    text_sel = self._get_selector("comment_text")
                    author_sel = self._get_selector("comment_author")
                    time_sel = self._get_selector("comment_time")

                    text = (await item.get_text(text_sel.value)) if text_sel else ""
                    author = (await item.get_text(author_sel.value)) if author_sel else ""
                    time = (await item.get_text(time_sel.value)) if time_sel else ""

                    comments.append({
                        "text": text.strip()[:500],
                        "author": author.strip()[:100],
                        "time": time.strip(),
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse comment: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to get comments: {e}")

        return comments

    async def execute(self, query: str, max_pages: int = 1, **kwargs) -> ArticleResults:
        """执行搜索流程（子类必须实现）"""
        raise NotImplementedError("Subclasses must implement execute()")

    async def _paginate(self, results: ArticleResults, max_pages: int) -> ArticleResults:
        """翻页获取更多结果"""
        all_articles = results.articles[:]

        for page in range(2, max_pages + 1):
            next_page_sel = self._get_selector("next_page")
            if not next_page_sel:
                break

            try:
                await self._session.click(next_page_sel.value)
                await self._wait.wait_for_network_idle(timeout=10.0)

                page_results = await self._parse_results(results.query)
                all_articles.extend(page_results.articles)

                if page_results.is_empty:
                    break

            except Exception as e:
                logger.warning(f"Pagination failed on page {page}: {e}")
                break

        results.articles = all_articles
        return results

    async def _parse_results(self, query: str) -> ArticleResults:
        """解析搜索结果（子类必须实现）"""
        raise NotImplementedError("Subclasses must implement _parse_results()")