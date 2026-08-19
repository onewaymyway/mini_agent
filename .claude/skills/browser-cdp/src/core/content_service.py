"""
Browser-CDP 内容搜索与详情展示模块
"""

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from enum import Enum


class ContentFormat(Enum):
    MARKDOWN = "markdown"
    HTML = "html"
    JSON = "json"
    TEXT = "text"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str
    category: str
    publish_time: Optional[str]
    author: Optional[str]
    score: float
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SearchResult':
        return cls(**data)


@dataclass
class ContentDetail:
    url: str
    title: str
    content: str
    format: str
    domain: str
    category: str
    author: Optional[str]
    publish_time: Optional[str]
    word_count: int
    images: List[str]
    links: List[str]
    extracted_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ContentDetail':
        return cls(**data)


class ContentParser:
    """内容解析器"""
    
    @classmethod
    def clean_html(cls, html: str) -> str:
        """清理HTML噪音"""
        patterns = [
            r'<script[^>]*>.*?</script>',
            r'<style[^>]*>.*?</style>',
            r'<nav[^>]*>.*?</nav>',
            r'<footer[^>]*>.*?</footer>',
        ]
        for p in patterns:
            html = re.sub(p, '', html, flags=re.DOTALL | re.IGNORECASE)
        return html
    
    @classmethod
    def html_to_markdown(cls, html: str) -> str:
        """HTML转Markdown"""
        text = cls.clean_html(html)
        text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<li[^>]*>(.*?)</li>', r'• \1\n', text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()
    
    @classmethod
    def extract_content(cls, html: str, fmt: ContentFormat = ContentFormat.MARKDOWN) -> str:
        if fmt == ContentFormat.MARKDOWN:
            return cls.html_to_markdown(html)
        elif fmt == ContentFormat.TEXT:
            return re.sub(r'[#*`\[\]()]', '', cls.html_to_markdown(html))
        return cls.clean_html(html)


class ContentDetailService:
    """内容详情服务"""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def get_or_fetch(self, url: str, force_refresh: bool = False) -> Optional[ContentDetail]:
        if not force_refresh:
            cached = self._load_cache(url)
            if cached:
                return cached
        return None
    
    def _load_cache(self, url: str) -> Optional[ContentDetail]:
        cache_file = self.cache_dir / f"{abs(hash(url))}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    return ContentDetail.from_dict(json.load(f))
            except Exception:
                return None
        return None
    
    def _save_cache(self, detail: ContentDetail):
        cache_file = self.cache_dir / f"{abs(hash(detail.url))}.json"
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(detail.to_dict(), f, ensure_ascii=False, indent=2)
    
    def display_result(self, result: SearchResult, verbose: bool = False) -> str:
        lines = [
            f"📄 {result.title}",
            f"   🔗 {result.url}",
            f"   📝 {result.snippet[:100]}{'...' if len(result.snippet) > 100 else ''}",
            f"   🏷️  分类: {result.category} | 域名: {result.domain}",
        ]
        if verbose:
            if result.author:
                lines.append(f"   👤 作者: {result.author}")
            if result.publish_time:
                lines.append(f"   📅 时间: {result.publish_time}")
            lines.append(f"   ⭐ 相关度: {result.score:.2f}")
        return '\n'.join(lines)
    
    def display_detail(self, detail: ContentDetail) -> str:
        lines = [
            f"# {detail.title}",
            f"\n🔗 {detail.url}\n",
            f"📝 字数: {detail.word_count} | 分类: {detail.category}\n",
            "---\n",
            detail.content[:2000],
        ]
        return '\n'.join(lines)
