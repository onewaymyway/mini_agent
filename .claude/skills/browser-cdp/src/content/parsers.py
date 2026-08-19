"""
parsers.py - 内容网站解析器

提供针对不同内容类型的文章解析能力：博客、新闻、知识库等。
"""
from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .models import (
    Article,
    ArticleSearchResults,
    Author,
    Category,
    ContentSiteProfile,
    Tag,
)

logger = logging.getLogger(__name__)


class BaseContentParser(ABC):
    """内容解析器基类"""

    # 默认选择器（子类可覆盖）
    SELECTORS = {
        "article_title":    "h1.article-title, .article-title, h1.entry-title, article h1",
        "article_content":  "div.article-content, .entry-content, article, .content-article",
        "article_author":   ".article-author, .author-name, .byline",
        "article_date":     ".article-date, .publish-date, time, .date",
        "article_tags":     ".article-tags, .tags, .post-tags a",
        "article_images":   ".article-content img, .entry-content img",
        "article_category": ".article-category, .post-category a",
        "related_articles": ".related-posts article, .related-articles .post",
        "next_page":        "a.next, .pagination-next, .next-page",
    }

    def __init__(self, domain: str, config: Optional[Dict] = None):
        self._domain = domain
        self._config = config or {}
        self._selectors = {**self.SELECTORS, **(config.get("selectors", {}) if config else {})}

    @property
    def domain(self) -> str:
        return self._domain

    @abstractmethod
    async def parse_article(self, html: str, url: str) -> Article:
        """解析单篇文章"""
        raise NotImplementedError

    @abstractmethod
    async def parse_list(self, html: str, url: str, page: int = 1) -> ArticleSearchResults:
        """解析文章列表"""
        raise NotImplementedError

    @abstractmethod
    def get_site_profile(self, html: str) -> ContentSiteProfile:
        """获取网站档案"""
        raise NotImplementedError

    # ─── 通用辅助方法 ───

    def _extract_text(self, html: str, selector: str) -> str:
        """从HTML中提取文本（使用简单正则，避免依赖BeautifulSoup）"""
        try:
            import re
            # 移除script和style标签
            clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
            # 提取目标选择器内容
            if 'class=' in selector:
                class_match = re.search(r'\.([\w-]+)', selector)
                if class_match:
                    cls = class_match.group(1)
                    pat = '<[^>]+(?:class|className)[^>]*="' + re.escape(cls) + '"[^>]*>(.*?)</[^>]+>'
                    match = re.search(pat, clean, re.DOTALL | re.IGNORECASE)
                    if match:
                        return re.sub(r'<[^>]+>', '', match.group(1)).strip()
            # 回退：提取第一个匹配的标签内容
            tag_pat = r'<(h[1-6]|div|span|p|article|section)[^>]*>.*?</\1>'
            tag_match = re.search(tag_pat, clean, re.DOTALL | re.IGNORECASE)
            if tag_match:
                return re.sub(r'<[^>]+>', '', tag_match.group(0)).strip()[:2000]
        except Exception as e:
            logger.debug(f"_extract_text failed: {e}")
        return ""

    def _extract_text_by_class(self, html: str, class_name: str) -> str:
        """按class名提取文本"""
        try:
            import re
            clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
            pattern = rf'<[^>]+(?:class|className)=["\'][^"\']*[\s]?{re.escape(class_name)}[\s]?[^"\']*["\']([^>]*)>[\s\S]*?</[^>]+>'
            match = re.search(pattern, clean, re.IGNORECASE)
            if match:
                return re.sub(r'<[^>]+>', '', match.group(1)).strip()[:2000]
        except Exception:
            pass
        return ""

    def _parse_author(self, html: str, selector: Optional[str] = None) -> Author:
        """解析作者信息"""
        author = Author()
        sel = selector or self._selectors.get("article_author", ".article-author, .author-name")
        text = self._extract_text(html, sel)
        if text:
            author.name = text[:100]
            author.username = text[:50]
        # 从页面meta提取
        author_match = re.search(r'<meta[^>]+property=["\']author["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if author_match:
            author.name = author_match.group(1).strip()
        return author

    def _parse_date(self, html: str, selector: Optional[str] = None) -> Optional[str]:
        """解析发布日期"""
        sel = selector or self._selectors.get("article_date", ".article-date, time")
        text = self._extract_text(html, sel)
        if not text:
            # 尝试JSON-LD
            jsonld_match = re.search(r'<script type=["\']application/ld\+json["\']>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
            if jsonld_match:
                date_match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', jsonld_match.group(1))
                if date_match:
                    return date_match.group(1)
        return text[:50] if text else ""

    def _parse_tags(self, html: str) -> List[Tag]:
        """解析标签"""
        tags = []
        tag_sel = self._selectors.get("article_tags", ".article-tags a, .tags a")
        text = self._extract_text(html, tag_sel)
        if text:
            for t in re.findall(r'["\'\>](\w[\w\-\u4e00-\u9fa5]{0,20})["\'\<]', text):
                tags.append(Tag(name=t.strip(), slug=t.strip().lower().replace(' ', '-')))
        return tags[:15]

    def _parse_images(self, html: str) -> List[str]:
        """解析图片列表"""
        images = []
        try:
            import re
            # 提取img标签的src
            src_matches = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)
            images.extend(src_matches[:20])
            # 提取open graph图片
            og_matches = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            images.extend(og_matches[:5])
        except Exception:
            pass
        return list(dict.fromkeys(images))  # 去重

    def _estimate_read_time(self, content: str) -> float:
        """估算阅读时间（分钟）"""
        words = len(content.split())
        return round(words / 200.0, 1)  # 假设200词/分钟

    def _estimate_word_count(self, content: str) -> int:
        """估算字数"""
        # 中文字符单独计数
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', content))
        en_words = len(re.findall(r'[a-zA-Z]+', content))
        return cn_chars + en_words

    def _record_latency(self, results: Any, start_time: float) -> Any:
        elapsed = (time.time() - start_time) * 1000
        if isinstance(results, ArticleSearchResults):
            results.latency_ms = round(elapsed, 2)
        return results


class BlogParser(BaseContentParser):
    """博客文章解析器"""

    SELECTORS = {
        **BaseContentParser.SELECTORS,
        "article_title":    "h1.entry-title, h1.article-title, .post-title, article h1",
        "article_content":  "div.entry-content, article.content, .post-content, .article-body",
        "article_author":   ".author-name, .byline a, .post-author",
        "article_date":     ".post-date, time.datetime, .publish-date",
        "article_tags":     ".post-tags a, .article-tags a, .tag-cloud a",
    }

    def __init__(self, domain: str, config: Optional[Dict] = None):
        config = config or {}
        config["site_type"] = "blog"
        super().__init__(domain, config)

    async def parse_article(self, html: str, url: str) -> Article:
        start = time.time()
        title = self._extract_text(html, self._selectors["article_title"])
        content = self._extract_text(html, self._selectors["article_content"])
        author = self._parse_author(html, self._selectors.get("article_author"))
        pub_date = self._parse_date(html, self._selectors.get("article_date"))
        tags = self._parse_tags(html)
        images = self._parse_images(html)
        word_count = self._estimate_word_count(content)

        article = Article(
            article_id=f"blog_{self._domain}_{len(url)}_{int(time.time())}",
            title=title or "未命名博客文章",
            url=url,
            content=content,
            excerpt=content[:300] if content else "",
            author=author,
            tags=tags,
            images=images,
            published_at=pub_date,
            source_domain=self._domain,
            word_count=word_count,
            read_time_minutes=self._estimate_read_time(content),
            scraped_at=datetime.now().isoformat(),
        )
        return self._record_latency(article, start)

    async def parse_list(self, html: str, url: str, page: int = 1) -> ArticleSearchResults:
        start = time.time()
        articles = []
        # 提取文章列表项
        item_pattern = re.findall(r'<article[^>]*>(.*?)</article>', html, re.DOTALL | re.IGNORECASE)
        for item_html in item_pattern[:20]:
            try:
                title = self._extract_text(item_html, self._selectors["article_title"])
                link_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', item_html)
                link = link_match.group(1) if link_match else ""
                if title and link:
                    articles.append(Article(
                        article_id=f"blog_{self._domain}_{len(articles)}",
                        title=title[:150],
                        url=link if link.startswith("http") else f"https://{self._domain}{link}",
                        source_domain=self._domain,
                    ))
            except Exception:
                continue
        return self._record_latency(
            ArticleSearchResults(success=True, query=url, articles=articles, site_domain=self._domain, parser_used="BlogParser"),
            start
        )

    def get_site_profile(self, html: str) -> ContentSiteProfile:
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
        return ContentSiteProfile(
            domain=self._domain,
            name=title_match.group(1).strip()[:100] if title_match else self._domain,
            site_type="blog",
            selectors=self._selectors,
        )


class NewsParser(BaseContentParser):
    """新闻资讯解析器"""

    SELECTORS = {
        **BaseContentParser.SELECTORS,
        "article_title":    "h1.headline, h1.article-title, .headline, article h1",
        "article_content":  "div.article-body, .story-content, article.content, .news-content",
        "article_author":   ".author, .byline, .news-author",
        "article_date":     ".pub-date, time, .news-date, .datePublished",
        "article_source":   ".source, .news-source, .media-name",
    }

    def __init__(self, domain: str, config: Optional[Dict] = None):
        config = config or {}
        config["site_type"] = "news"
        super().__init__(domain, config)

    async def parse_article(self, html: str, url: str) -> Article:
        start = time.time()
        title = self._extract_text(html, self._selectors["article_title"])
        content = self._extract_text(html, self._selectors["article_content"])
        author = self._parse_author(html, self._selectors.get("article_author"))
        pub_date = self._parse_date(html, self._selectors.get("article_date"))
        images = self._parse_images(html)
        source_match = self._extract_text(html, self._selectors.get("article_source", ".source"))

        article = Article(
            article_id=f"news_{self._domain}_{len(url)}_{int(time.time())}",
            title=title or "未命名新闻",
            url=url,
            content=content,
            excerpt=content[:300] if content else "",
            author=author,
            images=images,
            published_at=pub_date,
            source_domain=self._domain,
            source_type=ContentSource.ORIGINAL if not source_match else ContentSource.REPOST,
            word_count=self._estimate_word_count(content),
            read_time_minutes=self._estimate_read_time(content),
            scraped_at=datetime.now().isoformat(),
        )
        return self._record_latency(article, start)

    async def parse_list(self, html: str, url: str, page: int = 1) -> ArticleSearchResults:
        start = time.time()
        articles = []
        item_pattern = re.findall(r'<article[^>]*>(.*?)</article>', html, re.DOTALL | re.IGNORECASE)
        for item_html in item_pattern[:20]:
            try:
                title = self._extract_text(item_html, self._selectors["article_title"])
                link_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', item_html)
                link = link_match.group(1) if link_match else ""
                if title and link:
                    articles.append(Article(
                        article_id=f"news_{self._domain}_{len(articles)}",
                        title=title[:150],
                        url=link if link.startswith("http") else f"https://{self._domain}{link}",
                        source_domain=self._domain,
                    ))
            except Exception:
                continue
        return self._record_latency(
            ArticleSearchResults(success=True, query=url, articles=articles, site_domain=self._domain, parser_used="NewsParser"),
            start
        )

    def get_site_profile(self, html: str) -> ContentSiteProfile:
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
        return ContentSiteProfile(
            domain=self._domain,
            name=title_match.group(1).strip()[:100] if title_match else self._domain,
            site_type="news",
            selectors=self._selectors,
        )


class KnowledgeBaseParser(BaseContentParser):
    """知识库/教程解析器"""

    SELECTORS = {
        **BaseContentParser.SELECTORS,
        "article_title":    "h1.doc-title, h1.page-title, .content h1, article h1",
        "article_content":  "div.doc-content, .markdown-body, article.content, .kb-content",
        "article_toc":      ".toc, .table-of-contents, nav.toc",
        "article_version":  ".version, .doc-version, .kb-version",
    }

    def __init__(self, domain: str, config: Optional[Dict] = None):
        config = config or {}
        config["site_type"] = "knowledge_base"
        super().__init__(domain, config)

    async def parse_article(self, html: str, url: str) -> Article:
        start = time.time()
        title = self._extract_text(html, self._selectors["article_title"])
        content = self._extract_text(html, self._selectors["article_content"])
        author = self._parse_author(html)
        tags = self._parse_tags(html)
        images = self._parse_images(html)

        article = Article(
            article_id=f"kb_{self._domain}_{len(url)}_{int(time.time())}",
            title=title or "未命名文档",
            url=url,
            content=content,
            excerpt=content[:300] if content else "",
            author=author,
            tags=tags,
            images=images,
            source_domain=self._domain,
            content_type=ContentType.TUTORIAL,
            word_count=self._estimate_word_count(content),
            read_time_minutes=self._estimate_read_time(content),
            scraped_at=datetime.now().isoformat(),
        )
        return self._record_latency(article, start)

    async def parse_list(self, html: str, url: str, page: int = 1) -> ArticleSearchResults:
        start = time.time()
        articles = []
        # 知识库通常使用分类结构
        category_pattern = re.findall(r'<li[^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.DOTALL | re.IGNORECASE)
        for link, cat_text in category_pattern[:20]:
            try:
                cat_name = re.sub(r'<[^>]+>', '', cat_text).strip()[:100]
                articles.append(Article(
                    article_id=f"kb_{self._domain}_{len(articles)}",
                    title=cat_name or f"知识库分类{len(articles)+1}",
                    url=link if link.startswith("http") else f"https://{self._domain}{link}",
                    source_domain=self._domain,
                    categories=[Category(name=cat_name, slug=cat_name.lower().replace(' ', '-'))],
                ))
            except Exception:
                continue
        return self._record_latency(
            ArticleSearchResults(success=True, query=url, articles=articles, site_domain=self._domain, parser_used="KnowledgeBaseParser"),
            start
        )

    def get_site_profile(self, html: str) -> ContentSiteProfile:
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
        return ContentSiteProfile(
            domain=self._domain,
            name=title_match.group(1).strip()[:100] if title_match else self._domain,
            site_type="knowledge_base",
            selectors=self._selectors,
        )


# ─── 工厂函数 ───

_PARSER_REGISTRY: Dict[str, type] = {
    "blog":        BlogParser,
    "news":        NewsParser,
    "knowledge_base": KnowledgeBaseParser,
    "tutorial":    KnowledgeBaseParser,
    "docs":        KnowledgeBaseParser,
}


def create_parser_for_site(domain: str, site_type: Optional[str] = None, config: Optional[Dict] = None) -> BaseContentParser:
    """根据域名和类型创建合适的解析器"""
    config = config or {}
    if site_type and site_type in _PARSER_REGISTRY:
        parser_cls = _PARSER_REGISTRY[site_type]
    else:
        # 自动检测：根据域名关键词推断
        type_map = {
            "zhihu":      "knowledge_base",
            "juejin":     "blog",
            "csdn":       "blog",
            "cnblogs":    "blog",
            "segmentfault": "blog",
            "jianshu":    "blog",
            "toutiao":    "news",
            "sina":       "news",
            "163":        "news",
            "sohu":       "news",
            "github":     "knowledge_base",
            "stackoverflow": "knowledge_base",
            "docs":       "knowledge_base",
            "wiki":       "knowledge_base",
        }
        detected_type = "blog"
        for keyword, stype in type_map.items():
            if keyword in domain:
                detected_type = stype
                break
        parser_cls = _PARSER_REGISTRY.get(detected_type, BlogParser)
    return parser_cls(domain, config)


# 别名导出
BlogParserForAlias = BlogParser
NewsParserForAlias = NewsParser
KnowledgeBaseParserForAlias = KnowledgeBaseParser
