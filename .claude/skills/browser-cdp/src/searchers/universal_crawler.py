"""
Browser-CDP 通用爬虫系统
支持多种页面类型（文章、商品、图片等）的通用爬虫
"""

import json
import re
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime


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
    DOC = "document"


@dataclass
class CrawlResult:
    """爬取结果"""
    url: str
    content_type: str
    title: str
    content: str
    author: Optional[str]
    publish_time: Optional[str]
    images: List[str]
    links: List[str]
    metadata: Dict[str, Any]
    success: bool
    error: Optional[str]
    duration_ms: int
    crawled_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CrawlResult":
        return cls(**data)


class ContentExtractor:
    """内容提取器"""
    
    ARTICLE_SELECTORS = {
        "title": ["h1", ".article-title", ".post-title", "h1.entry-title"],
        "content": [".article-content", ".post-content", ".entry-content", "article"],
        "author": [".author", ".post-author", ".byline"],
        "publish_time": [".publish-time", ".date", ".time", ".post-date"],
    }
    
    PRODUCT_SELECTORS = {
        "title": [".product-title", ".item-name", ".goods-name", "h1.product"],
        "price": [".price", ".product-price", ".item-price", '[class*="price"]'],
        "original_price": [".original-price", ".old-price", ".list-price"],
        "image": [".product-image", ".item-image", "img.product"],
        "description": [".product-desc", ".item-desc", ".description"],
    }
    
    @classmethod
    def extract_article(cls, html: str, domain: str = "") -> Dict[str, Any]:
        """提取文章内容"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        
        result = {
            "title": "",
            "content": "",
            "author": "",
            "publish_time": "",
            "images": [],
            "links": []
        }
        
        # 提取标题
        for selector in cls.ARTICLE_SELECTORS["title"]:
            elem = soup.select_one(selector)
            if elem:
                result["title"] = elem.get_text(strip=True)
                if result["title"]:
                    break
        
        # 提取内容
        for selector in cls.ARTICLE_SELECTORS["content"]:
            elem = soup.select_one(selector)
            if elem:
                result["content"] = elem.get_text(strip=True)[:5000]
                if result["content"]:
                    break
        
        # 提取图片
        for img in soup.select("img"):
            src = img.get("src") or img.get("data-src")
            if src and src.startswith("http"):
                result["images"].append(src)
        
        return result
    
    @classmethod
    def extract_product(cls, html: str, domain: str = "") -> Dict[str, Any]:
        """提取商品信息"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        
        result = {
            "title": "",
            "price": "",
            "original_price": "",
            "images": [],
            "description": ""
        }
        
        # 提取标题
        for selector in cls.PRODUCT_SELECTORS["title"]:
            elem = soup.select_one(selector)
            if elem:
                result["title"] = elem.get_text(strip=True)
                if result["title"]:
                    break
        
        # 提取价格
        for selector in cls.PRODUCT_SELECTORS["price"]:
            elem = soup.select_one(selector)
            if elem:
                result["price"] = elem.get_text(strip=True)
                if result["price"]:
                    break
        
        # 提取图片
        for img in soup.select("img"):
            src = img.get("src") or img.get("data-src")
            if src and src.startswith("http"):
                result["images"].append(src)
        
        return result
    
    @classmethod
    def detect_content_type(cls, html: str, url: str) -> ContentType:
        """检测内容类型"""
        url_lower = url.lower()
        
        # 基于URL判断
        if any(kw in url_lower for kw in ["/p/", "/product/", "/item/", "/goods/"]):
            return ContentType.PRODUCT
        elif any(kw in url_lower for kw in ["/article/", "/post/", "/blog/", "/news/"]):
            return ContentType.ARTICLE
        elif any(kw in url_lower for kw in ["/job/", "/careers/"]):
            return ContentType.JOB
        elif any(kw in url_lower for kw in ["/image/", "/photo/", "/picture/"]):
            return ContentType.IMAGE
        
        # 基于HTML判断
        if "<img" in html and html.count("<img") > 10:
            return ContentType.IMAGE
        
        return ContentType.ARTICLE
    
    @classmethod
    def extract(cls, html: str, url: str, content_type: Optional[ContentType] = None) -> Dict[str, Any]:
        """通用内容提取"""
        if content_type is None:
            content_type = cls.detect_content_type(html, url)
        
        if content_type == ContentType.PRODUCT:
            return cls.extract_product(html, url)
        elif content_type == ContentType.IMAGE:
            return {"images": []}
        else:
            return cls.extract_article(html, url)


class DataStorage:
    """数据存储管道"""
    
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.cache = {}
    
    def save_result(self, result: CrawlResult, format: str = "json"):
        """保存爬取结果"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_url = re.sub(r"[/:*?\"<>|]", "_", result.url)[:100]
        filename = f"{safe_url}_{timestamp}.{format}"
        filepath = self.storage_dir / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        
        # 更新缓存
        self.cache[result.url] = result
    
    def load_result(self, url: str) -> Optional[CrawlResult]:
        """加载爬取结果"""
        if url in self.cache:
            return self.cache[url]
        
        # 从文件加载
        pattern = re.sub(r"[/:*?\"<>|]", "_", url)[:50]
        for f in self.storage_dir.glob(f"*{pattern}*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    return CrawlResult.from_dict(data)
            except Exception:
                continue
        return None
    
    def list_results(self, limit: int = 50) -> List[Dict]:
        """列出所有结果"""
        results = []
        for f in sorted(self.storage_dir.glob("*.json"), reverse=True)[:limit]:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    results.append({
                        "filename": f.name,
                        "url": data.get("url"),
                        "content_type": data.get("content_type"),
                        "title": data.get("title"),
                        "crawled_at": data.get("crawled_at")
                    })
            except Exception:
                continue
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        json_files = list(self.storage_dir.glob("*.json"))
        return {
            "total_results": len(json_files),
            "cache_size": len(self.cache),
            "storage_path": str(self.storage_dir)
        }


class UniversalCrawler:
    """通用爬虫"""
    
    def __init__(self, browser, storage_dir: Path):
        self.browser = browser
        self.storage = DataStorage(storage_dir)
        self.extractor = ContentExtractor()
        self.stats = {"total": 0, "success": 0, "failed": 0}
    
    def crawl(self, url: str, content_type: Optional[ContentType] = None, 
              wait_time: float = 2.0) -> CrawlResult:
        """爬取单个URL"""
        start_time = time.time()
        
        try:
            # 使用浏览器打开页面
            self.browser.goto(url)
            time.sleep(wait_time)
            
            # 获取页面内容
            html = self.browser.get_html()
            
            # 提取内容
            extracted = self.extractor.extract(html, url, content_type)
            
            duration = int((time.time() - start_time) * 1000)
            
            result = CrawlResult(
                url=url,
                content_type=content_type.value if content_type else "unknown",
                title=extracted.get("title", ""),
                content=extracted.get("content", ""),
                author=extracted.get("author"),
                publish_time=extracted.get("publish_time"),
                images=extracted.get("images", []),
                links=[],
                metadata=extracted,
                success=True,
                error=None,
                duration_ms=duration,
                crawled_at=datetime.now().isoformat()
            )
            
            # 保存结果
            self.storage.save_result(result)
            
            self.stats["total"] += 1
            self.stats["success"] += 1
            
            return result
            
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            result = CrawlResult(
                url=url,
                content_type=str(content_type) if content_type else "unknown",
                title="",
                content="",
                author=None,
                publish_time=None,
                images=[],
                links=[],
                metadata={},
                success=False,
                error=str(e),
                duration_ms=duration,
                crawled_at=datetime.now().isoformat()
            )
            
            self.stats["total"] += 1
            self.stats["failed"] += 1
            
            return result
    
    def batch_crawl(self, urls: List[str], delay: float = 1.0) -> List[CrawlResult]:
        """批量爬取"""
        results = []
        for url in urls:
            result = self.crawl(url)
            results.append(result)
            time.sleep(delay)
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """获取爬虫统计"""
        return {
            **self.stats,
            **self.storage.get_stats()
        }
