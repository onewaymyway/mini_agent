# -*- coding: utf-8 -*-
"""
ArticleParser 单元测试
"""
import sys
from pathlib import Path

SKILL_SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SKILL_SRC))

from core.article_parser import (
    ArticleParser,
    ArticleTypeDetector,
    ZhihuArticleParser,
    CsdnArticleParser,
    create_article_parser,
    parse_article,
)


class TestArticleTypeDetector:
    def test_detect_zhihu(self):
        platform = ArticleTypeDetector.detect_platform(
            "https://zhuanlan.zhihu.com/p/123456789", "")
        assert platform == "zhihu", f"Expected zhihu, got {platform}"

    def test_detect_csdn(self):
        platform = ArticleTypeDetector.detect_platform(
            "https://blog.csdn.net/qq_123456/article/details/123456789", "")
        assert platform == "csdn", f"Expected csdn, got {platform}"

    def test_detect_thepaper(self):
        platform = ArticleTypeDetector.detect_platform(
            "https://www.thepaper.cn/newsDetail_forward_25955", "")
        assert platform == "thepaper", f"Expected thepaper, got {platform}"

    def test_detect_sina(self):
        platform = ArticleTypeDetector.detect_platform(
            "https://finance.sina.com.cn/stock/usstock/2024.shtml", "")
        assert platform == "sina", f"Expected sina, got {platform}"

    def test_detect_jsonld(self):
        html = '<script type="application/ld+json">{"@type": "NewsArticle"}</script>'
        platform = ArticleTypeDetector.detect_platform("https://example.com", html)
        assert platform == "jsonld_article", f"Expected jsonld_article, got {platform}"

    def test_detect_generic(self):
        platform = ArticleTypeDetector.detect_platform("https://example.com/blog/post", "")
        assert platform == "generic_article", f"Expected generic_article, got {platform}"


class TestArticleParser:
    def _make_html(self, title="测试标题", content="这是文章内容", author="张三", publish_time="2024-01-01"):
        return f'''
        <html><head><title>{title}</title></head><body>
        <h1 class="article-title">{title}</h1>
        <span class="article-author">{author}</span>
        <time class="publish-time">{publish_time}</time>
        <article class="article-content">{content}</article>
        </body></html>
        '''

    def test_parse_basic_article(self):
        parser = ArticleParser()
        html = self._make_html(title="Hello World", content="这是文章内容", author="张三", publish_time="2024-01-15")
        result = parser.parse(html, "https://example.com/post/123")
        assert result.success, f"Parse failed: {result.error}"
        assert result.article is not None
        assert result.article.title == "Hello World"
        assert result.article.author == "张三"
        assert result.article.content == "这是文章内容"
        assert result.detected_type == "generic_article"

    def test_parse_with_jsonld(self):
        parser = ArticleParser()
        html = '''
        <html><head></head><body>
        <script type="application/ld+json">
        {"@type": "NewsArticle", "headline": "JSON标题", "author": {"name": "JSON作者"}, "datePublished": "2024-03-01", "articleBody": "JSON内容"}
        </script>
        </body></html>
        '''
        result = parser.parse(html, "https://example.com/news")
        assert result.success
        assert result.article.title == "JSON标题"
        assert result.article.author == "JSON作者"

    def test_parse_empty_html(self):
        parser = ArticleParser()
        result = parser.parse("", "https://example.com")
        assert not result.success
        assert result.error is not None

    def test_article_to_dict(self):
        parser = ArticleParser()
        html = self._make_html()
        result = parser.parse(html)
        assert result.success
        d = result.article.to_dict()
        assert "title" in d
        assert "word_count" in d
        assert "reading_time_min" in d

    def test_word_count_chinese(self):
        parser = ArticleParser()
        html = self._make_html(content="中文内容测试一二三")
        result = parser.parse(html)
        assert result.success
        assert result.article.word_count >= 6


class TestZhihuParser:
    def test_parse_zhihu(self):
        html = '''
        <html><body>
        <h1 class="QuestionHeader-title">知乎问题标题</h1>
        <div class="RichContent-inner">这是知乎文章内容</div>
        <span class="AuthorInfo-name">知乎作者</span>
        </body></html>
        '''
        parser = ZhihuArticleParser()
        result = parser.parse(html, "https://zhuanlan.zhihu.com/p/123")
        assert result.success
        assert result.article.title == "知乎问题标题"
        assert result.article.content == "这是知乎文章内容"
        assert result.article.author == "知乎作者"


class TestCsdnParser:
    def test_parse_csdn(self):
        html = '''
        <html><body>
        <h1 class="article_title">CSDN博客标题</h1>
        <div id="content_views">这是CSDN文章内容</div>
        <span class="article_name">CSDN作者</span>
        <span class="time">2024-01-01</span>
        </body></html>
        '''
        parser = CsdnArticleParser()
        result = parser.parse(html, "https://blog.csdn.net/user/article/details/123")
        assert result.success
        assert result.article.title == "CSDN博客标题"
        assert result.article.content == "这是CSDN文章内容"


class TestArticleListParsing:
    def test_parse_article_list(self):
        parser = ArticleParser()
        html = '''
        <html><body>
        <a href="/p/123">文章一</a>
        <a href="/p/456">文章二</a>
        <a href="/login">登录</a>
        </body></html>
        '''
        articles = parser.parse_article_list(html, "https://example.com/list")
        urls = [a["url"] for a in articles]
        assert any("/p/123" in u for u in urls)
        assert len(articles) >= 1

    def test_parse_empty_list(self):
        parser = ArticleParser()
        html = '<html><body></body></html>'
        articles = parser.parse_article_list(html)
        assert articles == []


class TestFactory:
    def test_create_zhihu(self):
        parser = create_article_parser(platform="zhihu")
        assert isinstance(parser, ZhihuArticleParser)

    def test_create_default(self):
        parser = create_article_parser()
        assert isinstance(parser, ArticleParser)

    def test_parse_article_convenience(self):
        html = '<html><body><h1 class="article-title">标题</h1><article class="article-content">内容</article></body></html>'
        result = parse_article(html, "https://example.com")
        assert result.success
        assert result.article.title == "标题"


def run_all_tests():
    passed = failed = 0
    failures = []
    for cls in [
        TestArticleTypeDetector, TestArticleParser,
        TestZhihuParser, TestCsdnParser,
        TestArticleListParsing, TestFactory,
    ]:
        instance = cls()
        for name in dir(instance):
            if name.startswith("test_"):
                try:
                    getattr(instance, name)()
                    passed += 1
                except Exception as e:
                    failed += 1
                    failures.append((cls.__name__, name, str(e)))
                    import traceback
                    traceback.print_exc()
    print(f"\n测试结果: {passed} passed, {failed} failed")
    if failures:
        print("失败详情:")
        for cls_name, method, err in failures:
            print(f"  {cls_name}.{method}: {err}")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
