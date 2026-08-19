"""
ArticleParser - 文章页面专用解析器

支持多种新闻/博客平台的文章页面解析，自动检测类型并提取结构化数据。
集成到CrawlScheduler管道中使用。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ==================== 数据模型 ====================

@dataclass
class ArticleContent:
    """文章内容数据"""
    title: str = ""
    author: str = ""
    publish_time: str = ""
    source: str = ""
    content: str = ""
    summary: str = ""
    tags: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    canonical_url: str = ""
    word_count: int = 0
    reading_time_min: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "publish_time": self.publish_time,
            "source": self.source,
            "summary": self.summary[:500] if self.summary else "",
            "content_length": len(self.content),
            "word_count": self.word_count,
            "reading_time_min": self.reading_time_min,
            "images_count": len(self.images),
            "tags": self.tags,
            "canonical_url": self.canonical_url,
            "metadata": self.metadata,
        }


@dataclass
class ArticleParseResult:
    """文章解析结果"""
    success: bool
    article: Optional[ArticleContent] = None
    error: Optional[str] = None
    detected_type: str = "unknown"
    parse_time_ms: int = 0
    raw_html_size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "success": self.success,
            "detected_type": self.detected_type,
            "parse_time_ms": self.parse_time_ms,
            "raw_html_size": self.raw_html_size,
            "error": self.error,
        }
        if self.article:
            result["article"] = self.article.to_dict()
        return result


# ==================== 检测器 ====================

class ArticleTypeDetector:
    """文章类型检测器"""

    # 平台检测规则（P6扩展：增加更多中文网站）
    PLATFORM_PATTERNS = {
        # 原有平台
        "zhihu": re.compile(r"zhihu\.com/question|zhihu\.com/p/"),
        "weibo": re.compile(r"weibo\.com/[\w-]+/[\w-]+"),
        "jianshu": re.compile(r"jianshu\.com/p/"),
        "csdn": re.compile(r"csdn\.net/[\w-]+/article/details/"),
        "juejin": re.compile(r"juejin\.cn/post/"),
        "segmentfault": re.compile(r"segmentfault\.com/a/"),
        "ifeng": re.compile(r"news\.ifeng\.com/"),
        "sohu": re.compile(r"sohu\.com/a/"),
        "163": re.compile(r"163\.com/dy/article/"),
        "sina": re.compile(r"finance\.sina\.com\.cn/"),
        "thepaper": re.compile(r"thepaper\.cn/newsDetail_forward_"),
        "cls": re.compile(r"cls\.cn/show/(\d+)|news\.cls\.cn/"),
        "eastmoney": re.compile(r"eastmoney\.com/(a|news)/"),
        "xueqiu": re.compile(r"xueqiu\.com/\d+"),
        "techcrunch": re.compile(r"techcrunch\.com/[\w-]+/"),
        "reuters": re.compile(r"reuters\.com/"),
        "nytimes": re.compile(r"nytimes\.com/"),
        "bbc": re.compile(r"bbc\.com/news/"),
        # P6新增：百度健康、健康之路、家庭医生、华律网等
        "baidu_health": re.compile(r"jiankang\.baidu\.com/"),
        "yihu": re.compile(r"yihu\.com/"),
        "familydoctor": re.compile(r"familydoctor\.com\.cn/"),
        "cnlawyer": re.compile(r"cnlawyer\.com/"),
        "haolvshi": re.compile(r"haolvshi\.com/"),
        "acla": re.compile(r"chinaac\.la/"),
        "ths": re.compile(r"10\.js\.cn|ths\.com\.cn"),
        "dxy": re.compile(r"dxy\.com/"),
        "medsci": re.compile(r"medsci\.cn/"),
        # 其他常见中文网站
        "toutiao": re.compile(r"toutiao\.com/"),
        "sogou": re.compile(r"sogou\.com/"),
        "baidu": re.compile(r"baike\.baidu\.com|tieba\.baidu\.com"),
        "qq_news": re.compile(r"new\.qq\.com/"),
        "netease_news": re.compile(r"news\.163\.com/"),
        "guancha": re.compile(r"guancha\.cn/"),
        "huanqiu": re.compile(r"huanqiu\.com/"),
        # 视频/直播类
        "bilibili": re.compile(r"bilibili\.com/video"),
        "youtube": re.compile(r"youtube\.com/watch"),
        # 电商类
        "jd": re.compile(r"item\.jd\.com/"),
        "taobao": re.compile(r"item\.taobao\.com|detail\.tmall\.com"),
        "pdd": re.compile(r"mobile\.pinduoduo\.com/detail\.html"),
    }

    # JSON-LD 类型检测
    JSONLD_ARTICLE_TYPES = ["NewsArticle", "BlogPosting", "Article", "TechArticle", "ScholarlyArticle"]

    @classmethod
    def detect_platform(cls, url: str, html: str) -> str:
        """检测文章所属平台"""
        # 先检查URL
        for platform, pattern in cls.PLATFORM_PATTERNS.items():
            if pattern.search(url):
                return platform
        # 再检查JSON-LD
        jsonld_match = re.search(r'<script type="application/ld\+json">([\s\S]*?)</script>', html)
        if jsonld_match:
            try:
                ld_data = json.loads(jsonld_match.group(1))
                type_str = ld_data.get("@type", "")
                if isinstance(type_str, list):
                    type_str = type_str[0]
                for atype in cls.JSONLD_ARTICLE_TYPES:
                    if atype.lower() in type_str.lower():
                        return "jsonld_article"
            except json.JSONDecodeError:
                pass
        # 检查meta标签
        if re.search(r'meta\s+name="theme-color"', html, re.I):
            return "unknown_blog"
        return "generic_article"

    @classmethod
    def get_selectors(cls, platform: str) -> Dict[str, List[str]]:
        """获取平台的CSS选择器"""
        return PLATFORM_SELECTORS.get(platform, DEFAULT_ARTICLE_SELECTORS)


# ==================== 选择器定义 ====================

DEFAULT_ARTICLE_SELECTORS = {
    "title": [
        "h1.article-title", "h1.post-title", "h1.entry-title",
        "meta[property='og:title']", "meta[name='twitter:title']",
        "h1", ".article-header h1", "title",
    ],
    "content": [
        ".article-content", ".post-content", ".entry-content",
        ".article-body", ".story-content", "article", ".content",
        ".post-body", ".article-body-content",
    ],
    "author": [
        ".article-author", ".post-author", ".byline",
        "meta[name='author']", ".author-name",
        "[itemprop='author']",
    ],
    "publish_time": [
        ".publish-time", ".post-date", ".article-date",
        "time[itemprop='datePublished']",
        "meta[property='article:published_time']",
    ],
    "summary": [
        "meta[name='description']", "meta[property='og:description']",
        ".article-summary", ".excerpt", ".lead",
    ],
    "tags": [
        ".tags a", ".tag a", ".article-tags a",
        "[rel='tag']",
    ],
}

PLATFORM_SELECTORS = {
    "zhihu": {
        "title": [
            "h1.QuestionHeader-title", ".QuestionHeader-title",
            "meta[property='og:title']",
        ],
        "content": [
            ".RichContent-inner", ".ContentItem-richText",
            ".Post-RichText",
        ],
        "author": [
            ".Author-info-name", ".ContentItem-authorName",
            ".AuthorInfo-name",
        ],
        "publish_time": [
            ".ContentItem-time", ".AuthorInfo-createTime",
        ],
        "summary": [
            ".RichContent-excerpt", "meta[name='description']",
        ],
    },
    "jianshu": {
        "title": ["h1.title", "meta[property='og:title']"],
        "content": [".show-content-only", ".article"],
        "author": [".author-name", ".name"],
        "publish_time": [".publish-time", ".meta-firstLine"],
        "summary": ["meta[name='description']"],
    },
    "csdn": {
        "title": [
            "h1.article_title", ".article_title",
            "meta[property='og:title']",
        ],
        "content": [
            "#content_views", ".article_content",
            ".markdown_views",
        ],
        "author": [
            ".article_name", ".user-name",
        ],
        "publish_time": [
            ".time", ".article_date",
        ],
        "tags": [
            ".tag-article", ".article-tag",
        ],
    },
    "juejin": {
        "title": ["h1.article-title", ".article-title"],
        "content": [".article-content", ".markdown-body"],
        "author": [".author-name", ".name"],
        "publish_time": [".publish-time", ".time"],
    },
    "thepaper": {
        "title": ["h1.article-title", ".article-title"],
        "content": [".article-content", ".newsBody"],
        "author": [".article-author", ".author"],
        "publish_time": [".article-time", ".pubtime"],
        "source": [".article-source", ".source"],
    },
    "sina": {
        "title": ["h1.article_title", ".artical-title"],
        "content": ["#artical_content", ".artical-content", ".article"],
        "author": [".article-author", ".source"],
        "publish_time": [".article-time", ".time"],
    },
    "eastmoney": {
        "title": ["h1.articletitle", ".articletitle"],
        "content": ["#content", ".article-content", ".right_cont"],
        "author": [".article-source", ".author"],
        "publish_time": [".time", ".publish-time"],
    },
}


# ==================== 核心解析器 ====================

class ArticleParser:
    """文章页面专用解析器"""

    def __init__(self, custom_selectors: Optional[Dict[str, Dict[str, List[str]]]] = None):
        self.custom_selectors = custom_selectors or {}
        self._html_cache: str = ""

    def parse(self, html: str, url: str = "") -> ArticleParseResult:
        """主解析入口"""
        start_time = datetime.now()
        self._html_cache = html
        raw_size = len(html.encode("utf-8"))

        # 1. 检测文章类型
        platform = ArticleTypeDetector.detect_platform(url, html)
        logger.debug(f"检测文章平台: {platform} | {url[:60]}")

        # 2. 获取选择器
        selectors = self._get_selectors(platform)

        # 3. 尝试解析
        article = self._do_parse(html, url, selectors)
        if article is None:
            # 尝试JSON-LD（P1修复）
            article = self._try_jsonld_parse(html)
        if article is None:
            # P8新增：尝试Microdata
            article = self._try_microdata_parse(html)

        elapsed_ms = (datetime.now() - start_time).microseconds // 1000

        if article is None:
            return ArticleParseResult(
                success=False,
                error="无法解析文章内容",
                detected_type=platform,
                parse_time_ms=elapsed_ms,
                raw_html_size=raw_size,
            )

        return ArticleParseResult(
            success=True,
            article=article,
            detected_type=platform,
            parse_time_ms=elapsed_ms,
            raw_html_size=raw_size,
        )

    def _get_selectors(self, platform: str) -> Dict[str, List[str]]:
        custom = self.custom_selectors.get(platform, {})
        if custom:
            return custom
        base = ArticleTypeDetector.get_selectors(platform)
        return base

    def _do_parse(
        self,
        html: str,
        url: str,
        selectors: Dict[str, List[str]],
    ) -> Optional[ArticleContent]:
        """执行HTML解析"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
        except ImportError:
            logger.warning("BeautifulSoup未安装，无法解析HTML")
            return None

        article = ArticleContent(canonical_url=url)

        # 提取标题
        article.title = self._extract_one(soup, selectors.get("title", []))
        if not article.title:
            title_tag = soup.find("title")
            if title_tag:
                article.title = title_tag.get_text(strip=True)

        # 提取正文内容
        article.content = self._extract_content(soup, selectors.get("content", []))
        if not article.content:
            article.content = self._extract_generic_content(soup)

        # 清理内容中的脚本和样式
        self._clean_content_tags(article.content)

        # 提取作者
        article.author = self._extract_one(soup, selectors.get("author", []))

        # 提取发布时间
        article.publish_time = self._extract_one(soup, selectors.get("publish_time", []))

        # 提取摘要
        article.summary = self._extract_one(soup, selectors.get("summary", []))

        # 提取标签
        article.tags = self._extract_tags(soup, selectors.get("tags", []))

        # 提取图片
        article.images = self._extract_images(soup)

        # 计算字数和阅读时间
        article.word_count = self._count_words(article.content)
        article.reading_time_min = max(1, article.word_count // 400)  # 400字/分钟

        # 提取元数据
        self._extract_metadata(soup, article)

        return article if (article.title or article.content) else None

    def _extract_one(self, soup: Any, selectors: List[str]) -> str:
        """按选择器列表依次尝试提取文本"""
        for selector in selectors:
            elem = soup.select_one(selector)
            if elem:
                text = elem.get_text(strip=True)
                if text:
                    return text[:50000]
        return ""

    def _extract_content(self, soup: Any, selectors: List[str]) -> str:
        """提取文章正文内容"""
        for selector in selectors:
            elem = soup.select_one(selector)
            if elem:
                # 移除脚本和样式
                for tag in elem.select("script, style, nav, header, footer, aside"):
                    tag.decompose()
                text = elem.get_text(strip=True)
                if len(text) > 0:
                    return text[:50000]
        return ""

    def _extract_generic_content(self, soup: Any) -> str:
        """通用内容提取（兜底）"""
        candidates = ["article", "main", ".post", ".entry", ".content"]
        for selector in candidates:
            elem = soup.select_one(selector)
            if elem:
                for tag in elem.select("script, style"):
                    tag.decompose()
                text = elem.get_text(strip=True)
                if len(text) > 50:
                    return text[:50000]
        return ""

    def _clean_content_tags(self, content: str) -> None:
        """清理内容中的HTML标签（保留纯文本）"""
        pass  # content已经是纯文本

    def _extract_tags(self, soup: Any, selectors: List[str]) -> List[str]:
        """提取标签列表"""
        tags = []
        for selector in selectors:
            for elem in soup.select(selector):
                text = elem.get_text(strip=True)
                if text and len(text) < 50 and text not in tags:
                    tags.append(text)
            if tags:
                break
        return tags[:20]  # 最多20个标签

    def _extract_images(self, soup: Any) -> List[str]:
        """提取文章图片"""
        images = []
        seen = set()
        for img in soup.select("img"):
            src = (img.get("src") or img.get("data-src") or "").strip()
            if src and src not in ("about:blank", "", "#") and src not in seen:
                if src.startswith("//"):
                    src = "https:" + src
                if src.startswith("http") and len(src) < 500:
                    images.append(src)
                    seen.add(src)
            if len(images) >= 20:
                break
        return images

    def _extract_metadata(self, soup: Any, article: ArticleContent) -> None:
        """提取元数据"""
        # og标签
        og_tags = soup.select("meta[property^='og:']")
        for tag in og_tags:
            prop = tag.get("property", "")
            content = tag.get("content", "")
            key = prop.replace("og:", "")
            if key == "title" and not article.title:
                article.title = content
            elif key == "description" and not article.summary:
                article.summary = content
            elif key == "image" and article.images and content not in article.images:
                article.images.insert(0, content)
            article.metadata[f"og:{key}"] = content

        # Twitter Card
        tw_tags = soup.select("meta[name^='twitter:']")
        for tag in tw_tags:
            name = tag.get("name", "")
            content = tag.get("content", "")
            article.metadata[name] = content

    def _try_jsonld_parse(self, html: str) -> Optional[ArticleContent]:
        """尝试从JSON-LD解析（P1修复：使用parser_base的方法）"""
        from src.core.parser_base import BaseParser
        ld_data = BaseParser._extract_jsonld_fallback(html) if hasattr(BaseParser, '_extract_jsonld_fallback') else None
        if not ld_data:
            # 降级：直接调用_base方法
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, 'lxml')
                scripts = soup.find_all('script', {'type': 'application/ld+json'})
                for script in scripts:
                    text = script.get_text(strip=True)
                    if not text:
                        continue
                    try:
                        data = json.loads(text)
                        if isinstance(data, dict):
                            result = self._parse_jsonld_item(data)
                            if result:
                                return result
                        elif isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict):
                                    result = self._parse_jsonld_item(item)
                                    if result:
                                        return result
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass
        return None

    def _try_microdata_parse(self, html: str) -> Optional[ArticleContent]:
        """P8新增：尝试从Microdata解析（支持schema.org结构化数据）"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')
        except ImportError:
            return None

        # 查找itemscope元素
        for elem in soup.find_all('div', {'itemscope': True}):
            type_value = elem.get('itemtype', '')
            if not any(t in type_value.lower() for t in ['article', 'newsarticle', 'blogposting']):
                continue

            article = ArticleContent()
            # 提取title
            title = elem.find('span', {'itemprop': 'headline'}) or elem.find('h1', {'itemprop': 'name'})
            if title:
                article.title = title.get_text(strip=True)
            # 提取author
            author = elem.find('span', {'itemprop': 'author'})
            if author:
                article.author = author.get_text(strip=True)
            # 提取datePublished
            date_elem = elem.find('time', {'itemprop': 'datePublished'})
            if date_elem:
                article.publish_time = date_elem.get('datetime', '') or date_elem.get_text(strip=True)
            # 提取articleBody
            body = elem.find('div', {'itemprop': 'articleBody'})
            if body:
                article.content = body.get_text(strip=True)[:50000]
            if article.title or article.content:
                return article
        return None

    def _parse_jsonld_item(self, data: Dict[str, Any]) -> Optional[ArticleContent]:
        """解析单个JSON-LD对象"""
        type_str = data.get("@type", "")
        if isinstance(type_str, list):
            type_str = type_str[0]
        # 只处理文章类型
        if not any(t in type_str for t in ArticleTypeDetector.JSONLD_ARTICLE_TYPES):
            return None

        article = ArticleContent()
        article.title = data.get("headline", data.get("name", ""))
        if isinstance(data.get("author"), dict):
            article.author = data["author"].get("name", "")
        elif isinstance(data.get("author"), str):
            article.author = data["author"]
        pub_date = data.get("datePublished", data.get("dateCreated", ""))
        if pub_date:
            article.publish_time = str(pub_date)[:19]
        article.content = data.get("articleBody", data.get("description", ""))
        article.summary = data.get("description", "")
        if data.get("image"):
            img = data["image"]
            if isinstance(img, str):
                article.images = [img]
            elif isinstance(img, list):
                article.images = [i for i in img if isinstance(i, str)]
        return article if article.title or article.content else None

    @staticmethod
    def _count_words(text: str) -> int:
        """统计中文字数和英文单词数"""
        if not text:
            return 0
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        en_words = len(re.findall(r'[a-zA-Z]+', text))
        return cn_chars + en_words

    def parse_article_list(self, html: str, url: str = "") -> List[Dict[str, Any]]:
        """解析文章列表页（提取每篇文章的标题和链接）"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
        except ImportError:
            return []

        articles = []
        seen_urls = set()
        for a_tag in soup.select("a[href]"):
            href = a_tag.get("href", "").strip()
            if not href or href in ("#", "javascript:", ""):
                continue
            # 过滤短链接和非文章链接
            if len(href) < 5 or ("/p/" not in href and "/article/" not in href and "/news/" not in href and "/post/" not in href and "/detail/" not in href and "/story/" not in href):
                continue
            if href in seen_urls:
                continue
            seen_urls.add(href)
            title = a_tag.get_text(strip=True)
            if not title or len(title) < 3:
                continue
            # 规范化URL
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                from urllib.parse import urljoin
                href = urljoin(url, href)
            articles.append({
                "title": title[:200],
                "url": href,
            })
            if len(articles) >= 100:
                break
        return articles


# ==================== 平台特化解析器 ====================

class ZhihuArticleParser(ArticleParser):
    """知乎文章解析器"""

    def _do_parse(self, html, url, selectors):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")

        article = ArticleContent(canonical_url=url)
        # 标题
        article.title = self._extract_one(soup, selectors.get("title", [
            "h1.QuestionHeader-title", ".QuestionHeader-title",
        ]))
        # 内容
        content_elems = soup.select(".RichContent-inner, .ContentItem-richText")
        if content_elems:
            for elem in content_elems:
                for tag in elem.select("script, style"):
                    tag.decompose()
                text = elem.get_text(strip=True)
                if len(text) > 0:
                    article.content = text[:50000]
                    break
        # 作者
        article.author = self._extract_one(soup, [".AuthorInfo-name", ".ContentItem-authorName"])
        # 时间
        time_elem = soup.select_one(".ContentItem-time")
        if time_elem:
            article.publish_time = time_elem.get("datetime", "") or time_elem.get_text(strip=True)
        # 图片
        article.images = self._extract_images(soup)
        article.word_count = self._count_words(article.content)
        article.reading_time_min = max(1, article.word_count // 400)
        return article


class CsdnArticleParser(ArticleParser):
    """CSDN文章解析器"""

    def _do_parse(self, html, url, selectors):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")

        article = ArticleContent(canonical_url=url)
        article.title = self._extract_one(soup, selectors.get("title", [
            "h1.article_title", ".article_title",
        ]))
        content = soup.select_one("#content_views, .article_content")
        if content:
            for tag in content.select("script, style"):
                tag.decompose()
            article.content = content.get_text(strip=True)[:50000]
        article.author = self._extract_one(soup, [".article_name", ".user-name"])
        time_elem = soup.select_one(".time, .article_date")
        if time_elem:
            article.publish_time = time_elem.get_text(strip=True)
        article.tags = self._extract_tags(soup, [".tag-article, .article-tag a"])
        article.images = self._extract_images(soup)
        article.word_count = self._count_words(article.content)
        article.reading_time_min = max(1, article.word_count // 400)
        return article


# ==================== 工厂函数 ====================

def create_article_parser(platform: Optional[str] = None,
                          custom_selectors: Optional[Dict] = None) -> ArticleParser:
    """根据平台创建专用解析器"""
    if platform == "zhihu":
        return ZhihuArticleParser(custom_selectors=custom_selectors)
    elif platform == "csdn":
        return CsdnArticleParser(custom_selectors=custom_selectors)
    else:
        return ArticleParser(custom_selectors=custom_selectors)


def parse_article(html: str, url: str = "") -> ArticleParseResult:
    """便捷函数：直接解析文章"""
    parser = ArticleParser()
    return parser.parse(html, url)


__all__ = [
    "ArticleContent", "ArticleParseResult", "ArticleParser",
    "ArticleTypeDetector", "ZhihuArticleParser", "CsdnArticleParser",
    "create_article_parser", "parse_article",
]
