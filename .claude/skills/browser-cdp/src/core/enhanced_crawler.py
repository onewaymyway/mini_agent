"""
Enhanced Universal Crawler - 增强版通用爬虫系统
支持多种页面类型：文章、商品、图片、视频、招聘、新闻、博客、论坛等
"""
from __future__ import annotations

import json
import re
import time
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Tuple

logger = logging.getLogger(__name__)


class ContentType(Enum):
    """内容类型枚举"""
    ARTICLE = "article"
    PRODUCT = "product"
    IMAGE = "image"
    VIDEO = "video"
    JOB = "job"
    NEWS = "news"
    BLOG = "blog"
    FORUM = "forum"
    DOCUMENT = "document"
    EVENT = "event"
    PROFILE = "profile"
    UNKNOWN = "unknown"


class CrawlerStrategy(Enum):
    """爬取策略枚举"""
    STATIC = "static"
    SPA = "spa"
    INFINITE_SCROLL = "infinite_scroll"
    API_BASED = "api_based"
    HYBRID = "hybrid"


@dataclass
class CrawlConfig:
    """爬取配置"""
    wait_time: float = 2.0
    scroll_to_load: bool = False
    scroll_pages: int = 3
    timeout: int = 30
    retry_count: int = 3
    retry_delay: float = 1.0
    user_agent: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    cookies: Optional[Dict[str, str]] = None
    extraction_timeout: int = 10


@dataclass
class ContentExtraction:
    """内容提取结果"""
    content_type: ContentType
    title: str = ""
    author: str = ""
    publish_time: str = ""
    description: str = ""
    content: str = ""
    images: List[str] = field(default_factory=list)
    videos: List[Dict[str, str]] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    extra_data: Dict[str, Any] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content_type": self.content_type.value,
            "title": self.title,
            "author": self.author,
            "publish_time": self.publish_time,
            "description": self.description[:500] if self.description else "",
            "content_length": len(self.content),
            "images_count": len(self.images),
            "videos_count": len(self.videos),
            "links_count": len(self.links),
            "tags": self.tags,
            "metadata": self.metadata,
            "extra_data": self.extra_data,
        }


@dataclass
class CrawlResult:
    """爬取结果"""
    url: str
    success: bool
    content_type: Optional[ContentType] = None
    extraction: Optional[ContentExtraction] = None
    error: Optional[str] = None
    duration_ms: int = 0
    raw_html_size: int = 0
    crawled_at: str = ""
    strategy_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        if self.extraction:
            result["extraction"] = self.extraction.to_dict()
        result["crawled_at"] = self.crawled_at or datetime.now().isoformat()
        return result


class SelectorSet:
    """选择器集合"""

    ARTICLE_SELECTORS = {
        "title": ["h1.article-title", ".post-title", "h1.entry-title", ".article-title", "meta[property='og:title']"],
        "content": [".article-content", ".post-content", ".entry-content", "article", ".content"],
        "author": [".author", ".post-author", ".byline"],
        "publish_time": [".publish-time", ".post-date", "time[itemprop='datePublished']"],
    }

    PRODUCT_SELECTORS = {
        "title": [".product-title", ".item-name", ".goods-name"],
        "price": [".price", ".product-price", "[class*='price']"],
        "original_price": [".original-price", ".old-price"],
        "image": [".product-image", "img.product", "[itemprop='image']"],
    }

    IMAGE_SELECTORS = {
        "images": [".image-gallery img", ".photo img", "img[data-src]"],
    }

    VIDEO_SELECTORS = {
        "title": ["h1.video-title", ".video-title"],
        "duration": [".video-duration", ".duration"],
    }

    JOB_SELECTORS = {
        "title": [".job-title", ".position", "h1.job-title"],
        "company": [".company", ".employer"],
        "location": [".location", ".job-location"],
        "salary": [".salary", ".compensation"],
        "description": [".job-description", ".role-description"],
    }

    NEWS_SELECTORS = {
        "title": ["h1.news-title", ".headline"],
        "content": [".article-body", ".story-content"],
        "source": [".source", ".news-source"],
    }

    FORUM_SELECTORS = {
        "posts": [".post", ".reply", ".message"],
        "author": [".post-author", ".username"],
        "content": [".post-content", ".message-content"],
    }


class SmartExtractor:
    """智能内容提取器"""

    @classmethod
    def detect_content_type(cls, html: str, url: str) -> ContentType:
        """智能检测内容类型"""
        url_lower = url.lower()

        url_indicators = {
            ContentType.PRODUCT: ["/product/", "/item/", "/goods/", "/shop/"],
            ContentType.ARTICLE: ["/article/", "/post/", "/blog/"],
            ContentType.JOB: ["/job/", "/jobs/", "/career/"],
            ContentType.IMAGE: ["/photo/", "/image/", "/gallery/"],
            ContentType.VIDEO: ["/video/", "/watch/", "/play/"],
            ContentType.FORUM: ["/forum/", "/thread/", "/topic/"],
            ContentType.NEWS: ["/news/", "/daily/"],
        }

        for content_type, indicators in url_indicators.items():
            if any(ind in url_lower for ind in indicators):
                return content_type

        if "jobPosting" in html:
            return ContentType.JOB
        if "Product" in html and "offers" in html:
            return ContentType.PRODUCT
        if "Article" in html or "NewsArticle" in html:
            return ContentType.NEWS

        img_count = html.count("<img")
        if img_count > 20:
            return ContentType.IMAGE
        if "<video" in html or "youtube" in url_lower:
            return ContentType.VIDEO

        return ContentType.ARTICLE

    @classmethod
    def extract_from_html(cls, html: str, content_type: ContentType,
                          domain: str = "") -> ContentExtraction:
        """从HTML提取内容"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
        except ImportError:
            soup = cls._parse_html_fallback(html)

        extraction = ContentExtraction(content_type=content_type)

        if content_type == ContentType.ARTICLE:
            cls._extract_article(soup, extraction)
        elif content_type == ContentType.PRODUCT:
            cls._extract_product(soup, extraction)
        elif content_type == ContentType.IMAGE:
            cls._extract_images(soup, extraction)
        elif content_type == ContentType.VIDEO:
            cls._extract_video(soup, extraction)
        elif content_type == ContentType.JOB:
            cls._extract_job(soup, extraction)
        elif content_type == ContentType.NEWS:
            cls._extract_news(soup, extraction)
        elif content_type == ContentType.FORUM:
            cls._extract_forum(soup, extraction)
        else:
            cls._extract_generic(soup, extraction)

        cls._extract_metadata(soup, extraction)
        cls._extract_links(soup, extraction)

        return extraction

    @classmethod
    def _extract_article(cls, soup: Any, extraction: ContentExtraction):
        """提取文章内容"""
        for sel in SelectorSet.ARTICLE_SELECTORS["title"]:
            elem = soup.select_one(sel)
            if elem:
                extraction.title = elem.get_text(strip=True)
                if extraction.title:
                    break

        for sel in SelectorSet.ARTICLE_SELECTORS["content"]:
            elem = soup.select_one(sel)
            if elem:
                for tag in elem.select("script, style"):
                    tag.decompose()
                extraction.content = elem.get_text(strip=True)[:10000]
                if extraction.content:
                    break

        for sel in SelectorSet.ARTICLE_SELECTORS["author"]:
            elem = soup.select_one(sel)
            if elem:
                extraction.author = elem.get_text(strip=True)
                break

        for sel in SelectorSet.ARTICLE_SELECTORS["publish_time"]:
            elem = soup.select_one(sel)
            if elem:
                extraction.publish_time = elem.get_text(strip=True)
                break

        for img in soup.select("img"):
            src = img.get("src") or img.get("data-src")
            if src and src.startswith(("http://", "https://")):
                if src not in extraction.images:
                    extraction.images.append(src)

    @classmethod
    def _extract_product(cls, soup: Any, extraction: ContentExtraction):
        """提取商品信息"""
        for sel in SelectorSet.PRODUCT_SELECTORS["title"]:
            elem = soup.select_one(sel)
            if elem:
                extraction.title = elem.get_text(strip=True)
                break

        for sel in SelectorSet.PRODUCT_SELECTORS["price"]:
            elem = soup.select_one(sel)
            if elem:
                extraction.metadata["price"] = elem.get_text(strip=True)
                break

        for img in soup.select("img"):
            src = img.get("src") or img.get("data-src")
            if src and src.startswith(("http://", "https://")):
                if src not in extraction.images:
                    extraction.images.append(src)
                if len(extraction.images) >= 5:
                    break

    @classmethod
    def _extract_images(cls, soup: Any, extraction: ContentExtraction):
        """提取图片"""
        for sel in SelectorSet.IMAGE_SELECTORS["images"]:
            for img in soup.select(sel):
                src = img.get("src") or img.get("data-src") or img.get("data-original")
                if src and src.startswith(("http://", "https://", "//")):
                    if src not in extraction.images:
                        extraction.images.append(src)
                if len(extraction.images) >= 50:
                    return

    @classmethod
    def _extract_video(cls, soup: Any, extraction: ContentExtraction):
        """提取视频信息"""
        for sel in SelectorSet.VIDEO_SELECTORS["title"]:
            elem = soup.select_one(sel)
            if elem:
                extraction.title = elem.get_text(strip=True)
                break

        for video in soup.select("video, iframe"):
            src = video.get("src", "") or video.get("data-src", "")
            if src:
                extraction.videos.append({"url": src, "type": "iframe" if "iframe" in video.name else "video"})

    @classmethod
    def _extract_job(cls, soup: Any, extraction: ContentExtraction):
        """提取招聘信息"""
        for sel in SelectorSet.JOB_SELECTORS["title"]:
            elem = soup.select_one(sel)
            if elem:
                extraction.title = elem.get_text(strip=True)
                break

        for sel in SelectorSet.JOB_SELECTORS["company"]:
            elem = soup.select_one(sel)
            if elem:
                extraction.metadata["company"] = elem.get_text(strip=True)
                break

        for sel in SelectorSet.JOB_SELECTORS["description"]:
            elem = soup.select_one(sel)
            if elem:
                for tag in elem.select("script, style"):
                    tag.decompose()
                extraction.content = elem.get_text(strip=True)[:5000]
                break

    @classmethod
    def _extract_news(cls, soup: Any, extraction: ContentExtraction):
        """提取新闻"""
        for sel in SelectorSet.NEWS_SELECTORS["title"]:
            elem = soup.select_one(sel)
            if elem:
                extraction.title = elem.get_text(strip=True)
                break

        for sel in SelectorSet.NEWS_SELECTORS["content"]:
            elem = soup.select_one(sel)
            if elem:
                for tag in elem.select("script, style"):
                    tag.decompose()
                extraction.content = elem.get_text(strip=True)[:8000]
                break

    @classmethod
    def _extract_forum(cls, soup: Any, extraction: ContentExtraction):
        """提取论坛帖子"""
        post_count = 0
        for sel in SelectorSet.FORUM_SELECTORS["posts"]:
            for post in soup.select(sel)[:50]:
                post_data = {"author": "", "content": ""}

                for author_sel in SelectorSet.FORUM_SELECTORS["author"]:
                    author_elem = post.select_one(author_sel)
                    if author_elem:
                        post_data["author"] = author_elem.get_text(strip=True)
                        break

                for content_sel in SelectorSet.FORUM_SELECTORS["content"]:
                    content_elem = post.select_one(content_sel)
                    if content_elem:
                        post_data["content"] = content_elem.get_text(strip=True)[:2000]
                        break

                if post_data["content"]:
                    extraction.extra_data.setdefault("posts", []).append(post_data)
                    post_count += 1

        extraction.metadata["post_count"] = post_count

    @classmethod
    def _extract_generic(cls, soup: Any, extraction: ContentExtraction):
        """通用提取"""
        title = soup.title
        if title and title.string:
            extraction.title = title.string.strip()

        for sel in ["article", ".content", ".main", "main"]:
            elem = soup.select_one(sel)
            if elem:
                for tag in elem.select("script, style, nav, header, footer"):
                    tag.decompose()
                extraction.content = elem.get_text(strip=True)[:5000]
                if extraction.content:
                    break

    @classmethod
    def _extract_metadata(cls, soup: Any, extraction: ContentExtraction):
        """提取元数据"""
        og_tags = soup.select("meta[property^='og:']")
        for tag in og_tags:
            prop = tag.get("property", "")
            content = tag.get("content", "")
            if prop.startswith("og:"):
                extraction.metadata[prop[3:]] = content

        json_ld = soup.select("script[type='application/ld+json']")
        for tag in json_ld:
            try:
                data = json.loads(tag.string or "{}")
                extraction.metadata["structured_data"] = data
            except:
                pass

    @classmethod
    def _extract_links(cls, soup: Any, extraction: ContentExtraction):
        """提取链接"""
        seen = set()
        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
            if href and href not in ("#", "javascript:", "") and href not in seen:
                if len(href) < 200:
                    extraction.links.append(href)
                    seen.add(href)
            if len(extraction.links) >= 50:
                break

    @classmethod
    def _parse_html_fallback(cls, html: str) -> Any:
        """备用HTML解析"""
        try:
            from html.parser import HTMLParser
            parser = HTMLParser()
            parser.feed(html)
            return parser
        except:
            return None


class UniversalCrawler:
    """通用爬虫主类"""

    def __init__(self, browser_api, config: Optional[CrawlConfig] = None):
        self.browser = browser_api
        self.config = config or CrawlConfig()
        self.extractor = SmartExtractor()
        self.stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "by_type": {},
            "avg_duration_ms": 0
        }
        self._crawl_history: List[CrawlResult] = []

    def crawl(self, url: str, content_type: Optional[ContentType] = None,
              custom_selectors: Optional[Dict[str, Any]] = None,
              post_process: Optional[Callable] = None) -> CrawlResult:
        """爬取单个URL"""
        start_time = time.time()
        self.stats["total"] += 1

        try:
            # 导航到页面
            self.browser.goto(url)
            time.sleep(self.config.wait_time)

            # 获取HTML
            html = self.browser.get_html()
            raw_size = len(html.encode("utf-8"))

            # 检测内容类型
            if content_type is None:
                content_type = self.extractor.detect_content_type(html, url)

            # 执行智能提取
            extraction = self.extractor.extract_from_html(html, content_type)

            # 自定义后处理
            if custom_selectors:
                extraction = self._apply_custom_selectors(html, extraction, custom_selectors)
            if post_process:
                extraction = post_process(extraction)

            duration = int((time.time() - start_time) * 1000)

            result = CrawlResult(
                url=url,
                success=True,
                content_type=content_type,
                extraction=extraction,
                error=None,
                duration_ms=duration,
                raw_html_size=raw_size,
                crawled_at=datetime.now().isoformat(),
                strategy_used=CrawlerStrategy.STATIC.value
            )

            self._update_stats(result)
            self._crawl_history.append(result)

            return result

        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            logger.error(f"Crawl failed for {url}: {e}")

            result = CrawlResult(
                url=url,
                success=False,
                error=str(e),
                duration_ms=duration,
                crawled_at=datetime.now().isoformat()
            )

            self.stats["failed"] += 1
            self._crawl_history.append(result)

            return result

    def _apply_custom_selectors(self, html: str, extraction: ContentExtraction,
                                custom_selectors: Dict[str, Any]) -> ContentExtraction:
        """应用自定义选择器"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")

            for field, selectors in custom_selectors.items():
                if isinstance(selectors, list):
                    for sel in selectors:
                        elem = soup.select_one(sel)
                        if elem:
                            value = elem.get_text(strip=True)
                            if value:
                                setattr(extraction, field, value)
                                break
        except Exception as e:
            logger.warning(f"Custom selector application failed: {e}")

        return extraction

    def _update_stats(self, result: CrawlResult):
        """更新统计信息"""
        if result.success:
            self.stats["success"] += 1
            ct = result.content_type.value if result.content_type else "unknown"
            self.stats["by_type"][ct] = self.stats["by_type"].get(ct, 0) + 1

            total = self.stats["success"]
            avg = (self.stats["avg_duration_ms"] * (total - 1) + result.duration_ms) // total
            self.stats["avg_duration_ms"] = avg

    def batch_crawl(self, urls: List[str], delay: float = 1.0,
                    progress_callback: Optional[Callable] = None) -> List[CrawlResult]:
        """批量爬取"""
        results = []
        for i, url in enumerate(urls):
            result = self.crawl(url)
            results.append(result)

            if progress_callback:
                progress_callback(i + 1, len(urls), result)

            if delay > 0 and i < len(urls) - 1:
                time.sleep(delay)

        return results

    def crawl_with_retry(self, url: str, **kwargs) -> CrawlResult:
        """带重试的爬取"""
        last_error = None

        for attempt in range(self.config.retry_count):
            result = self.crawl(url, **kwargs)
            if result.success:
                return result
            last_error = result.error
            logger.warning(f"Attempt {attempt + 1} failed for {url}: {last_error}")
            if attempt < self.config.retry_count - 1:
                time.sleep(self.config.retry_delay * (attempt + 1))

        return CrawlResult(
            url=url,
            success=False,
            error=f"Failed after {self.config.retry_count} attempts: {last_error}",
            crawled_at=datetime.now().isoformat()
        )

    def get_stats(self) -> Dict[str, Any]:
        """获取爬虫统计"""
        return {
            **self.stats,
            "history_size": len(self._crawl_history),
            "success_rate": (
                self.stats["success"] / self.stats["total"] * 100
                if self.stats["total"] > 0 else 0
            )
        }

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取爬取历史"""
        return [r.to_dict() for r in self._crawl_history[-limit:]]

    def clear_history(self):
        """清空历史记录"""
        self._crawl_history.clear()


class MultiPageCrawler(UniversalCrawler):
    """多页面爬虫 - 支持分页和无限滚动"""

    def crawl_search_results(self, search_url: str, query: str,
                              max_pages: int = 5,
                              delay: float = 1.0) -> List[CrawlResult]:
        """爬取搜索结果（带分页）"""
        results = []

        for page in range(1, max_pages + 1):
            if "{page}" in search_url:
                page_url = search_url.format(page=page, query=query)
            elif "?" in search_url:
                page_url = f"{search_url}&page={page}&q={query}"
            else:
                page_url = f"{search_url}?q={query}&page={page}"

            result = self.crawl(page_url)
            results.append(result)

            if not result.success or self._is_last_page(result):
                break

            time.sleep(delay)

        return results

    def crawl_with_infinite_scroll(self, url: str, max_scrolls: int = 5,
                                    scroll_delay: float = 1.0) -> CrawlResult:
        """带无限滚动的爬取"""
        start_time = time.time()
        self.stats["total"] += 1

        try:
            self.browser.goto(url)
            time.sleep(self.config.wait_time)

            scroll_count = 0
            prev_item_count = 0

            for _ in range(max_scrolls):
                current_count = self._count_items()
                self.browser.execute_script("window.scrollBy(0, 800);")
                time.sleep(scroll_delay)
                new_count = self._count_items()

                if new_count == prev_item_count:
                    break

                prev_item_count = new_count
                scroll_count += 1

            html = self.browser.get_html()
            content_type = self.extractor.detect_content_type(html, url)
            extraction = self.extractor.extract_from_html(html, content_type)
            extraction.metadata["scroll_count"] = scroll_count

            duration = int((time.time() - start_time) * 1000)

            result = CrawlResult(
                url=url,
                success=True,
                content_type=content_type,
                extraction=extraction,
                duration_ms=duration,
                crawled_at=datetime.now().isoformat(),
                strategy_used=CrawlerStrategy.INFINITE_SCROLL.value
            )

            self._update_stats(result)
            return result

        except Exception as e:
            return CrawlResult(
                url=url,
                success=False,
                error=str(e),
                crawled_at=datetime.now().isoformat()
            )

    def _count_items(self) -> int:
        """计算当前页面项目数"""
        try:
            items = self.browser.find_elements(".item, .result, .post, .card")
            return len(items)
        except:
            return 0

    def _is_last_page(self, result: CrawlResult) -> bool:
        """检查是否最后一页"""
        if not result.extraction:
            return True
        try:
            next_page = self.browser.find_element(".next-page, .pagination-next")
            return next_page is None
        except:
            return False


# 便捷工厂函数
def create_crawler(browser_api, **kwargs) -> UniversalCrawler:
    """创建爬虫实例"""
    config = CrawlConfig(**kwargs) if kwargs else None
    return UniversalCrawler(browser_api, config)


def create_search_crawler(browser_api, **kwargs) -> MultiPageCrawler:
    """创建搜索爬虫实例"""
    config = CrawlConfig(**kwargs) if kwargs else None
    return MultiPageCrawler(browser_api, config)
