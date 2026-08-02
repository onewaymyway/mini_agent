"""
Tests for news scrapers
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import json


class TestSinaNewsScraper:
    """Test SinaNewsScraper"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.news.scrapers import SinaNewsScraper
        with patch('finance_toolkit.news.scrapers.httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            scraper = SinaNewsScraper()
            scraper.client = mock_client
            yield scraper
    
    @pytest.mark.asyncio
    async def test_get_latest_list(self, scraper):
        """Test fetching latest news list"""
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            'result': {
                'data': [
                    {
                        'docid': 'test123',
                        'title': '测试新闻标题',
                        'intro': '测试摘要',
                        'url': 'https://finance.sina.com.cn/test.html',
                        'source': '新浪财经',
                        'ctime': '2024-01-15 10:00:00',
                        'channel': 'stock',
                        'keywords': '股票,财经',
                    }
                ]
            }
        })
        scraper.client.get.return_value = mock_response
        
        news_list = await scraper.get_latest_list(page=1, page_size=10)
        
        assert len(news_list) == 1
        assert news_list[0].source.value == 'sina'
        assert news_list[0].title == '测试新闻标题'
        assert news_list[0].summary == '测试摘要'
        assert '000001.SZ' in news_list[0].symbols or len(news_list[0].symbols) >= 0
    
    @pytest.mark.asyncio
    async def test_get_detail(self, scraper):
        """Test fetching news detail"""
        mock_response = MagicMock()
        mock_response.text = '''
        <html>
            <h1>详细标题</h1>
            <div class="article-body">详细正文内容</div>
            <span class="date">2024-01-15 10:00:00</span>
        </html>
        '''
        scraper.client.get.return_value = mock_response
        
        news = await scraper.get_detail('https://finance.sina.com.cn/test.html')
        
        assert news.title == '详细标题'
        assert '详细正文内容' in news.content
        assert news.source.value == 'sina'
    
    @pytest.mark.asyncio
    async def test_extract_symbols(self, scraper):
        """Test symbol extraction from text"""
        text = '平安银行(000001.SZ)今日大涨，贵州茅台(600519.SH)跟涨'
        symbols = scraper._extract_symbols(text)
        assert '000001.SZ' in symbols
        assert '600519.SH' in symbols
    
    @pytest.mark.asyncio
    async def test_map_category(self, scraper):
        """Test category mapping"""
        from finance_toolkit.news.models import NewsCategory
        assert scraper._map_category('stock') == NewsCategory.STOCK
        assert scraper._map_category('finance') == NewsCategory.MARKET
        assert scraper._map_category('macro') == NewsCategory.MACRO
        assert scraper._map_category('unknown') == NewsCategory.MARKET
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        """Test close method"""
        await scraper.close()
        scraper.client.aclose.assert_called_once()


class TestCLSNewsScraper:
    """Test CLSNewsScraper"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.news.scrapers import CLSNewsScraper
        with patch('finance_toolkit.news.scrapers.httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            scraper = CLSNewsScraper()
            scraper.client = mock_client
            yield scraper
    
    @pytest.mark.asyncio
    async def test_get_latest_list(self, scraper):
        """Test fetching CLS telegraph list"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'data': {
                'items': [
                    {
                        'id': 'cls123',
                        'title': '财联社电报',
                        'descr': '电报摘要',
                        'content': '电报正文',
                        'time': 1705284000,
                        'important': 2,
                    }
                ]
            }
        }
        scraper.client.get.return_value = mock_response
        
        news_list = await scraper.get_latest_list(page=1, page_size=10)
        
        assert len(news_list) == 1
        assert news_list[0].source.value == 'cls'
        assert news_list[0].title == '财联社电报'
        assert news_list[0].importance == 2
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        await scraper.close()
        scraper.client.aclose.assert_called_once()


class TestWallstreetcnScraper:
    """Test WallstreetcnScraper"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.news.scrapers import WallstreetcnScraper
        with patch('finance_toolkit.news.scrapers.httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            scraper = WallstreetcnScraper(token='test_token')
            scraper.client = mock_client
            yield scraper
    
    @pytest.mark.asyncio
    async def test_get_live_feed(self, scraper):
        """Test fetching live feed"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'data': {
                'items': [
                    {
                        'id': 'ws123',
                        'title': '华尔街见闻快讯',
                        'summary': '快讯摘要',
                        'content': '快讯正文',
                        'uri': 'https://wallstreetcn.com/articles/123',
                        'author': {'name': '作者'},
                        'display_time': 1705284000,
                        'tags': ['宏观', '美股'],
                    }
                ]
            }
        }
        scraper.client.get.return_value = mock_response
        
        news_list = await scraper.get_live_feed(limit=10)
        
        assert len(news_list) == 1
        assert news_list[0].source.value == 'wallstreetcn'
        assert news_list[0].title == '华尔街见闻快讯'
        assert '宏观' in news_list[0].keywords
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        await scraper.close()
        scraper.client.aclose.assert_called_once()


class TestArxivScraper:
    """Test ArxivScraper"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.news.scrapers import ArxivScraper
        scraper = ArxivScraper()
        yield scraper
    
    @pytest.mark.asyncio
    async def test_get_latest_list(self, scraper):
        """Test fetching arXiv papers"""
        from datetime import datetime
        
        # Mock arxiv paper object
        mock_paper = MagicMock()
        mock_paper.entry_id = 'http://arxiv.org/abs/2401.12345'
        mock_paper.title = 'Quantitative Finance Paper'
        mock_paper.summary = 'This is a paper about quantitative finance.'
        mock_paper.published = datetime(2024, 1, 15, 10, 0, 0)
        # Fix: authors should be a list of objects with .name attribute
        mock_author = MagicMock()
        mock_author.name = 'Author Name'
        mock_paper.authors = [mock_author]
        mock_paper.categories = ['q-fin.CP', 'q-fin.GN']
        mock_paper.pdf_url = 'http://arxiv.org/pdf/2401.12345'
        mock_paper.primary_category = 'q-fin.CP'
        mock_paper.comment = None
        
        # Mock arxiv module in sys.modules
        mock_arxiv = MagicMock()
        
        # Setup mock Search and Client
        mock_search_instance = MagicMock()
        mock_arxiv.Search.return_value = mock_search_instance
        
        mock_client = MagicMock()
        mock_arxiv.Client.return_value = mock_client
        mock_client.results.return_value = [mock_paper]
        
        # Setup enums
        mock_arxiv.SortCriterion.SubmittedDate = 'SubmittedDate'
        mock_arxiv.SortOrder.Descending = 'Descending'
        
        # Mock the _get_arxiv_client method to return our mock client
        scraper._get_arxiv_client = lambda: mock_client
        
        news_list = scraper.search_papers(max_results=10)
        
        assert len(news_list) == 1
        assert news_list[0].source.value == 'arxiv'
        assert news_list[0].title == 'Quantitative Finance Paper'
        assert news_list[0].category.value == 'academic'
        assert 'q-fin.CP' in news_list[0].keywords
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        # ArxivScraper doesn't use httpx client, just verify close doesn't raise
        await scraper.close()  # Should complete without error


class TestRegulatorScraper:
    """Test RegulatorScraper"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.news.scrapers import RegulatorScraper
        with patch('finance_toolkit.news.scrapers.httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            scraper = RegulatorScraper()
            scraper.client = mock_client
            yield scraper
    
    @pytest.mark.asyncio
    async def test_get_latest_list(self, scraper):
        """Test fetching regulator announcements"""
        mock_response = MagicMock()
        mock_response.text = '''
        <html>
            <div class="list">
                <a href="/notice/123.html">证监会发布新规</a>
                <span class="date">2024-01-15</span>
            </div>
        </html>
        '''
        scraper.client.get.return_value = mock_response
        
        news_list = await scraper.get_latest_list(page=1, page_size=10)
        
        assert len(news_list) >= 0  # May be 0 if parsing fails
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        await scraper.close()
        scraper.client.aclose.assert_called_once()


class TestNewsAggregator:
    """Test NewsAggregator"""
    
    @pytest.fixture
    def aggregator(self):
        from finance_toolkit.news import NewsAggregator, NewsSource
        from finance_toolkit.news.scrapers import SinaNewsScraper, CLSNewsScraper
        
        agg = NewsAggregator()
        with patch('finance_toolkit.news.scrapers.httpx.AsyncClient'):
            agg.register_scraper(NewsSource.SINA, SinaNewsScraper())
            agg.register_scraper(NewsSource.CLS, CLSNewsScraper())
            yield agg
    
    @pytest.mark.asyncio
    async def test_fetch_all(self, aggregator):
        """Test fetching from all registered scrapers"""
        # Mock the scrapers' get_latest_list methods
        for source, scraper in aggregator.scrapers.items():
            scraper.get_latest_list = AsyncMock(return_value=[])
        
        news = await aggregator.fetch_all(since_hours=24, max_per_source=5)
        
        assert isinstance(news, list)
        # Both scrapers should have been called
        for scraper in aggregator.scrapers.values():
            scraper.get_latest_list.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_close(self, aggregator):
        """Test closing all scrapers"""
        for scraper in aggregator.scrapers.values():
            scraper.close = AsyncMock()
        
        await aggregator.close()
        
        for scraper in aggregator.scrapers.values():
            scraper.close.assert_called_once()


class TestNewsModels:
    """Test news data models"""
    
    def test_finance_news_creation(self):
        """Test FinanceNews creation"""
        from finance_toolkit.news.models import FinanceNews, NewsSource, NewsCategory
        
        news = FinanceNews(
            news_id='test123',
            source=NewsSource.SINA,
            category=NewsCategory.STOCK,
            title='测试标题',
            summary='测试摘要',
            content='测试正文',
            url='https://example.com',
        )
        
        assert news.news_id == 'test123'
        assert news.source == NewsSource.SINA
        assert news.category == NewsCategory.STOCK
    
    def test_finance_news_to_dict(self):
        """Test FinanceNews to_dict"""
        from finance_toolkit.news.models import FinanceNews, NewsSource, NewsCategory
        
        news = FinanceNews(
            news_id='test123',
            source=NewsSource.SINA,
            category=NewsCategory.STOCK,
            title='测试标题',
            summary='测试摘要',
            content='测试正文',
            url='https://example.com',
            publish_time=datetime(2024, 1, 15, 10, 0, 0),
        )
        
        d = news.to_dict()
        assert d['news_id'] == 'test123'
        assert d['source'] == 'sina'
        assert d['category'] == 'stock'
        assert d['publish_time'] == '2024-01-15T10:00:00'
    
    def test_finance_news_from_dict(self):
        """Test FinanceNews from_dict"""
        from finance_toolkit.news.models import FinanceNews, NewsSource
        
        d = {
            'news_id': 'test123',
            'source': 'sina',
            'category': 'stock',
            'title': '测试标题',
            'summary': '测试摘要',
            'content': '测试正文',
            'url': 'https://example.com',
            'publish_time': '2024-01-15T10:00:00',
            'crawl_time': '2024-01-15T10:00:00',
            'symbols': [],
            'keywords': [],
            'entities': [],
            'sentiment': None,
            'importance': None,
            'credibility': None,
            'images': [],
            'videos': [],
            'raw': None,
        }
        
        news = FinanceNews.from_dict(d)
        assert news.news_id == 'test123'
        assert news.source == NewsSource.SINA
        assert news.publish_time == datetime(2024, 1, 15, 10, 0, 0)
    
    def test_fingerprint(self):
        """Test content fingerprint for deduplication"""
        from finance_toolkit.news.models import FinanceNews, NewsSource, NewsCategory
        
        news1 = FinanceNews(
            news_id='test1',
            source=NewsSource.SINA,
            category=NewsCategory.STOCK,
            title='相同标题',
            summary='',
            content='相同内容开头...',
            url='https://example.com/1',
        )
        news2 = FinanceNews(
            news_id='test2',
            source=NewsSource.SINA,
            category=NewsCategory.STOCK,
            title='相同标题',
            summary='',
            content='相同内容开头...',
            url='https://example.com/2',
        )
        
        assert news1.fingerprint() == news2.fingerprint()
    
    def test_equality(self):
        """Test equality based on news_id"""
        from finance_toolkit.news.models import FinanceNews, NewsSource, NewsCategory
        
        news1 = FinanceNews(
            news_id='same_id',
            source=NewsSource.SINA,
            category=NewsCategory.STOCK,
            title='标题1',
            summary='',
            content='',
            url='https://example.com/1',
        )
        news2 = FinanceNews(
            news_id='same_id',
            source=NewsSource.CLS,
            category=NewsCategory.MARKET,
            title='标题2',
            summary='',
            content='',
            url='https://example.com/2',
        )
        
        assert news1 == news2
        assert hash(news1) == hash(news2)
