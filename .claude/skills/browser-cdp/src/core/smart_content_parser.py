"""
smart_content_parser.py - 智能内容解析器

整合多种解析策略，自动选择最优解析路径：
1. JSON-LD / Microdata 结构化数据
2. OpenGraph / Twitter Card 元数据
3. data-* 属性数据
4. 内联 JS 变量
5. 表格数据
6. 通用 HTML 内容提取
7. 搜索结果提取
8. 文章/博客内容提取
9. 商品数据提取

目标：解析准确率提升至90%以上
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.core.parser_base import BaseParser, ParseResult
from src.core.structured_extractor import (
    StructuredDataExtractor,
    StructuredDataResult,
    ExtractedField,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 数据模型
# ============================================================================


@dataclass
class ContentItem:
    """解析出的内容项"""
    item_type: str  # 'article', 'search_result', 'product', 'video', 'user', 'table_row', etc.
    title: str = ""
    url: str = ""
    description: str = ""
    author: str = ""
    published_date: str = ""
    tags: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_type": self.item_type,
            "title": self.title,
            "url": self.url,
            "description": self.description,
            "author": self.author,
            "published_date": self.published_date,
            "tags": self.tags,
            "extra": self.extra,
            "confidence": self.confidence,
        }


@dataclass
class ParsingContext:
    """解析上下文"""
    html: str = ""
    url: str = ""
    headers: Optional[Dict] = None
    options: Dict[str, Any] = field(default_factory=dict)
    structured_data: Optional[StructuredDataResult] = None
    content_items: List[ContentItem] = field(default_factory=list)

    def add_item(self, item: ContentItem):
        self.content_items.append(item)

    def get_meta_field(self, name: str) -> Optional[str]:
        """从结构化数据中获取字段"""
        if not self.structured_data:
            return None
        return self.structured_data.get_field(name)

    def has_structured_data(self) -> bool:
        return self.structured_data is not None and len(self.structured_data.fields) > 0


# ============================================================================
# 智能内容解析器
# ============================================================================


class SmartContentParser(BaseParser):
    """
    智能内容解析器

    特性：
    1. 自动检测页面类型（文章/搜索结果/商品/视频等）
    2. 优先使用结构化数据（JSON-LD/Microdata）
    3. 降级到 HTML 解析和正则匹配
    4. 支持多策略结果融合
    5. 置信度评估
    """

    # 页面类型检测模式
    PAGE_TYPE_PATTERNS = [
        # 文章/博客
        (r'<article[^>]*>|<div[^>]*class=["\'][^"\']*(?:post|article|blog)[^"\']*["\']', 'article'),
        (r'application/ld\+json.*?"@type"\s*:\s*"Article"', 'article'),
        (r'application/ld\+json.*?"@type"\s*:\s*"BlogPosting"', 'article'),
        (r'<meta[^>]*property=["\']article:[^"\']*["\']', 'article'),

        # 搜索结果
        (r'<div[^>]*class=["\'][^"\']*(?:result|search-result|srp-results)[^"\']*["\']', 'search'),
        (r'application/ld\+json.*?"@type"\s*:\s*"ItemList"', 'search'),
        (r'<div[^>]*class=["\'][^"\']*(?:web-search|results-container)[^"\']*["\']', 'search'),

        # 商品
        (r'application/ld\+json.*?"@type"\s*:\s*"Product"', 'product'),
        (r'<meta[^>]*property=["\']product:[^"\']*["\']', 'product'),
        (r'<div[^>]*class=["\'][^"\']*(?:product|goods|item)[^"\']*["\']', 'product'),

        # 视频
        (r'application/ld\+json.*?"@type"\s*:\s*"VideoObject"', 'video'),
        (r'<meta[^>]*property=["\']video:[^"\']*["\']', 'video'),
        (r'<meta[^>]*property=["\']og:video[^"\']*["\']', 'video'),

        # 用户资料
        (r'application/ld\+json.*?"@type"\s*:\s*"Person"', 'profile'),
        (r'<meta[^>]*property=["\']profile:[^"\']*["\']', 'profile'),

        # 新闻
        (r'application/ld\+json.*?"@type"\s*:\s*"NewsArticle"', 'news'),
        (r'application/ld\+json.*?"@type"\s*:\s*"Journal"', 'news'),

        # 招聘信息
        (r'application/ld\+json.*?"@type"\s*:\s*"JobPosting"', 'job'),
        (r'<meta[^>]*property=["\']job:[^"\']*["\']', 'job'),
    ]

    # 文章正文提取选择器
    ARTICLE_SELECTORS = [
        'article', '.article-content', '.post-content', '.entry-content',
        '.article_body', '.content-area', '.main-content', '.article-text',
        '[class*="article"][class*="content"]', '[class*="post"][class*="content"]',
        'main article', 'main .content', '#article-content', '#post-content',
    ]

    # 搜索结果条目选择器
    SEARCH_ITEM_SELECTORS = [
        '.result-item', '.search-result', '.srp-result', '.search-result-item',
        '[class*="result"][class*="item"]', 'li.result', 'article.result',
        '.list-item', '.feed-item', '.content-item',
    ]

    # 商品数据选择器
    PRODUCT_SELECTORS = {
        'name': ['.product-name', '.product-title', '[class*="product-name"]', 'h1.product-title'],
        'price': ['.price', '.product-price', '[class*="price"]', '.selling-price'],
        'original_price': ['.original-price', '.list-price', '[class*="list-price"]'],
        'image': ['.product-image', '.product-img', 'img.product-image'],
        'brand': ['.brand', '.product-brand', '[class*="brand"]'],
        'rating': ['.rating', '.score', '[class*="rating"]'],
        'review_count': ['.review-count', '.comment-count', '[class*="review"]'],
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._structured_extractor = StructuredDataExtractor()
        self._page_type: Optional[str] = None
        self._page_type_confidence: float = 0.0

    # -------------------- 入口方法 --------------------

    def _do_parse(self, content: str, url: str, headers: Optional[Dict] = None) -> ParseResult:
        """Abstract BaseParser hook — delegates to SmartContentParser.parse()."""
        return self.parse(content, url=url, headers=headers)

    def parse(self, content: str, url: str = "", headers: Optional[Dict] = None) -> ParseResult:
        """主解析入口"""
        self._html_cache = content
        context = ParsingContext(
            html=content,
            url=url,
            headers=headers,
            options=self.config,
        )

        # 1. 检测页面类型
        self._detect_page_type(content)

        # 2. 提取结构化数据（最高优先级）
        context.structured_data = self._extract_structured_data(content, url)

        # 3. 根据页面类型选择解析策略
        if context.has_structured_data():
            # 有结构化数据：优先使用
            result = self._parse_with_structured_data(context)
        else:
            # 无结构化数据：使用 HTML 解析
            result = self._parse_without_structured_data(context)

        # 4. 后处理：去重、排序、格式化
        result = self._post_process(result, context)

        return result

    # -------------------- 页面类型检测 --------------------

    def _detect_page_type(self, html: str):
        """检测页面类型"""
        for pattern, page_type in self.PAGE_TYPE_PATTERNS:
            if re.search(pattern, html, re.IGNORECASE | re.DOTALL):
                self._page_type = page_type
                self._page_type_confidence = 0.9 if '@type' in pattern else 0.7
                logger.debug(f"检测到页面类型: {page_type} (置信度: {self._page_type_confidence})")
                return

        # 默认检测：根据 URL 路径
        self._page_type = self._detect_by_url()
        self._page_type_confidence = 0.5

    def _detect_by_url(self) -> str:
        """根据 URL 路径检测页面类型"""
        url = self._html_cache or ""
        if '/search' in url or '?q=' in url:
            return 'search'
        if '/article' in url or '/post' in url or '/blog' in url:
            return 'article'
        if '/product' in url or '/item' in url or '/goods' in url:
            return 'product'
        if '/video' in url or '/watch' in url:
            return 'video'
        if '/user' in url or '/profile' in url or '/u/' in url:
            return 'profile'
        return 'unknown'

    # -------------------- 结构化数据提取 --------------------

    def _extract_structured_data(self, html: str, url: str) -> StructuredDataResult:
        """提取结构化数据"""
        try:
            result = self._structured_extractor.extract(
                html,
                url=url,
                options=self.config.get('structured_options', {}),
            )
            logger.debug(f"结构化数据提取完成: {len(result.fields)} 个字段")
            return result
        except Exception as e:
            logger.warning(f"结构化数据提取失败: {e}")
            return StructuredDataResult(success=False, error=str(e))

    # -------------------- 基于结构化数据的解析 --------------------

    def _parse_with_structured_data(self, context: ParsingContext) -> ParseResult:
        """基于结构化数据解析"""
        items = []

        # 根据页面类型提取对应字段
        if context.structured_data:
            fields = {f.name: f.value for f in context.structured_data.fields}

            if self._page_type == 'article' or self._page_type == 'news':
                items.append(self._extract_article_from_fields(fields, context.url))
            elif self._page_type == 'product':
                items.append(self._extract_product_from_fields(fields, context.url))
            elif self._page_type == 'video':
                items.append(self._extract_video_from_fields(fields, context.url))
            elif self._page_type == 'job':
                items.append(self._extract_job_from_fields(fields, context.url))
            elif self._page_type == 'search':
                items.extend(self._extract_search_results_from_fields(fields, context.url))
            else:
                # 通用解析：提取所有已知字段
                items.append(self._extract_generic_item(fields, context.url))

        return ParseResult(
            success=len(items) > 0,
            items=items,
            total_count=len(items),
            has_more=False,
            metadata={
                'page_type': self._page_type,
                'page_type_confidence': self._page_type_confidence,
                'structured_data_used': True,
                'field_count': len(context.structured_data.fields) if context.structured_data else 0,
            },
        )

    # -------------------- 无结构化数据的 HTML 解析 --------------------

    def _parse_without_structured_data(self, context: ParsingContext) -> ParseResult:
        """无结构化数据时的 HTML 解析"""
        items = []

        if self._page_type == 'article' or self._page_type == 'news':
            items.append(self._extract_article_from_html(context.html, context.url))
        elif self._page_type == 'product':
            items.append(self._extract_product_from_html(context.html, context.url))
        elif self._page_type == 'video':
            items.append(self._extract_video_from_html(context.html, context.url))
        elif self._page_type == 'search':
            items = self._extract_search_results_from_html(context.html, context.url)
        elif self._page_type == 'job':
            items.append(self._extract_job_from_html(context.html, context.url))
        else:
            # 通用解析
            items.append(self._extract_generic_from_html(context.html, context.url))

        return ParseResult(
            success=len(items) > 0,
            items=items,
            total_count=len(items),
            has_more=False,
            metadata={
                'page_type': self._page_type,
                'structured_data_used': False,
            },
        )

    # -------------------- 文章解析 --------------------

    def _extract_article_from_fields(self, fields: Dict[str, str], url: str) -> ContentItem:
        """从结构化数据字段提取文章"""
        return ContentItem(
            item_type='article',
            title=fields.get('title') or fields.get('headline') or fields.get('name') or '',
            url=fields.get('url') or url,
            description=fields.get('description') or fields.get('abstract') or '',
            author=fields.get('author') or '',
            published_date=fields.get('datePublished') or fields.get('dateCreated') or '',
            tags=[fields.get('keywords', '').split(',')] if fields.get('keywords') else [],
            confidence=0.9,
        )

    def _extract_article_from_html(self, html: str, url: str) -> ContentItem:
        """从 HTML 提取文章内容"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return ContentItem(item_type='article', url=url, confidence=0.3)

        # 提取正文
        content = ''
        for selector in self.ARTICLE_SELECTORS:
            elem = soup.select_one(selector)
            if elem:
                content = elem.get_text(strip=True)
                break

        # 提取标题
        title = ''
        title_selectors = ['article h1', '.article-title', '.post-title', 'h1.entry-title', 'meta[property="og:title"]']
        for sel in title_selectors:
            elem = soup.select_one(sel)
            if elem:
                title = elem.get_text(strip=True) or elem.get('content', '')
                break
        if not title:
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text(strip=True)

        # 提取作者
        author = ''
        author_selectors = ['.author', '.article-author', '[rel="author"]', 'meta[name="author"]']
        for sel in author_selectors:
            elem = soup.select_one(sel)
            if elem:
                author = elem.get_text(strip=True) or elem.get('content', '')
                break

        return ContentItem(
            item_type='article',
            title=title,
            url=url,
            description=content[:500] if content else '',
            author=author,
            confidence=0.7 if content else 0.3,
            extra={'content_length': len(content)},
        )

    # -------------------- 商品解析 --------------------

    def _extract_product_from_fields(self, fields: Dict[str, str], url: str) -> ContentItem:
        """从结构化数据字段提取商品信息"""
        price = fields.get('offers.price') or fields.get('price') or ''
        original_price = fields.get('offers.highPrice') or fields.get('offers.lowPrice') or ''

        return ContentItem(
            item_type='product',
            title=fields.get('name') or fields.get('headline') or '',
            url=fields.get('url') or url,
            description=fields.get('description') or '',
            extra={
                'price': price,
                'original_price': original_price,
                'brand': fields.get('brand.name') or fields.get('brand') or '',
                'rating': fields.get('aggregateRating.ratingValue') or '',
                'review_count': fields.get('aggregateRating.reviewCount') or '',
            },
            confidence=0.9,
        )

    def _extract_product_from_html(self, html: str, url: str) -> ContentItem:
        """从 HTML 提取商品信息"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return ContentItem(item_type='product', url=url, confidence=0.3)

        # 提取名称
        title = ''
        for sel in self.PRODUCT_SELECTORS['name']:
            elem = soup.select_one(sel)
            if elem:
                title = elem.get_text(strip=True)
                if title:
                    break

        # 提取价格
        price = ''
        for sel in self.PRODUCT_SELECTORS['price']:
            elem = soup.select_one(sel)
            if elem:
                price = elem.get_text(strip=True)
                if price:
                    break

        # 提取图片
        image = ''
        for sel in self.PRODUCT_SELECTORS['image']:
            elem = soup.select_one(sel)
            if elem:
                image = elem.get('src', '') or elem.get('data-src', '')
                if image:
                    break

        return ContentItem(
            item_type='product',
            title=title,
            url=url,
            extra={
                'price': price,
                'image': image,
            },
            confidence=0.7 if title or price else 0.3,
        )

    # -------------------- 视频解析 --------------------

    def _extract_video_from_fields(self, fields: Dict[str, str], url: str) -> ContentItem:
        """从结构化数据字段提取视频信息"""
        return ContentItem(
            item_type='video',
            title=fields.get('name') or fields.get('headline') or '',
            url=fields.get('contentUrl') or fields.get('url') or url,
            description=fields.get('description') or '',
            extra={
                'duration': fields.get('duration') or '',
                'thumbnail': fields.get('thumbnailUrl') or '',
                'upload_date': fields.get('uploadDate') or '',
            },
            confidence=0.9,
        )

    def _extract_video_from_html(self, html: str, url: str) -> ContentItem:
        """从 HTML 提取视频信息"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return ContentItem(item_type='video', url=url, confidence=0.3)

        # 提取视频信息
        title = ''
        title_selectors = ['meta[property="og:video:title"]', 'meta[property="og:title"]', 'title']
        for sel in title_selectors:
            elem = soup.select_one(sel)
            if elem:
                title = elem.get('content', '') or elem.get_text(strip=True)
                if title:
                    break

        thumbnail = ''
        thumb_selectors = ['meta[property="og:video:image"]', 'meta[property="og:image"]']
        for sel in thumb_selectors:
            elem = soup.select_one(sel)
            if elem:
                thumbnail = elem.get('content', '')
                if thumbnail:
                    break

        return ContentItem(
            item_type='video',
            title=title,
            url=url,
            extra={'thumbnail': thumbnail},
            confidence=0.7 if title else 0.3,
        )

    # -------------------- 搜索解析 --------------------

    def _extract_search_results_from_fields(self, fields: Dict[str, str], url: str) -> List[ContentItem]:
        """从结构化数据字段提取搜索结果"""
        items = []

        # 查找列表项
        for key, value in fields.items():
            if key.startswith('itemListElement.') or '[0].' in key:
                # 尝试解析为单个结果
                item = ContentItem(
                    item_type='search_result',
                    title=value,
                    url=url,
                    confidence=0.7,
                )
                items.append(item)

        # 如果找不到结构化数据中的搜索结果，返回空列表
        if not items:
            items.append(ContentItem(
                item_type='search_result',
                url=url,
                confidence=0.3,
            ))

        return items

    def _extract_search_results_from_html(self, html: str, url: str) -> List[ContentItem]:
        """从 HTML 提取搜索结果"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return [ContentItem(item_type='search_result', url=url, confidence=0.3)]

        items = []

        for selector in self.SEARCH_ITEM_SELECTORS:
            elements = soup.select(selector)
            if elements:
                for elem in elements:
                    item = self._parse_search_item(elem, url)
                    if item and item.title:
                        items.append(item)
                if items:
                    break

        if not items:
            # 兜底：提取所有链接
            links = soup.find_all('a', href=True)
            for link in links[:20]:
                text = link.get_text(strip=True)
                if text and len(text) > 5:
                    items.append(ContentItem(
                        item_type='search_result',
                        title=text[:100],
                        url=link['href'],
                        confidence=0.4,
                    ))

        return items

    def _parse_search_item(self, elem, base_url: str) -> Optional[ContentItem]:
        """解析单个搜索结果项"""
        try:
            from bs4 import BeautifulSoup
            if not isinstance(elem, BeautifulSoup):
                from bs4 import BeautifulSoup
                elem_soup = BeautifulSoup(str(elem), 'lxml')
            else:
                elem_soup = elem

            # 提取标题
            title_elem = elem_soup.find('a') or elem_soup.find(['h1', 'h2', 'h3', 'h4'])
            if not title_elem:
                title_elem = elem_soup.find('a')
            title = self.extract_text(title_elem)

            # 提取 URL
            href = self.extract_attr(title_elem, 'href', '') if title_elem else ''
            if href and not href.startswith(('http://', 'https://')):
                from urllib.parse import urljoin
                href = urljoin(base_url, href)

            # 提取描述
            desc_elem = elem_soup.find(['p', '.snippet', '.description', '.abstract'])
            description = self.extract_text(desc_elem)

            if not title:
                return None

            return ContentItem(
                item_type='search_result',
                title=title[:200],
                url=href,
                description=description[:500],
                confidence=0.8,
            )
        except Exception as e:
            logger.debug(f"解析搜索结果项失败: {e}")
            return None

    # -------------------- 招聘信息解析 --------------------

    def _extract_job_from_fields(self, fields: Dict[str, str], url: str) -> ContentItem:
        """从结构化数据字段提取招聘信息"""
        return ContentItem(
            item_type='job',
            title=fields.get('title') or fields.get('headline') or '',
            url=fields.get('url') or url,
            description=fields.get('description') or '',
            extra={
                'company': fields.get('hiringOrganization.name') or fields.get('employer') or '',
                'location': fields.get('jobLocation') or '',
                'salary': fields.get('baseSalary') or '',
                'employment_type': fields.get('employmentType') or '',
                'date_posted': fields.get('datePosted') or '',
            },
            confidence=0.9,
        )

    def _extract_job_from_html(self, html: str, url: str) -> ContentItem:
        """从 HTML 提取招聘信息"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return ContentItem(item_type='job', url=url, confidence=0.3)

        title = self.extract_text(soup.find('h1')) or self.extract_text(soup.select_one('.job-title'))
        company = self.extract_text(soup.select_one('.company'))
        location = self.extract_text(soup.select_one('.location'))
        salary = self.extract_text(soup.select_one('.salary'))

        return ContentItem(
            item_type='job',
            title=title,
            url=url,
            extra={
                'company': company,
                'location': location,
                'salary': salary,
            },
            confidence=0.7 if title else 0.3,
        )

    # -------------------- 通用解析 --------------------

    def _extract_generic_item(self, fields: Dict[str, str], url: str) -> ContentItem:
        """通用内容提取"""
        return ContentItem(
            item_type='generic',
            title=fields.get('title') or fields.get('name') or '',
            url=fields.get('url') or url,
            description=fields.get('description') or '',
            author=fields.get('author') or '',
            confidence=0.6,
        )

    def _extract_generic_from_html(self, html: str, url: str) -> ContentItem:
        """通用 HTML 解析"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return ContentItem(item_type='generic', url=url, confidence=0.3)

        title = self.extract_text(soup.find('title')) or self.extract_text(soup.find('h1'))
        description = self.extract_text(soup.find('meta', {'name': 'description'})) or self.extract_text(soup.find('meta', {'property': 'og:description'}))

        return ContentItem(
            item_type='generic',
            title=title,
            url=url,
            description=description,
            confidence=0.5,
        )

    # -------------------- 后处理 --------------------

    def _post_process(self, result: ParseResult, context: ParsingContext) -> ParseResult:
        """后处理：去重、排序、格式化"""
        # 去重
        if result.items:
            result.items = self._deduplicate_items_by_title(result.items)

        # 排序（按置信度降序）
        result.items.sort(key=lambda x: x.confidence, reverse=True)

        # 格式化
        formatted_items = []
        for item in result.items:
            formatted_items.append(self._format_item(item))

        result.items = formatted_items
        return result

    def _deduplicate_items_by_title(self, items: List[ContentItem]) -> List[ContentItem]:
        """按标题去重"""
        seen_titles = set()
        unique_items = []
        for item in items:
            title_key = item.title.lower().strip()[:50]
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique_items.append(item)
        return unique_items

    def _format_item(self, item: ContentItem) -> Dict[str, Any]:
        """格式化输出项"""
        return item.to_dict()


# ============================================================================
# 便捷函数
# ============================================================================


def smart_parse(html: str, url: str = "", **kwargs) -> ParseResult:
    """便捷函数：智能解析"""
    parser = SmartContentParser(**kwargs)
    return parser.parse(html, url=url)


def parse_with_priority(html: str, url: str = "", priority: List[str] = None) -> ParseResult:
    """
    按优先级解析

    Args:
        html: HTML 内容
        url: 页面 URL
        priority: 解析策略优先级，如 ['jsonld', 'meta', 'html']
    """
    parser = SmartContentParser()
    parser.config['structured_options'] = {'methods': priority or ['jsonld', 'meta', 'data_attrs', 'inline_js']}
    return parser.parse(html, url=url)


__all__ = [
    "SmartContentParser",
    "ContentItem",
    "ParsingContext",
    "smart_parse",
    "parse_with_priority",
]
