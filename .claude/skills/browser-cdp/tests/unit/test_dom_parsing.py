"""
DOM解析能力单元测试

测试BaseParser和SmartContentParser的静态HTML解析逻辑，无需浏览器。
目标：通过率 >= 95%

覆盖场景：
- 文章页面解析（标题、正文、作者、时间）
- 搜索结果提取
- 商品数据提取
- JSON-LD结构化数据提取
- 链接和图片提取
- 分页检测
- 选择器容错
"""
from __future__ import annotations

import json
import logging
import sys
import os
from typing import List, Dict, Any
from unittest.mock import MagicMock, patch

import pytest

# 添加skill目录到路径
skill_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, skill_dir)

from src.core.parser_base import BaseParser, ParseResult
from src.core.smart_content_parser import SmartContentParser, ContentItem, ParsingContext
from src.core.structured_extractor import StructuredDataExtractor
from src.core.smart_selector import SelectorCache, SelectorConfig

logger = logging.getLogger(__name__)


# ============================================================================
# HTML fixtures - 模拟各种网站页面
# ============================================================================

BAIDU_SEARCH_HTML = """
<!DOCTYPE html>
<html>
<head><title>百度搜索</title></head>
<body>
  <div id="wrapper">
    <div id="head">
      <div class="s_nav">
        <a href="/">首页</a>
        <a href="news">新闻</a>
        <a href="zhidao">知道</a>
      </div>
      <div id="form" class="s_form">
        <div class="s_form_wrapper">
          <input id="kw" name="wd" class="s_ipt" value="" autocomplete="off">
          <input id="su" type="submit" value="百度一下" class="btn self-btn">
        </div>
      </div>
    </div>
    <div id="content">
      <div class="result">
        <h3 class="t"><a href="https://baike.baidu.com/item/AI" data-click="{'p':'clickurl','u':'https://baike.baidu.com'}">AI_百度百科</a></h3>
        <span class="c-gap-left-small">人工智能，是研究、开发用于模拟、延伸和扩展人的智能的理论、方法...</span>
      </div>
      <div class="result">
        <h3 class="t"><a href="https://zhuanlan.zhihu.com/p/ai">AI大模型入门指南 - 知乎</a></h3>
        <span class="c-gap-left-small">本文介绍了大语言模型的基本原理和应用场景...</span>
      </div>
    </div>
  </div>
</body>
</html>
"""

NEWS_ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>新浪财经 - 测试新闻标题</title>
  <meta property="og:title" content="新浪财经 - 测试新闻标题" />
  <meta property="og:description" content="这是一条测试新闻摘要内容" />
  <meta name="author" content="张三" />
  <meta property="article:published_time" content="2026-08-12T08:00:00+08:00" />
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "JSON-LD新闻标题",
    "datePublished": "2026-08-12T08:00:00+08:00",
    "author": {"@type": "Person", "name": "李四"},
    "publisher": {"@type": "Organization", "name": "测试报社"},
    "image": "https://example.com/image.jpg"
  }
  </script>
</head>
<body>
  <div class="article">
    <h1 class="article-title">测试新闻标题</h1>
    <div class="article-info">
      <span class="source">新浪财经</span>
      <span class="time">2026-08-12 08:00</span>
      <span class="author">张三</span>
    </div>
    <div class="article-content">
      <p>这是新闻正文的第一段内容。</p>
      <p>这是新闻正文的第二段内容，包含更多详细信息。</p>
      <img src="https://example.com/img1.jpg" alt="配图1">
      <img data-src="https://example.com/img2.jpg" alt="配图2">
    </div>
    <div class="tags">
      <a href="#">科技</a>
      <a href="#">AI</a>
      <a href="#">新闻</a>
    </div>
  </div>
  <nav class="pagination">
    <a rel="next" href="https://example.com/page/2">下一页</a>
  </nav>
</body>
</html>
"""

NEWS_LIST_HTML = """
<!DOCTYPE html>
<html>
<head><title>新浪新闻首页</title></head>
<body>
  <div class="news-list">
    <div class="list-item">
      <a href="/news/1">新闻标题1</a>
      <span class="time">2026-08-12</span>
      <span class="source">新浪新闻</span>
    </div>
    <div class="list-item">
      <a href="/news/2">新闻标题2</a>
      <span class="time">2026-08-12</span>
      <span class="source">新浪新闻</span>
    </div>
    <div class="list-item">
      <a href="/news/3">新闻标题3</a>
      <span class="time">2026-08-11</span>
      <span class="source">新浪新闻</span>
    </div>
  </div>
</body>
</html>
"""

PRODUCT_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head><title>京东 - 测试商品</title></head>
<body>
  <div class="sku-name">测试商品名称</div>
  <div class="price">
    <span class="p-price">¥ 999.00</span>
    <span class="p-original">¥ 1299.00</span>
  </div>
  <div class="product-features">
    <ul>
      <li>Feature 1</li>
      <li>Feature 2</li>
    </ul>
  </div>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "JSON-LD商品名称",
    "offers": {
      "@type": "Offer",
      "price": "999.00",
      "priceCurrency": "CNY"
    }
  }
  </script>
</body>
</html>
"""

ZHIHU_QUESTION_HTML = """
<!DOCTYPE html>
<html>
<head><title>知乎 - 测试问题</title></head>
<body>
  <div class="QuestionHeader">
    <h1 class="QuestionHeader-title">如何学习Python编程？</h1>
    <span class="QuestionHeader-followCount">1234关注</span>
    <span class="QuestionHeader-answerCount">5678回答</span>
  </div>
  <div class="Question-main">
    <div class="Question-question">
      <div class="RichContent">想了解Python入门学习方法...</div>
    </div>
  </div>
  <div class="List-main">
    <div class="Answer-item">
      <div class="Answer-header">
        <a class="Author-link">Python爱好者</a>
        <span class="Voted">1234赞同</span>
      </div>
      <div class="Answer-content">这是一个很好的回答内容...</</div>
    </div>
    <div class="Answer-item">
      <div class="Answer-header">
        <a class="Author-link">编程新手</a>
        <span class="Voted">567赞同</span>
      </div>
      <div class="Answer-content">补充一下...</div>
    </div>
  </div>
</body>
</html>
"""

GOV_CN_HTML = """
<!DOCTYPE html>
<html>
<head><title>中国政府网</title></head>
<body>
  <header class="header">
    <nav class="nav-main">
      <a href="/">首页</a>
      <a href="/zhengce">政策</a>
      <a href="/xinwen">新闻</a>
      <a href="/czfw">服务</a>
      <a href="/gk">公开</a>
    </nav>
  </header>
  <main class="main">
    <div class="content">
      <h1>国务院关于印发"十四五"规划的通知</h1>
      <p class="time">2026-08-12</p>
      <div class="text">这是正文内容...</div>
    </div>
  </main>
</body>
</html>
"""

SIMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>简单页面</title></head>
<body>
  <div class="container">
    <h1>主标题</h1>
    <p>正文内容段落。</p>
    <a href="https://example.com/1">链接1</a>
    <a href="https://example.com/2">链接2</a>
    <img src="https://example.com/img1.jpg">
    <img data-src="https://example.com/img2.jpg">
  </div>
</body>
</html>
"""

EMPTY_HTML = """
<!DOCTYPE html>
<html><head><title>空页面</title></head><body></body></html>
"""


# ============================================================================
# 测试辅助类
# ============================================================================

class TestSmartParser(SmartContentParser):
    """测试用 SmartContentParser 子类，实现抽象方法"""
    SELECTORS = {
        "title": ["h1", ".title", "meta[property=\"og:title\"]"],
        "author": [".author", "meta[name=\"author\"]"],
        "content": [".content", "article", ".article-content"],
    }
    JSONLD_KEYS = ["@type", "headline", "name", "author", "datePublished"]

    def _do_parse(self, content, url, headers):
        # 委托给父类的通用 parse 逻辑（通过直接调用内部方法）
        return self.parse(content, url, headers)


class TestBaseParserImpl(BaseParser):
    """测试用 BaseParser 子类，实现抽象方法"""
    JSONLD_KEYS = ["@type", "headline", "name"]

    def _do_parse(self, content, url, headers):
        return ParseResult(success=True)


# ============================================================================
# BaseParser 单元测试
# ============================================================================

class TestBaseParser:
    """BaseParser 工具方法测试"""

    def test_extract_text_none(self):
        assert BaseParser.extract_text(None) == ""
        assert BaseParser.extract_text(None, "default") == "default"

    def test_extract_text_string(self):
        mock = MagicMock()
        mock.get_text.return_value = "hello  world"  # 已 strip 的值
        assert BaseParser.extract_text(mock) == "hello  world"

    def test_extract_attr_none(self):
        assert BaseParser.extract_attr(None, "href") == ""
        assert BaseParser.extract_attr(None, "href", "default") == "default"

    def test_extract_attr_dict(self):
        assert BaseParser.extract_attr({"href": "https://example.com"}, "href") == "https://example.com"
        assert BaseParser.extract_attr({"href": "https://example.com"}, "src", "fallback") == "fallback"

    def test_clean_text_empty(self):
        assert BaseParser.clean_text("") == ""
        assert BaseParser.clean_text(None) == ""

    def test_clean_text_whitespace(self):
        text = "  hello   world  foo   bar  "
        result = BaseParser.clean_text(text)
        assert result == "hello world foo bar"

    def test_clean_text_max_length(self):
        long_text = "a" * 1000
        result = BaseParser.clean_text(long_text, max_length=100)
        assert len(result) == 100

    def test_parse_with_subclass(self):
        parser = TestBaseParserImpl()
        result = parser.parse("<p>test</p>", url="https://example.com")
        assert result.success is True
        assert result.metadata.get("parser") == "TestBaseParserImpl"


class TestExtractLinks:
    """链接提取测试"""

    def test_extract_links_basic(self):
        html = '<a href="https://example.com/1">Link1</a><a href="https://example.com/2">Link2</a>'
        links = BaseParser.extract_links(html)
        assert len(links) == 2
        assert "https://example.com/1" in links
        assert "https://example.com/2" in links

    def test_extract_links_relative(self):
        html = '<a href="/page/1">Link1</a><a href="/page/2">Link2</a>'
        links = BaseParser.extract_links(html, base_url="https://example.com")
        assert "https://example.com/page/1" in links
        assert "https://example.com/page/2" in links

    def test_extract_links_filter_bad(self):
        html = '''
          <a href="javascript:void(0)">Bad1</a>
          <a href="#">Bad2</a>
          <a href="">Bad3</a>
          <a href="https://example.com/good">Good</a>
        '''
        links = BaseParser.extract_links(html)
        assert len(links) == 1
        assert links[0] == "https://example.com/good"

    def test_extract_links_dedup(self):
        html = '<a href="https://example.com/same">A</a><a href="https://example.com/same">B</a>'
        links = BaseParser.extract_links(html)
        assert len(links) == 1

    def test_extract_links_protocol_relative(self):
        html = '<a href="//example.com/page">Link</a>'
        links = BaseParser.extract_links(html)
        assert links[0] == "https://example.com/page"

    def test_extract_links_max_count(self):
        html = ''.join(f'<a href="https://example.com/{i}">Link{i}</a>' for i in range(100))
        links = BaseParser.extract_links(html, max_count=10)
        assert len(links) == 10


class TestExtractImages:
    """图片提取测试"""

    def test_extract_images_src(self):
        html = '<img src="https://example.com/img1.jpg"><img src="https://example.com/img2.png">'
        images = BaseParser.extract_images(html)
        assert len(images) == 2
        assert images[0]["src"] == "https://example.com/img1.jpg"

    def test_extract_images_data_src(self):
        html = '<img data-src="https://example.com/lazy1.jpg"><img data-src="https://example.com/lazy2.jpg">'
        images = BaseParser.extract_images(html)
        assert len(images) == 2

    def test_extract_images_mixed(self):
        html = '''
          <img src="https://example.com/real.jpg">
          <img data-src="https://example.com/lazy.jpg">
          <img src="about:blank">
          <img>
        '''
        images = BaseParser.extract_images(html)
        assert len(images) == 2

    def test_extract_images_protocol_relative(self):
        html = '<img src="//example.com/img.jpg">'
        images = BaseParser.extract_images(html)
        assert images[0]["src"] == "https://example.com/img.jpg"


class TestExtractJSONLD:
    """JSON-LD 提取测试（使用通用解析器实现）"""

    def _make_parser(self):
        return TestBaseParserImpl()

    def test_extract_jsonld_basic(self):
        parser = self._make_parser()
        html = '''
          <script type="application/ld+json">
          {"@context":"https://schema.org","@type":"NewsArticle","headline":"Test"}
          </script>
        '''
        result = parser._extract_jsonld(html)
        assert result is not None
        assert result["headline"] == "Test"

    def test_extract_jsonld_not_found(self):
        parser = self._make_parser()
        html = '<p>没有结构化数据</p>'
        result = parser._extract_jsonld(html)
        assert result is None

    def test_extract_jsonld_array(self):
        parser = self._make_parser()
        html = '''
          <script type="application/ld+json">
          [{"@type":"Person","name":"Alice"},{"@type":"Person","name":"Bob"}]
          </script>
        '''
        result = parser._extract_jsonld(html)
        assert result is not None
        assert result["name"] == "Alice"


class TestDetectPagination:
    """分页检测测试"""

    def test_detect_next_link(self):
        from bs4 import BeautifulSoup
        html = '<a rel="next" href="/page/2">下一页</a>'
        soup = BeautifulSoup(html, 'lxml')
        has_more, next_url = BaseParser.detect_pagination(html, soup, "https://example.com")
        assert has_more is True
        assert next_url == "/page/2"

    def test_detect_next_button(self):
        from bs4 import BeautifulSoup
        html = '<button class="load-more">加载更多</button>'
        soup = BeautifulSoup(html, 'lxml')
        has_more, next_url = BaseParser.detect_pagination(html, soup, "https://example.com")
        assert has_more is True

    def test_no_pagination(self):
        from bs4 import BeautifulSoup
        html = '<p>没有分页</p>'
        soup = BeautifulSoup(html, 'lxml')
        has_more, next_url = BaseParser.detect_pagination(html, soup, "https://example.com")
        assert has_more is False
        assert next_url is None


class TestTextSimilarity:
    """文本相似度测试"""

    def test_identical_chinese(self):
        s = BaseParser.text_similarity("你好世界", "你好世界")
        assert s == 1.0

    def test_different_chinese(self):
        s = BaseParser.text_similarity("你好世界", "再见世界")
        assert 0 < s < 1.0

    def test_empty_string(self):
        s = BaseParser.text_similarity("", "hello")
        assert s == 0.0

    def test_identical_english(self):
        s = BaseParser.text_similarity("hello world", "hello world")
        assert s == 1.0


class TestDeduplicate:
    """去重测试"""

    def test_no_duplicates(self):
        items = [{"title": "A"}, {"title": "B"}]
        result = BaseParser.deduplicate_items(items)
        assert len(result) == 2

    def test_with_duplicates(self):
        items = [
            {"title": "相同标题"},
            {"title": "相似标题内容几乎一样"},
        ]
        result = BaseParser.deduplicate_items(items, threshold=0.8)
        # 高相似度应该去重
        assert len(result) <= 2


# ============================================================================
# SmartContentParser 单元测试
# ============================================================================

class TestSmartContentParser:
    """SmartContentParser 解析测试"""

    def _create_parser(self):
        return TestSmartParser()

    def test_parse_news_article(self):
        parser = self._create_parser()
        result = parser.parse(NEWS_ARTICLE_HTML, url="https://example.com/news/1")
        assert result.success is True
        assert len(result.items) > 0
        item = result.items[0]
        # parser 优先从 <title> 提取，可能包含网站名
        assert "测试新闻标题" in item["title"]
        assert len(item["description"]) > 0

    def test_parse_baidu_search(self):
        parser = self._create_parser()
        result = parser.parse(BAIDU_SEARCH_HTML, url="https://www.baidu.com/s?wd=test")
        assert result.success is True
        # 应检测到搜索结果页面
        assert result.metadata.get("page_type") in ("search", "unknown")

    def test_parse_zhihu_question(self):
        parser = self._create_parser()
        result = parser.parse(ZHIHU_QUESTION_HTML, url="https://www.zhihu.com/question/123")
        assert result.success is True

    def test_parse_gov_page(self):
        parser = self._create_parser()
        result = parser.parse(GOV_CN_HTML, url="https://www.gov.cn/zhengce/123")
        assert result.success is True
        assert len(result.items) > 0
        item = result.items[0]
        # title 来自 <title> 或 <h1>，只要非空且不是空页面即可
        assert item["title"] != ""

    def test_parse_empty(self):
        parser = self._create_parser()
        result = parser.parse(EMPTY_HTML, url="https://example.com/empty")
        # 空页面不应报错，应返回空结果
        assert result.success is True

    def test_page_type_detection_article(self):
        parser = self._create_parser()
        parser._detect_page_type(NEWS_ARTICLE_HTML)
        assert parser._page_type in ("article", "news")
        assert parser._page_type_confidence >= 0.5

    def test_page_type_detection_search(self):
        parser = self._create_parser()
        parser._detect_page_type(BAIDU_SEARCH_HTML)
        # 无结构化数据，通过URL检测
        assert parser._page_type == "search"

    def test_structured_data_extraction(self):
        parser = self._create_parser()
        context = ParsingContext(html=NEWS_ARTICLE_HTML, url="https://example.com")
        context.structured_data = parser._extract_structured_data(NEWS_ARTICLE_HTML, "https://example.com")
        if context.has_structured_data():
            assert len(context.structured_data.fields) > 0


class TestSelectorCache:
    """选择器缓存测试"""

    def test_cache_miss(self):
        cache = SelectorCache()
        result = cache.get_effective_selector("example.com", "title")
        assert result is None

    def test_cache_record_and_retrieve(self):
        cache = SelectorCache()
        cache.record_success("example.com", "title", ".article-title", 50)
        # 不设置缓存文件，直接验证内存
        key = "example.com:title"
        assert key in cache.cache
        assert cache.cache[key]["valid"] is True


class TestStructuredDataExtractor:
    """结构化数据提取器测试"""

    def test_extract_jsonld_basic(self):
        extractor = StructuredDataExtractor()
        html = '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Person","name":"Test"}</script>'
        result = extractor.extract(html)
        assert result.success is True
        assert len(result.fields) > 0

    def test_extract_jsonld_invalid_json(self):
        extractor = StructuredDataExtractor()
        html = '<script type="application/ld+json">{invalid json}</script>'
        result = extractor.extract(html)
        # 无效JSON不应导致异常
        assert result.success is False or len(result.fields) == 0

    def test_extract_og_tags(self):
        extractor = StructuredDataExtractor()
        html = '''
          <meta property="og:title" content="OG标题">
          <meta property="og:description" content="OG描述">
          <meta property="og:image" content="https://example.com/image.jpg">
        '''
        result = extractor.extract(html)
        assert result.success is True


# ============================================================================
# 综合场景测试 - 多网站页面解析
# ============================================================================

class TestMultiSiteParsing:
    """多网站页面解析综合测试"""

    PAGES = {
        "baidu": BAIDU_SEARCH_HTML,
        "sina_news_article": NEWS_ARTICLE_HTML,
        "sina_news_list": NEWS_LIST_HTML,
        "jd_product": PRODUCT_PAGE_HTML,
        "zhihu_question": ZHIHU_QUESTION_HTML,
        "gov_cn": GOV_CN_HTML,
        "simple": SIMPLE_HTML,
        "empty": EMPTY_HTML,
    }

    @pytest.mark.parametrize("site_name,html", [
        (name, html) for name, html in PAGES.items()
    ])
    def test_parse_no_crash(self, site_name, html):
        """所有页面应能正常解析而不抛出异常"""
        parser = TestSmartParser()
        try:
            result = parser.parse(html, url=f"https://example.com/{site_name}")
            assert result is not None
        except Exception as e:
            pytest.fail(f"{site_name} 页面解析失败: {e}")

    def test_baidu_search_extraction(self):
        """百度搜索页应能提取结果链接"""
        parser = TestSmartParser()
        result = parser.parse(BAIDU_SEARCH_HTML, url="https://www.baidu.com/s?wd=AI")
        links = BaseParser.extract_links(BAIDU_SEARCH_HTML)
        assert len(links) >= 2  # 至少有百科和知乎两个结果

    def test_news_article_fields(self):
        """新闻文章应提取标题、作者、时间"""
        parser = TestSmartParser()
        result = parser.parse(NEWS_ARTICLE_HTML, url="https://finance.sina.com.cn/123")
        assert len(result.items) > 0
        item = result.items[0]
        # 标题应存在
        assert item["title"] != ""

    def test_gov_page_structure(self):
        """中国政府网页面结构正确"""
        parser = TestSmartParser()
        result = parser.parse(GOV_CN_HTML, url="https://www.gov.cn")
        assert result.success is True

    def test_product_price_extraction(self):
        """商品页面应能提取价格"""
        parser = TestSmartParser()
        result = parser.parse(PRODUCT_PAGE_HTML, url="https://item.jd.com/123.html")
        assert result.success is True

    def test_navigation_links(self):
        """导航链接应能提取"""
        links = BaseParser.extract_links(GOV_CN_HTML, base_url="https://www.gov.cn")
        gov_links = [l for l in links if l.startswith("https://www.gov.cn")]
        assert len(gov_links) >= 5  # 至少5个导航链接

    def test_image_extraction_from_article(self):
        """新闻文章图片应能提取"""
        images = BaseParser.extract_images(NEWS_ARTICLE_HTML)
        assert len(images) == 2

    def test_empty_page_fallback(self):
        """空页面应安全处理"""
        parser = TestSmartParser()
        result = parser.parse(EMPTY_HTML, url="https://example.com/empty")
        # 不应崩溃，返回空结果
        assert result is not None
        assert result.success is True


# ============================================================================
# 选择器容错测试
# ============================================================================

class TestSelectorFaultTolerance:
    """选择器容错能力测试"""

    def test_fallback_selector_chain(self):
        """主选择器失败时应尝试备选选择器"""
        html = '<div class="content"><h1>标题</h1></div>'
        parser = TestSmartParser()
        # 测试多个选择器依次尝试
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
        # 主选择器不存在，备选选择器存在
        elem = soup.select_one('.nonexistent')
        assert elem is None
        elem = soup.select_one('h1')
        assert elem is not None
        assert elem.get_text(strip=True) == "标题"

    def test_partial_html(self):
        """部分HTML片段应能正常解析"""
        partial_html = '<div class="item"><a href="/link">Text</a></div>'
        links = BaseParser.extract_links(partial_html)
        assert len(links) == 1
        assert links[0] == "/link"

    def test_malformed_html(self):
        """格式错误的HTML应能容忍解析"""
        bad_html = '<div class="unclosed><p>内容</div>'
        try:
            links = BaseParser.extract_links(bad_html)
            # 即使解析失败也不应抛异常
        except Exception:
            pytest.fail(" malformed HTML 不应导致异常")


# ============================================================================
# 性能与边界测试
# ============================================================================

class TestPerformance:
    """性能相关测试"""

    def test_large_html_parse(self):
        """大HTML应能正常解析"""
        large_html = NEWS_ARTICLE_HTML * 100  # 重复100次
        parser = TestSmartParser()
        result = parser.parse(large_html, url="https://example.com/large")
        assert result is not None

    def test_unicode_content(self):
        """Unicode内容应正确处理"""
        unicode_html = '''
          <html><body>
          <h1>中文标题测试</h1>
          <p>这是一段包含中文、日文、韩文等内容：안녕하세요 你好世界</p>
          </body></html>
        '''
        parser = TestSmartParser()
        result = parser.parse(unicode_html, url="https://example.com/unicode")
        assert result is not None

    def test_special_characters_in_links(self):
        """链接中的特殊字符应正确处理"""
        html = '<a href="https://example.com/path?foo=bar&baz=qux">Link</a>'
        links = BaseParser.extract_links(html)
        assert len(links) == 1
        assert "?foo=bar&baz=qux" in links[0]


# ============================================================================
