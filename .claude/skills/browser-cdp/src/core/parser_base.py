"""
解析框架基类 - 统一的页面解析接口

所有网站搜索器/爬虫的解析逻辑都继承自 BaseParser。
支持HTML解析、JSON解析、结构化数据提取。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """解析结果"""
    success: bool
    items: List[Dict[str, Any]] = field(default_factory=list)
    total_count: int = 0
    has_more: bool = False
    next_page_url: Optional[str] = None
    error: Optional[str] = None
    raw_data: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.items and not self.error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "items_count": len(self.items),
            "total_count": self.total_count,
            "has_more": self.has_more,
            "next_page_url": self.next_page_url,
            "error": self.error,
            "metadata": self.metadata,
        }


class BaseParser:
    """解析器基类 - 非抽象，可直接实例化用于测试"""

    # 通用选择器映射（子类可覆盖）
    SELECTORS: Dict[str, List[str]] = {}
    # 结构化数据CSS选择器
    SCHEMA_SELECTORS: Dict[str, str] = {}
    # JSON-LD提取键 - 默认覆盖常见结构化数据类型
    JSONLD_KEYS: List[str] = [
        '@type', 'name', 'headline', 'description', 'author',
        'datePublished', 'dateModified', 'publisher', 'image',
        'offers', 'aggregateRating', 'review', 'product', 'event'
    ]

    def __init__(self, **kwargs):
        self.config = kwargs
        self._html_cache: Optional[str] = None

    # -------------------- 入口方法 --------------------

    def parse(self, content: str, url: str = "", headers: Optional[Dict] = None) -> ParseResult:
        """主解析入口，统一处理各种输入格式"""
        self._html_cache = content
        result = self._do_parse(content, url, headers)
        result.raw_data = content
        result.metadata["parser"] = self.__class__.__name__
        result.metadata["url"] = url
        result.metadata["content_length"] = len(content)
        return result

    def _do_parse(self, content: str, url: str, headers: Optional[Dict]) -> ParseResult:
        """默认实现：简单提取标题和链接，子类可覆盖"""
        items = []
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, 'lxml')
            title_elem = soup.find('title')
            if title_elem:
                title = title_elem.get_text(strip=True)
                if title:
                    items.append({'title': title, 'url': url})
            links = self.extract_links(content, url)
            if links:
                items[0]['links'] = links[:20] if items else []
        except Exception as e:
            logger.debug(f"Default parse fallback: {e}")
        return ParseResult(success=len(items) > 0, items=items, total_count=len(items))

    # -------------------- 工具方法 --------------------

    @classmethod
    def extract_text(cls, element: Any, default: str = "") -> str:
        """安全提取文本"""
        if element is None:
            return default
        text = element.get_text(strip=True) if hasattr(element, 'get_text') else str(element)
        return text or default

    @classmethod
    def extract_attr(cls, element: Any, attr: str, default: str = "") -> str:
        """安全提取属性"""
        if element is None:
            return default
        if hasattr(element, 'get'):
            return element.get(attr, default)
        return default

    @classmethod
    def clean_text(cls, text: str, max_length: int = 50000) -> str:
        """清理文本：去除多余空白、控制长度

        P5修复：max_length 默认值从 5000 提升到 50000，避免长文章被截断。
        调用方可通过参数覆盖。
        """
        if not text:
            return ""
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        return text[:max_length] if len(text) > max_length else text

    @classmethod
    def extract_links(cls, html: str, base_url: str = "", max_count: int = 500) -> List[str]:
        """从HTML中提取所有链接（P4修复版）

        - 使用 BeautifulSoup 而非正则，避免属性顺序/嵌套问题
        - 过滤 javascript:/data: 等危险协议
        - 支持 href 出现在任意位置的 <a> 标签
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except Exception:
            # 降级到正则兜底
            return cls._extract_links_regex(html, base_url, max_count)

        links = []
        seen = set()
        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            if not href or href in ('#', '', 'javascript:', 'javascript:void(0)', 'data:', 'about:blank'):
                continue
            # 规范化相对路径
            if href.startswith('//'):
                href = 'https:' + href
            elif href.startswith('/') and base_url:
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            elif base_url and not href.startswith(('http://', 'https://')):
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            if href not in seen:
                seen.add(href)
                links.append(href)
            if len(links) >= max_count:
                break
        return links

    @classmethod
    def _extract_links_regex(cls, html: str, base_url: str = "", max_count: int = 500) -> List[str]:
        """正则兜底：匹配任意位置的 href 属性"""
        import re as _re
        pattern = r'<a\b[^>]*\bhref=["\']([^"\']*)["\'][^>]*>'
        links = []
        seen = set()
        for match in _re.finditer(pattern, html, _re.IGNORECASE):
            href = match.group(1).strip()
            if not href or href in ('#', '', 'javascript:', 'javascript:void(0)', 'data:', 'about:blank'):
                continue
            if href.startswith('//'):
                href = 'https:' + href
            elif href.startswith('/') and base_url:
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            elif base_url and not href.startswith(('http://', 'https://')):
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            if href not in seen:
                seen.add(href)
                links.append(href)
            if len(links) >= max_count:
                break
        return links

    @classmethod
    def extract_images(cls, html: str, max_count: int = 50) -> List[Dict[str, str]]:
        """从HTML中提取图片（含懒加载 data-src / data-lazy-src）

        P13相关：同时支持 src 和 data-src/data-lazy-src 属性。
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except Exception:
            return cls._extract_images_regex(html, max_count)

        images = []
        seen = set()
        for img in soup.find_all('img'):
            src = (img.get('src') or img.get('data-src') or img.get('data-lazy-src') or '').strip()
            if not src or src in ('about:blank', '', '#'):
                continue
            if src.startswith('//'):
                src = 'https:' + src
            if src not in seen:
                seen.add(src)
                images.append({'src': src})
            if len(images) >= max_count:
                break
        return images

    @classmethod
    def _extract_images_regex(cls, html: str, max_count: int = 50) -> List[Dict[str, str]]:
        """正则兜底：匹配 src / data-src / data-lazy-src"""
        import re as _re
        pattern = r'<img\b[^>]*\b(?:src|data-src|data-lazy-src)=["\']([^"\']*)["\'][^>]*>'
        images = []
        seen = set()
        for match in _re.finditer(pattern, html, _re.IGNORECASE):
            src = match.group(1).strip()
            if not src or src in ('about:blank', '', '#'):
                continue
            if src.startswith('//'):
                src = 'https:' + src
            if src not in seen:
                seen.add(src)
                images.append({'src': src})
            if len(images) >= max_count:
                break
        return images

    # -------------------- HTML解析（使用 BeautifulSoup）--------------------

    def _parse_html(self, html: str) -> Any:
        """解析HTML为BeautifulSoup对象"""
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html, 'lxml')
        except ImportError:
            logger.warning("BeautifulSoup未安装，返回None")
            return None

    def _find(self, soup: Any, selector: str) -> Any:
        """在soup中查找单个元素"""
        if soup is None:
            return None
        try:
            return soup.select_one(selector)
        except Exception:
            return None

    def _find_all(self, soup: Any, selector: str) -> List[Any]:
        """在soup中查找所有匹配元素"""
        if soup is None:
            return []
        try:
            return soup.select(selector)
        except Exception:
            return []

    def _extract_via_selectors(self, soup: Any, selectors: Dict[str, List[str]]) -> Dict[str, str]:
        """通过选择器字典提取字段"""
        result = {}
        for field_name, selector_list in selectors.items():
            for selector in selector_list:
                elem = self._find(soup, selector)
                if elem is not None:
                    text = self.extract_text(elem)
                    if text:
                        result[field_name] = text
                        break
        return result

    # -------------------- JSON-LD 解析 --------------------

    @staticmethod
    def _repair_jsonld(text: str) -> str:
        """修复含未转义双引号的 JSON-LD 字符串。

        策略：逐字符扫描，遇到字符串内部的未转义双引号时，
        通过向后查找后续分隔符（, } ] :）判断是合法边界还是内部引号。
        合法的字符串结束引号保持不变，内部未转义引号转为转义形式。"""
        text = text.strip()
        if not text or text[0] not in ('{', '['):
            return text
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        BS = chr(92)
        chars = list(text)
        n = len(chars)
        in_string = False
        i = 0
        while i < n:
            c = chars[i]
            if in_string:
                if c == BS and i + 1 < n:
                    i += 2
                elif c == '"':
                    rest = ''.join(chars[i + 1:]).lstrip()
                    if rest and rest[0] in (',', '}', ']', ':'):
                        in_string = False
                    else:
                        chars[i] = BS + '"'
                    i += 1
                else:
                    i += 1
            else:
                if c == '"':
                    in_string = True
                    i += 1
                else:
                    i += 1
        return ''.join(chars)

    def _extract_jsonld(self, html: str) -> Optional[Dict[str, Any]]:
        """从HTML中提取JSON-LD结构化数据（P1修复版）

        改进：使用 BeautifulSoup 而非正则解析 <script> 标签，避免嵌套 script 块导致的错误截断。
        同时支持多个 JSON-LD 块，优先返回包含 JSONLD_KEYS 中任一 key 的数据。
        新增：对含未转义双引号的 JSON-LD 进行自动修复后再解析。
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except Exception:
            return self._extract_jsonld_fallback(html)

        scripts = soup.find_all('script', {'type': 'application/ld+json'})
        if not scripts:
            return None

        for script in scripts:
            text = script.get_text(strip=True)
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                text = self._repair_jsonld(text)
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
            if isinstance(data, dict):
                for key in self.JSONLD_KEYS:
                    if key in data:
                        return data
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        for key in self.JSONLD_KEYS:
                            if key in item:
                                return item
        return None

    @classmethod
    def _extract_jsonld_fallback(cls, html: str) -> Optional[Dict[str, Any]]:
        """正则兜底：安全提取 JSON-LD（不使用非贪婪 .*?，改用手动扫描）

        新增：对解析失败的 JSON-LD 块调用 _repair_jsonld 进行修复后重试。
        """
        pattern = r'<script\s+(?:[^>]*\btype=["\']application/ld\+json["\'][^>]*)>([\s\S]*?)</script>'
        for match in re.finditer(pattern, html, re.IGNORECASE):
            text = match.group(1).strip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                text = cls._repair_jsonld(text)
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
            if isinstance(data, dict):
                for key in cls.JSONLD_KEYS:
                    if key in data:
                        return data
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        for key in cls.JSONLD_KEYS:
                            if key in item:
                                return item
        return None

    # -------------------- 正则提取 --------------------

    @classmethod
    def extract_by_regex(cls, html: str, patterns: Dict[str, str]) -> Dict[str, str]:
        """通过正则表达式提取字段"""
        result = {}
        for field_name, pattern in patterns.items():
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                result[field_name] = cls.clean_text(match.group(1) if match.lastindex else match.group(0))
        return result

    # -------------------- 分页检测 --------------------

    @classmethod
    def detect_pagination(cls, html: str, soup: Any, url: str) -> Tuple[bool, Optional[str]]:
        """检测是否存在下一页，返回 (has_more, next_page_url)"""
        # 常见分页选择器
        next_selectors = [
            'a[next]', 'a[rel="next"]', '.pagination .next',
            '.pager .next', 'a[href*="page=2"]', 'a[href*="pageNum"]',
            '.page-item.next a', '[data-page="2"]', '.load-more',
            '.infinite-scroll-button', 'button.load-more', 'a.more-btn',
        ]
        if soup:
            for selector in next_selectors:
                elem = soup.select_one(selector)
                if elem:
                    href = elem.get('href', '') or elem.get('data-url', '')
                    if href:
                        return True, href
                    if elem.get_text(strip=True).lower() in ('下一页', '加载更多', 'more', 'next'):
                        return True, None  # 动态加载
        return False, None

    # -------------------- 内容去重评分 --------------------

    @classmethod
    def text_similarity(cls, text1: str, text2: str) -> float:
        """计算两个文本的相似度（P3修复版）

        改进：
        - 对中文文本使用字符级 n-gram（bigram），而非空格分词
        - 对英文文本保留单词级 Jaccard 相似度
        - 混合文本自动检测并选择合适策略
        """
        if not text1 or not text2:
            return 0.0

        # 检测是否包含中文字符
        has_cn = bool(re.search(r'[\u4e00-\u9fff]', text1)) or bool(re.search(r'[\u4e00-\u9fff]', text2))

        if has_cn:
            # 中文：字符级 bigram Jaccard 相似度
            def _char_bigrams(t: str) -> set:
                t = t.lower().strip()
                return set(t[i:i+2] for i in range(len(t) - 1)) if len(t) >= 2 else {t}
            bg1, bg2 = _char_bigrams(text1), _char_bigrams(text2)
            if not bg1 or not bg2:
                return 0.0
            intersection = bg1 & bg2
            union = bg1 | bg2
            return len(intersection) / len(union) if union else 0.0
        else:
            # 英文：单词级 Jaccard 相似度
            words1 = set(text1.lower().split())
            words2 = set(text2.lower().split())
            if not words1 or not words2:
                return 0.0
            intersection = words1 & words2
            union = words1 | words2
            return len(intersection) / len(union) if union else 0.0

    @classmethod
    def deduplicate_items(cls, items: List[Dict[str, Any]], key: str = "title", threshold: float = 0.9) -> List[Dict[str, Any]]:
        """对结果去重"""
        if not items:
            return items
        seen_keys: set = set()
        unique_items = []
        for item in items:
            val = item.get(key, "")
            is_dup = False
            for seen in seen_keys:
                if cls.text_similarity(val, seen) >= threshold:
                    is_dup = True
                    break
            if not is_dup:
                unique_items.append(item)
                seen_keys.add(val)
        return unique_items

    # -------------------- 辅助方法 --------------------

    @classmethod
    def normalize_url(cls, url: str, base_url: str = "") -> str:
        """规范化URL"""
        if not url:
            return ""
        # 已经是完整URL
        if url.startswith("http://") or url.startswith("https://"):
            return url
        # 相对URL
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            if base_url:
                from urllib.parse import urljoin
                return urljoin(base_url, url)
            return f"https:{url}"
        if base_url:
            from urllib.parse import urljoin
            return urljoin(base_url, url)
        return url

    @classmethod
    def extract_domain(cls, url: str) -> str:
        """提取域名"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc.lower().replace("www.", "", 1)
        except Exception:
            return url

    def get_selector(self, field_name: str, default_selectors: Optional[List[str]] = None) -> List[str]:
        """获取字段的CSS选择器列表"""
        if field_name in self.SELECTORS:
            return self.SELECTORS[field_name]
        if default_selectors:
            return default_selectors
        return [f".{field_name}", f"#{field_name}", f"[class*='{field_name}']"]


class JsonParser(BaseParser):
    """JSON API响应解析器"""

    def _do_parse(self, content: str, url: str, headers: Optional[Dict] = None) -> ParseResult:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return ParseResult(success=False, error=f"JSON解析失败: {e}")

        items = self._extract_json_items(data)
        has_more, next_url = self._detect_json_pagination(data)

        return ParseResult(
            success=True,
            items=items,
            total_count=len(items),
            has_more=has_more,
            next_page_url=next_url,
            metadata={"data_type": type(data).__name__},
        )

    def _extract_json_items(self, data: Any) -> List[Dict[str, Any]]:
        """子类重写此方法提取列表数据"""
        return []

    def _detect_json_pagination(self, data: Any) -> Tuple[bool, Optional[str]]:
        """子类重写此方法检测分页"""
        return False, None


class SearchResultsParser(BaseParser):
    """搜索结果页解析器 - 通用实现"""

    DEFAULT_SELECTORS = {
        "title": ["h3 a", "h2 a", ".title a", ".result-title a", 'a[href*="/search"]'],
        "url": ["h3 a", "h2 a", ".title a", ".result-url"],
        "snippet": [".snippet", ".description", ".abstract", "p"],
        "site": [".site-name", ".domain", ".url-display"],
    }

    def __init__(self, custom_selectors: Optional[Dict] = None, **kwargs):
        super().__init__(**kwargs)
        self.SELECTORS = {**self.DEFAULT_SELECTORS, **(custom_selectors or {})}
        self.item_selector = self.config.get("item_selector", "")
        self.results_container = self.config.get("results_container", "")

    def _do_parse(self, content: str, url: str, headers: Optional[Dict] = None) -> ParseResult:
        soup = self._parse_html(content)
        if soup is None:
            return ParseResult(success=False, error="HTML解析失败")

        # 提取搜索结果
        items = []
        if self.item_selector:
            elements = self._find_all(soup, self.item_selector)
        else:
            # 自动检测搜索结果容器
            elements = self._find_all(soup, "article, .result, .search-result, li, .item")

        for elem in elements:
            item = self._extract_item(elem, url)
            if item:
                items.append(item)

        # 检测分页
        has_more, next_page = self.detect_pagination(content, soup, url)

        return ParseResult(
            success=True,
            items=items,
            total_count=len(items),
            has_more=has_more,
            next_page_url=next_page,
        )

    def _extract_item(self, elem: Any, base_url: str) -> Optional[Dict[str, Any]]:
        """从搜索结果条目中提取字段"""
        title_elem = self._find(elem, 'a[href]') or self._find(elem, 'h3 a, h2 a, .title a')
        title = self.extract_text(title_elem)
        href = self.extract_attr(title_elem, 'href', '') if title_elem else ''
        href = self.normalize_url(href, base_url)

        snippet_elem = self._find(elem, '.snippet, .description, p')
        snippet = self.extract_text(snippet_elem)

        return {
            "title": title,
            "url": href,
            "snippet": snippet,
        } if title else None


__all__ = ["ParseResult", "BaseParser", "JsonParser", "SearchResultsParser"]