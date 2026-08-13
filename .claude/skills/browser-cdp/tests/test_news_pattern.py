"""
test_news_pattern.py - 新闻类网站 Pattern 单元测试

覆盖：NewsPattern 基类、ZhihuNewsPattern、ToutiaoNewsPattern
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.selector_manager import SelectorManager, Selector, SelectorType
from src.interaction_patterns.news_pattern import NewsPattern, ArticleData, ArticleResults
from src.interaction_patterns.zhihu_news_pattern import ZhihuNewsPattern
from src.interaction_patterns.toutiao_news_pattern import ToutiaoNewsPattern


# ─────────────────────── ArticleData ───────────────────────

class TestArticleData:
    """ArticleData 数据模型测试"""

    def test_default_values(self):
        a = ArticleData()
        assert a.title == ""
        assert a.url == ""
        assert a.content == ""
        assert a.author == ""
        assert a.tags == []

    def test_to_dict(self):
        a = ArticleData(
            title="Test Title",
            url="https://example.com/1",
            author="Author",
            content="Content text",
            source_domain="example.com",
        )
        d = a.to_dict()
        assert d["title"] == "Test Title"
        assert d["url"] == "https://example.com/1"
        assert d["author"] == "Author"
        assert d["content"] == "Content text"

    def test_to_dict_empty(self):
        a = ArticleData()
        d = a.to_dict()
        assert d["title"] == ""
        assert d["metadata"] == {}

    def test_serialization_full(self):
        a = ArticleData(
            title="The Answer",
            url="https://zhihu.com/p/123",
            author="Alice",
            publish_time="2026-08-14",
            content="Long text content here.",
            snippet="Short summary",
            source_domain="zhihu.com",
            category="tech",
            tags=["AI", "news"],
        )
        d = a.to_dict()
        assert d["title"] == "The Answer"
        assert d["tags"] == ["AI", "news"]
        assert d["category"] == "tech"
        assert d["publish_time"] == "2026-08-14"


# ─────────────────────── ArticleResults ───────────────────────

class TestArticleResults:
    """ArticleResults 数据模型测试"""

    def test_empty_results(self):
        r = ArticleResults(success=True, query="test")
        assert r.is_empty is True
        assert r.articles == []

    def test_results_with_articles(self):
        articles = [
            ArticleData(title="A", url="http://a.com"),
            ArticleData(title="B", url="http://b.com"),
        ]
        r = ArticleResults(success=True, query="test", articles=articles, total_count=2)
        assert r.is_empty is False
        assert len(r.articles) == 2

    def test_to_dict(self):
        articles = [ArticleData(title="T", url="http://t.com")]
        r = ArticleResults(success=True, query="q", articles=articles, pattern_used="Test")
        d = r.to_dict()
        assert d["success"] is True
        assert d["query"] == "q"
        assert len(d["articles"]) == 1
        assert d["pattern_used"] == "Test"

    def test_error_results(self):
        r = ArticleResults(success=False, query="q", error_message="timeout")
        assert r.success is False
        assert r.error_message == "timeout"


# ─────────────────────── NewsPattern (base class) ───────────────────────

class TestNewsPatternBase:
    """NewsPattern 基类测试"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_init_default_selectors(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            mock_wait_cls.return_value.wait_for_network_idle = AsyncMock()

            class ConcreteNews(NewsPattern):
                async def execute(self, query, max_pages=1, **kwargs):
                    return ArticleResults(success=True, query=query)
                async def _parse_results(self, query):
                    return ArticleResults(success=True, query=query)

            pattern = ConcreteNews(mock_session, "example.com")
            assert pattern.domain == "example.com"
            assert pattern._max_pages == 1
            assert pattern._selectors.has_domain("example.com")
            sel = pattern._selectors.resolve("example.com", "article_content")
            assert sel is not None

    @pytest.mark.asyncio
    async def test_load_article_returns_article(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_network_idle = AsyncMock()

            class ConcreteNews(NewsPattern):
                async def execute(self, query, max_pages=1, **kwargs):
                    return ArticleResults(success=True, query=query)
                async def _parse_results(self, query):
                    return ArticleResults(success=True, query=query)

            pattern = ConcreteNews(mock_session, "example.com")
            article = await pattern.load_article("https://example.com/article-1")
            assert isinstance(article, ArticleData)
            assert article.url == "https://example.com/article-1"
            assert article.source_domain == "example.com"

    @pytest.mark.asyncio
    async def test_get_comments_empty(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_network_idle = AsyncMock()

            class ConcreteNews(NewsPattern):
                async def execute(self, query, max_pages=1, **kwargs):
                    return ArticleResults(success=True, query=query)
                async def _parse_results(self, query):
                    return ArticleResults(success=True, query=query)

            pattern = ConcreteNews(mock_session, "example.com")
            comments = await pattern.get_comments("https://example.com/a")
            assert comments == []

    @pytest.mark.asyncio
    async def test_execute_abstract_raises(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            pattern = NewsPattern(mock_session, "example.com")
            with pytest.raises(NotImplementedError):
                await pattern.execute("q")

    @pytest.mark.asyncio
    async def test_parse_results_abstract_raises(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            pattern = NewsPattern(mock_session, "example.com")
            with pytest.raises(NotImplementedError):
                await pattern._parse_results("q")


# ─────────────────────── ZhihuNewsPattern ───────────────────────

class TestZhihuNewsPattern:
    """ZhihuNewsPattern 单元测试"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_init_registers_selectors(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            pattern = ZhihuNewsPattern(mock_session)
            assert pattern.domain == "zhihu.com"
            for name in ["result_item", "article_content", "comment_section"]:
                sel = pattern._selectors.resolve("zhihu.com", name)
                assert sel is not None, f"Selector '{name}' not registered"

    def test_search_url_config(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            pattern = ZhihuNewsPattern(mock_session)
            assert "zhihu.com" in pattern._config["search_url"]

    def test_custom_config_override(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            custom = {"search_url": "https://custom.zhihu.com?q={query}"}
            pattern = ZhihuNewsPattern(mock_session, config=custom)
            assert pattern._config["search_url"] == "https://custom.zhihu.com?q={query}"

    @pytest.mark.asyncio
    async def test_execute_returns_error_on_exception(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("network error"))
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            mock_wait_cls.return_value.wait_for_network_idle = AsyncMock()
            pattern = ZhihuNewsPattern(mock_session)
            result = await pattern.execute("test query")
            assert result.success is False
            assert "network error" in result.error_message
            assert result.pattern_used == "ZhihuNewsPattern"

    @pytest.mark.asyncio
    async def test_get_hot_list_returns_empty_on_error(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("error"))
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            mock_wait_cls.return_value.wait_for_network_idle = AsyncMock()
            pattern = ZhihuNewsPattern(mock_session)
            hot = await pattern.get_hot_list()
            assert hot == []


# ─────────────────────── ToutiaoNewsPattern ───────────────────────

class TestToutiaoNewsPattern:
    """ToutiaoNewsPattern 单元测试"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_init_registers_selectors(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            pattern = ToutiaoNewsPattern(mock_session)
            assert pattern.domain == "toutiao.com"
            for name in ["result_item", "article_content", "comment_section"]:
                sel = pattern._selectors.resolve("toutiao.com", name)
                assert sel is not None, f"Selector '{name}' not registered"

    def test_search_url_config(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            pattern = ToutiaoNewsPattern(mock_session)
            assert "keyword=" in pattern._config["search_url"]

    def test_custom_config_override(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            custom = {"search_url": "https://custom.toutiao.com/?kw={query}"}
            pattern = ToutiaoNewsPattern(mock_session, config=custom)
            assert pattern._config["search_url"] == "https://custom.toutiao.com/?kw={query}"

    @pytest.mark.asyncio
    async def test_execute_returns_error_on_exception(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("timeout"))
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            mock_wait_cls.return_value.wait_for_network_idle = AsyncMock()
            pattern = ToutiaoNewsPattern(mock_session)
            result = await pattern.execute("search term")
            assert result.success is False
            assert "timeout" in result.error_message
            assert result.pattern_used == "ToutiaoNewsPattern"

    @pytest.mark.asyncio
    async def test_get_hot_list_returns_empty_on_error(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("err"))
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            mock_wait_cls.return_value.wait_for_network_idle = AsyncMock()
            pattern = ToutiaoNewsPattern(mock_session)
            hot = await pattern.get_hot_list()
            assert hot == []


# ─────────────────────── Integration ───────────────────────

class TestNewsPatternIntegration:
    """Pattern 协作集成测试（mock）"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_zhihu_and_toutiao_selectors_are_isolated(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            zhihu = ZhihuNewsPattern(mock_session)
            tt = ToutiaoNewsPattern(mock_session)

            # 两个 pattern 的配置应相互独立（各自维护独立 config）
            assert zhihu._config["search_url"] != tt._config["search_url"]
            assert "zhihu.com" in zhihu._config["search_url"]
            assert "keyword=" in tt._config["search_url"]
            # 选择器对象实例不同
            assert zhihu._get_selector("result_item") is not tt._get_selector("result_item")

    @pytest.mark.asyncio
    async def test_news_pattern_subclass_relationship(self):
        """验证继承关系"""
        from src.interaction_patterns import NewsPattern as NP
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as mock_wait_cls:
            mock_wait_cls.return_value.wait_for_selector = AsyncMock()
            assert issubclass(ZhihuNewsPattern, NP)
            assert issubclass(ToutiaoNewsPattern, NP)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
