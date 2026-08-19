"""
src/core/static_crawler.py

基础静态页面抓取器：HTML解析、DOM提取、数据格式化输出。
面向无反爬或弱反爬网站（政府/新闻/学术等）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


@dataclass
class ExtractedItem:
    """提取到的单条结构化数据"""
    title: str = ""
    url: str = ""
    snippet: str = ""
    author: str = ""
    publish_date: str = ""
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_url: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "author": self.author,
            "publish_date": self.publish_date,
            "content": self.content[:500] if self.content else "",
            "metadata": self.metadata,
            "source_url": self.source_url,
            "extracted_at": datetime.now().isoformat(),
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass
class CrawlResult:
    """抓取结果"""
    success: bool
    items: List[ExtractedItem] = field(default_factory=list)
    total_count: int = 0
    has_more: bool = False
    next_page_url: Optional[str] = None
    error: Optional[str] = None
    raw_html: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "items_count": len(self.items),
            "total_count": self.total_count,
            "has_more": self.has_more,
            "next_page_url": self.next_page_url,
            "error": self.error,
            "items": [item.to_dict() for item in self.items],
            "metadata": self.metadata,
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    def to_csv(self) -> str:
        if not self.items:
            return ""
        import csv
        import io
        output = io.StringIO()
        fieldnames = ["title", "url", "snippet", "author", "publish_date"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in self.items:
            writer.writerow(item.to_dict())
        return output.getvalue()
    
    def to_markdown(self) -> str:
        lines = [f"# 抓取结果 ({len(self.items)} 条)", ""]
        for i, item in enumerate(self.items, 1):
            lines.append(f"## {i}. {item.title}")
            lines.append(f"- **链接**: {item.url}")
            if item.snippet:
                lines.append(f"- **摘要**: {item.snippet[:200]}...")
            lines.append("")
        return "\n".join(lines)


class StaticCrawler:
    """
    基础静态页面抓取器。
    
    面向无反爬或弱反爬网站，提供：
    1. HTML 页面下载与缓存
    2. CSS 选择器 + XPath 混合提取
    3. JSON-LD / Schema.org 结构化数据提取
    4. 链接发现与分页处理
    5. 多种输出格式（JSON / CSV / Markdown）
    
    使用示例：
        crawler = StaticCrawler()
        result = await crawler.crawl("https://www.gov.cn", 
                                     selectors={"title": "h1", "link": "a"})
        print(result.to_json())
    """
    
    # 常用选择器预定义
    DEFAULT_SELECTORS = {
        # 通用文章结构
        "article_title": ["h1", "article h1", ".article-title", ".entry-title"],
        "article_content": ["article", ".article-content", ".entry-content", "#content"],
        "article_meta": ["meta[property='og:title']", 'meta[name="title"]'],
        "links": ["a[href]"],
        "images": ["img[src]"],
        "text": ["p", "div.content", ".article p"],
    }
    
    # JSON-LD 常见类型
    JSONLD_TYPES = [
        "NewsArticle", "Article", "WebPage", "WebSite",
        "Product", "Event", "Organization", "Person",
        "FAQPage", "HowTo", "BreadcrumbList",
    ]
    
    def __init__(self, timeout: float = 30.0, encoding: str = "utf-8",
                 follow_links: bool = False, max_pages: int = 5):
        self._timeout = timeout
        self._encoding = encoding
        self._follow_links = follow_links
        self._max_pages = max_pages
        self._html_cache: Dict[str, str] = {}
        self._visited_urls: set = set()
    
    async def fetch_page(self, url: str, headers: Optional[Dict] = None) -> Optional[str]:
        """获取页面 HTML"""
        if url in self._html_cache:
            return self._html_cache[url]
        
        try:
            import aiohttp
            session_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            if headers:
                session_headers.update(headers)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=session_headers, 
                                       timeout=aiohttp.ClientTimeout(total=self._timeout)) as resp:
                    if resp.status == 200:
                        html = await resp.text(encoding=self._encoding)
                        self._html_cache[url] = html
                        return html
                    else:
                        logger.warning(f"HTTP {resp.status} for {url}")
                        return None
        except Exception as e:
            logger.error(f"Fetch error for {url}: {e}")
            return None
    
    async def crawl(self, url: str, 
                    selectors: Optional[Dict[str, str]] = None,
                    extract_links: bool = True,
                    output_format: str = "json") -> CrawlResult:
        """
        抓取单个页面并提取数据。
        
        Args:
            url: 目标 URL
            selectors: 自定义选择器映射 {name: css_selector}
            extract_links: 是否提取页面内链接
            output_format: 输出格式 (json/csv/markdown)
        
        Returns:
            CrawlResult
        """
        html = await self.fetch_page(url)
        if not html:
            return CrawlResult(success=False, error=f"无法获取页面: {url}")
        
        self._visited_urls.add(url)
        result = CrawlResult(success=True, raw_html=html, metadata={"url": url})
        
        # 使用默认选择器或自定义选择器
        sel = selectors or self.DEFAULT_SELECTORS
        items = self._parse_html(html, url, sel)
        result.items = items
        result.total_count = len(items)
        
        # 提取链接
        if extract_links:
            links = self._extract_links(html, url)
            result.metadata["links_found"] = len(links)
            result.metadata["sample_links"] = links[:10]
        
        # 提取 JSON-LD
        jsonld = self._extract_jsonld(html)
        if jsonld:
            result.metadata["jsonld"] = jsonld
        
        # 检测分页
        next_page = self._detect_next_page(html, url)
        if next_page:
            result.has_more = True
            result.next_page_url = next_page
        
        # 格式化输出
        if output_format in ("csv", "markdown"):
            result.metadata["formatted_output"] = getattr(result, f"to_{output_format}")()
        
        return result
    
    def _parse_html(self, html: str, source_url: str, 
                    selectors: Dict[str, str]) -> List[ExtractedItem]:
        """解析 HTML 并提取结构化数据"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        items = []
        
        # 尝试提取文章列表
        article_selectors = [
            "article", ".article", ".post", ".news-item", 
            ".list-item", "li[class*='item']", ".result-item",
            'div[class*="article"]', 'div[class*="post"]',
        ]
        
        articles = []
        for sel in article_selectors:
            found = soup.select(sel)
            if found:
                articles = found
                break
        
        if not articles:
            # 没有文章列表，将整个页面视为单条内容
            articles = [soup]
        
        for article in articles:
            item = ExtractedItem(source_url=source_url)
            
            # 标题
            for sel in selectors.get("article_title", ["h1", ".title", "[property='og:title']"]):
                el = article.select_one(sel)
                if el:
                    text = el.get_text(strip=True)
                    if text and len(text) > 3:
                        item.title = text
                        break
            
            # 链接
            for sel in ["a[href]", 'link[rel="canonical"]']:
                el = article.select_one(sel)
                if el:
                    href = el.get("href", "") or el.get("content", "")
                    if href and href != item.url:
                        item.url = urljoin(source_url, href)
                        break
            
            # 摘要
            for sel in selectors.get("article_content", [".summary", ".excerpt", "meta[name='description']", "meta[property='og:description']"]):
                el = article.select_one(sel)
                if el:
                    if el.name == "meta":
                        item.snippet = el.get("content", "")
                    else:
                        text = el.get_text(strip=True)[:300]
                        if text:
                            item.snippet = text
                    break
            
            # 作者
            for sel in ["meta[name='author']", "[property='article:author']", ".author", ".byline"]:
                el = article.select_one(sel)
                if el:
                    if el.name == "meta":
                        item.author = el.get("content", "")
                    else:
                        item.author = el.get_text(strip=True)
                    break
            
            # 发布日期
            for sel in ["meta[property='article:published_time']", "time", ".date", ".publish-time"]:
                el = article.select_one(sel)
                if el:
                    if el.name == "meta":
                        item.publish_date = el.get("content", "")
                    else:
                        item.publish_date = el.get("datetime", el.get_text(strip=True))
                    break
            
            # 正文
            for sel in selectors.get("article_content", ["article", ".content", ".entry-content"]):
                el = article.select_one(sel)
                if el:
                    item.content = el.get_text(strip=True)[:2000]
                    break
            
            if item.title or item.url:
                items.append(item)
        
        return items
    
    def _extract_links(self, html: str, base_url: str) -> List[Dict]:
        """提取页面内所有链接"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(("http://", "https://")):
                links.append({"text": a.get_text(strip=True), "url": href})
            elif href.startswith("/"):
                links.append({"text": a.get_text(strip=True), "url": urljoin(base_url, href)})
        return links[:50]  # 限制数量
    
    def _extract_jsonld(self, html: str) -> List[Dict]:
        """提取 JSON-LD 结构化数据"""
        import re
        jsonld_blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
        results = []
        for block in jsonld_blocks:
            try:
                data = json.loads(block)
                if isinstance(data, dict) and data.get("@type") in self.JSONLD_TYPES:
                    results.append(data)
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") in self.JSONLD_TYPES:
                            results.append(item)
            except json.JSONDecodeError:
                continue
        return results
    
    def _detect_next_page(self, html: str, current_url: str) -> Optional[str]:
        """检测下一页链接"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        
        # 常见分页选择器
        selectors = [
            'a[rel="next"]',
            '.pagination .next a',
            '.page-next a',
            '[aria-label="Next"]',
            '.next-page a',
        ]
        
        for sel in selectors:
            el = soup.select_one(sel)
            if el and el.get("href"):
                next_url = urljoin(current_url, el["href"])
                if next_url not in self._visited_urls:
                    return next_url
        
        # 查找包含 "下一页" / "next" 文字的链接
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if any(k in text for k in ["下一页", "next", "下页", "后页"]):
                next_url = urljoin(current_url, a["href"])
                if next_url not in self._visited_urls:
                    return next_url
        
        return None
    
    async def crawl_multiple(self, urls: List[str], 
                             selectors: Optional[Dict] = None,
                             concurrency: int = 3) -> List[CrawlResult]:
        """并发抓取多个页面"""
        semaphore = asyncio.Semaphore(concurrency)
        
        async def _crawl_one(url: str) -> CrawlResult:
            async with semaphore:
                return await self.crawl(url, selectors=selectors)
        
        tasks = [asyncio.create_task(_crawl_one(u)) for u in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_stats(self) -> Dict:
        return {
            "cached_pages": len(self._html_cache),
            "visited_urls": len(self._visited_urls),
            "encoding": self._encoding,
            "timeout": self._timeout,
        }


__all__ = ["StaticCrawler", "ExtractedItem", "CrawlResult"]
