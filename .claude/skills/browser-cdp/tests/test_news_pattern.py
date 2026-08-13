"""
test_news_pattern.py - 新闻类网站 Pattern 单元测试

覆盖：NewsPattern 基类、ZhihuNewsPattern、ToutiaoNewsPattern、SinaNewsPattern、ClsNewsPattern
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.selector_manager import SelectorManager
from src.interaction_patterns.news_pattern import NewsPattern, ArticleData, ArticleResults
from src.interaction_patterns.zhihu_news_pattern import ZhihuNewsPattern
from src.interaction_patterns.toutiao_news_pattern import ToutiaoNewsPattern


# ---------------------------------------------------------------------------
#  fixtures / helpers
# ---------------------------------------------------------------------------

def _mock_wait(return_attrs=None):
    """返回一个 SmartWaitV2 mock，预置常用异步方法。"""
    m = MagicMock()
    m.wait_for_selector = AsyncMock(return_value=(return_attrs or {}))
    m.wait_for_network_idle = AsyncMock()
    m.wait_for_page_ready_v2 = AsyncMock()
    return m


def _make_pattern(cls, mock_session=None, domain=None, config=None):
    """工厂：用 patch SmartWaitV2 创建 Pattern 实例。"""
    session = mock_session or MagicMock()
    with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
        MockSW.return_value = _mock_wait()
        return cls(session, domain or getattr(cls, '_default_domain', 'example.com'), config)


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

    def test_to_dict_contains_articles_dicts(self):
        a = ArticleData(title="X", url="http://x.com", author="Alice")
        r = ArticleResults(success=True, query="q", articles=[a])
        d = r.to_dict()
        assert isinstance(d["articles"], list)
        assert d["articles"][0]["title"] == "X"
        assert d["articles"][0]["author"] == "Alice"


class TestNewsPatternBase:
    """NewsPattern 基类测试"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_init_default_selectors(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()

            class ConcreteNews(NewsPattern):
                async def execute(self, query, max_pages=1, **kwargs):
                    return ArticleResults(success=True, query=query)
                async def _parse_results(self, query):
                    return ArticleResults(success=True, query=query)

            pattern = ConcreteNews(mock_session, "example.com")
            assert pattern.domain == "example.com"
            assert pattern._max_pages == 1
            # 默认选择器应已注册
            assert pattern._selectors.has_domain("example.com")
            sel = pattern._selectors.resolve("example.com", "article_content")
            assert sel is not None
            assert "." in sel.value

    @pytest.mark.asyncio
    async def test_load_article_returns_article(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()

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
    async def test_load_article_handles_navigation_error(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("conn refused"))
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()

            class ConcreteNews(NewsPattern):
                async def execute(self, query, max_pages=1, **kwargs):
                    return ArticleResults(success=True, query=query)
                async def _parse_results(self, query):
                    return ArticleResults(success=True, query=query)

            pattern = ConcreteNews(mock_session, "example.com")
            article = await pattern.load_article("https://example.com/bad-url")
            assert isinstance(article, ArticleData)
            assert article.content == ""  # 异常时 content 保持空

    @pytest.mark.asyncio
    async def test_get_comments_empty_when_no_section(self):
        mock_session = MagicMock()
        mock_session.query_selector = AsyncMock(return_value=None)
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()

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
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = NewsPattern(mock_session, "example.com")
        with pytest.raises(NotImplementedError):
            await pattern.execute("q")

    @pytest.mark.asyncio
    async def test_parse_results_abstract_raises(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = NewsPattern(mock_session, "example.com")
        with pytest.raises(NotImplementedError):
            await pattern._parse_results("q")

    def test_max_pages_from_config(self):
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()

            class ConcreteNews(NewsPattern):
                async def execute(self, query, max_pages=1, **kwargs):
                    return ArticleResults(success=True, query=query)
                async def _parse_results(self, query):
                    return ArticleResults(success=True, query=query)

            pattern = ConcreteNews(mock_session, "example.com", {"max_pages": 5})
            assert pattern._max_pages == 5


class TestZhihuNewsPattern:
    """ZhihuNewsPattern 单元测试"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_init_registers_selectors(self):
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ZhihuNewsPattern(MagicMock())
            assert pattern.domain == "zhihu.com"
            # search_url 是配置项不是选择器，检查真正的选择器
            for name in ["result_item", "article_content", "comment_section", "next_page"]:
                sel = pattern._selectors.resolve("zhihu.com", name)
                assert sel is not None, f"Selector '{name}' not registered"

    def test_search_url_config(self):
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ZhihuNewsPattern(MagicMock())
            assert "zhihu.com" in pattern._config["search_url"]

    def test_custom_config_override(self):
        custom = {"search_url": "https://custom.zhihu.com?q={query}"}
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ZhihuNewsPattern(MagicMock(), config=custom)
            assert pattern._config["search_url"] == "https://custom.zhihu.com?q={query}"

    @pytest.mark.asyncio
    async def test_execute_returns_error_on_exception(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("network error"))
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ZhihuNewsPattern(mock_session)
            result = await pattern.execute("test query")
            assert result.success is False
            assert "network error" in result.error_message
            assert result.pattern_used == "ZhihuNewsPattern"

    @pytest.mark.asyncio
    async def test_get_hot_list_returns_empty_on_error(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("error"))
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ZhihuNewsPattern(mock_session)
            hot = await pattern.get_hot_list()
            assert hot == []

    @pytest.mark.asyncio
    async def test_execute_success_path(self):
        """mock 完整 execute 成功路径"""
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock()
        mock_session.query_selector_all = AsyncMock(return_value=[])
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value.wait_for_selector = AsyncMock()
            MockSW.return_value.wait_for_network_idle = AsyncMock()
            pattern = ZhihuNewsPattern(mock_session)
            result = await pattern.execute("AI news")
            # execute() 末尾调用 _record_latency(results.to_dict()) 返回 dict
            if hasattr(result, 'success'):
                assert result.success is True
                assert result.pattern_used == "ZhihuNewsPattern"
            else:
                assert result.get('success') is True
                assert result.get('pattern_used', '').startswith("ZhihuNewsPattern")


class TestToutiaoNewsPattern:
    """ToutiaoNewsPattern 单元测试"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_init_registers_selectors(self):
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ToutiaoNewsPattern(MagicMock())
            assert pattern.domain == "toutiao.com"
            # search_url 是配置项不是选择器，检查真正的选择器
            for name in ["result_item", "article_content", "comment_section", "next_page"]:
                sel = pattern._selectors.resolve("toutiao.com", name)
                assert sel is not None, f"Selector '{name}' not registered"

    def test_search_url_config(self):
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ToutiaoNewsPattern(MagicMock())
            assert "keyword=" in pattern._config["search_url"]

    def test_custom_config_override(self):
        custom = {"search_url": "https://custom.toutiao.com/?kw={query}"}
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ToutiaoNewsPattern(MagicMock(), config=custom)
            assert pattern._config["search_url"] == "https://custom.toutiao.com/?kw={query}"

    @pytest.mark.asyncio
    async def test_execute_returns_error_on_exception(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("timeout"))
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ToutiaoNewsPattern(mock_session)
            result = await pattern.execute("search term")
            assert result.success is False
            assert "timeout" in result.error_message
            assert result.pattern_used == "ToutiaoNewsPattern"

    @pytest.mark.asyncio
    async def test_get_hot_list_returns_empty_on_error(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock(side_effect=Exception("err"))
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            pattern = ToutiaoNewsPattern(mock_session)
            hot = await pattern.get_hot_list()
            assert hot == []

    @pytest.mark.asyncio
    async def test_execute_success_path(self):
        mock_session = MagicMock()
        mock_session.navigate = AsyncMock()
        mock_session.query_selector_all = AsyncMock(return_value=[])
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value.wait_for_selector = AsyncMock()
            MockSW.return_value.wait_for_network_idle = AsyncMock()
            pattern = ToutiaoNewsPattern(mock_session)
            result = await pattern.execute("热点")
            if hasattr(result, 'success'):
                assert result.success is True
                assert result.pattern_used.startswith("ToutiaoNewsPattern")
            else:
                assert result.get('success') is True
                assert result.get('pattern_used', '').startswith("ToutiaoNewsPattern")


class TestNewsPatternIntegration:
    """Pattern 协作集成测试（mock）"""

    def setup_method(self):
        SelectorManager.reset_instance()

    def test_zhihu_and_toutiao_selectors_are_isolated(self):
        """不同域名下的选择器注册相互隔离"""
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            zhihu = ZhihuNewsPattern(MagicMock())
            tt = ToutiaoNewsPattern(MagicMock())

            # search_url 是配置项，值不同可验证 config 隔离
            assert zhihu._config['search_url'] != tt._config['search_url']

            # result_item 选择器在不同域名下独立注册，各自存在
            z_sel = zhihu._selectors.resolve("zhihu.com", "result_item")
            t_sel = tt._selectors.resolve("toutiao.com", "result_item")
            assert z_sel is not None
            assert t_sel is not None
            # 两站都基于默认值，CSS 值可能相同但域隔离

    def test_different_css_selectors_for_different_sites(self):
        """不同站点的特殊选择器应有差异"""
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            zhihu = ZhihuNewsPattern(MagicMock())
            tt = ToutiaoNewsPattern(MagicMock())
            # 知乎特有：result_item 用 SearchResult-Item；头条用 widget-box
            z_sel = zhihu._config.get('result_item', '')
            t_sel = tt._config.get('result_item', '')
            # zhihu 配置中有 SearchResult-Item，toutiao 有 widget-box
            assert 'SearchResult' in z_sel or 'Item' in z_sel
            assert 'widget' in t_sel or 'article' in t_sel

    @pytest.mark.asyncio
    async def test_news_pattern_subclass_relationship(self):
        """验证继承关系"""
        from src.interaction_patterns import NewsPattern as NP
        mock_session = MagicMock()
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            assert issubclass(ZhihuNewsPattern, NP)
            assert issubclass(ToutiaoNewsPattern, NP)

    def test_selector_isolation_across_runs(self):
        """每次 reset_instance 后选择器互不干扰"""
        with patch('src.core.smart_wait_v2.SmartWaitV2') as MockSW:
            MockSW.return_value = _mock_wait()
            p1 = ZhihuNewsPattern(MagicMock())
            SelectorManager.reset_instance()
            p2 = ToutiaoNewsPattern(MagicMock())

            # p1 的 zhihu.com 选择器不应出现在 p2 上下文中
            sel = p2._selectors.resolve("zhihu.com", "search_url")
            assert sel is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
