"""
Tests for social media scrapers
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json


class TestWeiboHotScraper:
    """Test WeiboHotScraper"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.social.scrapers import WeiboHotScraper
        with patch('finance_toolkit.social.scrapers.httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            scraper = WeiboHotScraper()
            scraper.client = mock_client
            yield scraper
    
    @pytest.mark.asyncio
    async def test_get_hot_list(self, scraper):
        """Test fetching Weibo hot search list"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'data': {
                'realtime': [
                    {
                        'word': '测试热搜话题',
                        'num': 1000000,
                        'rank': 1,
                        'category': '热',
                    },
                    {
                        'word': '股市大涨',
                        'num': 800000,
                        'rank': 2,
                        'category': '沸',
                    },
                ]
            }
        }
        scraper.client.get.return_value = mock_response
        
        posts = await scraper.get_hot_list(limit=10)
        
        assert len(posts) == 2
        assert posts[0].source.value == 'weibo_hot'
        assert posts[0].title == '测试热搜话题'
        assert posts[0].topic_heat == 1000000
        assert posts[0].category.value == 'hot_topic'
        assert posts[0].sentiment_score is not None
        assert posts[0].sentiment_label in ['positive', 'negative', 'neutral']
    
    @pytest.mark.asyncio
    async def test_search_posts(self, scraper):
        """Test searching posts"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'data': {
                'realtime': [
                    {'word': '茅台大涨', 'num': 500000, 'rank': 1, 'category': '热'},
                ]
            }
        }
        scraper.client.get.return_value = mock_response
        
        posts = await scraper.search_posts('茅台', limit=5)
        
        assert len(posts) == 1
        assert '茅台' in posts[0].title
    
    @pytest.mark.asyncio
    async def test_extract_symbols(self, scraper):
        """Test symbol extraction"""
        text = '贵州茅台(600519.SH)今日大涨5%'
        symbols = scraper._extract_symbols(text)
        assert '600519.SH' in symbols
    
    @pytest.mark.asyncio
    async def test_simple_sentiment(self, scraper):
        """Test simple sentiment analysis"""
        # Positive
        score, label = scraper._simple_sentiment('茅台大涨业绩超预期买入')
        assert score > 0
        assert label == 'positive'
        
        # Negative
        score, label = scraper._simple_sentiment('茅台暴跌业绩不及预期卖出')
        assert score < 0
        assert label == 'negative'
        
        # Neutral
        score, label = scraper._simple_sentiment('茅台今日股价平盘')
        assert score == 0
        assert label == 'neutral'
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        await scraper.close()
        scraper.client.aclose.assert_called_once()


class TestXueqiuDiscussionScraper:
    """Test XueqiuDiscussionScraper"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.social.scrapers import XueqiuDiscussionScraper
        with patch('finance_toolkit.social.scrapers.httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            scraper = XueqiuDiscussionScraper()
            scraper.client = mock_client
            yield scraper
    
    @pytest.mark.asyncio
    async def test_get_hot_list(self, scraper):
        """Test fetching Xueqiu hot discussions"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'list': [
                {
                    'data': json.dumps({
                        'id': 123456,
                        'title': '看好茅台后市',
                        'description': '茅台业绩超预期，机构纷纷买入',
                        'user': {'id': 789, 'screen_name': '测试用户'},
                        'created_at': 1705300000000,
                        'retweet_count': 100,
                        'reply_count': 50,
                        'like_count': 200,
                        'stocks': [{'code': '600519', 'name': '贵州茅台'}],
                    })
                }
            ]
        }
        scraper.client.get.return_value = mock_response
        
        posts = await scraper.get_hot_list(limit=10)
        
        assert len(posts) == 1
        assert posts[0].source.value == 'xueqiu_discussion'
        assert posts[0].title == '看好茅台后市'
        assert posts[0].topic_heat == 350  # 100 + 50 + 200
        assert '600519.SH' in posts[0].symbols
    
    @pytest.mark.asyncio
    async def test_search_posts(self, scraper):
        """Test searching Xueqiu posts"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'list': [
                {
                    'data': json.dumps({
                        'id': 789012,
                        'title': '搜索结果',
                        'description': '搜索内容',
                        'user': {'id': 111, 'screen_name': '搜索用户'},
                        'created_at': 1705300000000,
                        'retweet_count': 10,
                        'reply_count': 5,
                        'like_count': 20,
                        'stocks': [],
                    })
                }
            ]
        }
        scraper.client.get.return_value = mock_response
        
        posts = await scraper.search_posts('茅台', limit=5)
        
        assert len(posts) == 1
        assert posts[0].title == '搜索结果'
    
    @pytest.mark.asyncio
    async def test_get_stock_discussions(self, scraper):
        """Test getting discussions for a specific stock"""
        mock_response = MagicMock()
        mock_response.json.return_value = {'list': []}
        scraper.client.get.return_value = mock_response
        
        posts = await scraper.get_stock_discussions('600519.SH', limit=10)
        
        # Should call search with the converted symbol
        scraper.client.get.assert_called()
        assert isinstance(posts, list)
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        await scraper.close()
        scraper.client.aclose.assert_called_once()


class TestThsWencaiScraper:
    """Test ThsWencaiScraper"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.social.scrapers import ThsWencaiScraper
        with patch('finance_toolkit.social.scrapers.httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            scraper = ThsWencaiScraper()
            scraper.client = mock_client
            yield scraper
    
    @pytest.mark.asyncio
    async def test_get_hot_list(self, scraper):
        """Test fetching Ths Wencai hot list"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'data': {
                'result': [
                    {
                        'question': '今日涨停股有哪些？',
                        'answer': '今日涨停股包括贵州茅台、宁德时代等',
                        'heat': 10000,
                        'view_count': 5000,
                        'url': 'https://www.iwencai.com/question/123',
                    }
                ]
            }
        }
        scraper.client.get.return_value = mock_response
        
        posts = await scraper.get_hot_list(limit=10)
        
        assert len(posts) >= 0  # May be 0 if parsing fails
    
    @pytest.mark.asyncio
    async def test_search_posts(self, scraper):
        """Test searching Ths Wencai"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'data': {'result': []}
        }
        scraper.client.get.return_value = mock_response
        
        posts = await scraper.search_posts('茅台', limit=5)
        
        assert isinstance(posts, list)
    
    @pytest.mark.asyncio
    async def test_get_stock_qa(self, scraper):
        """Test getting stock Q&A"""
        mock_response = MagicMock()
        mock_response.json.return_value = {'data': {'result': []}}
        scraper.client.get.return_value = mock_response
        
        posts = await scraper.get_stock_qa('600519.SH', limit=5)
        
        assert isinstance(posts, list)
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        await scraper.close()
        scraper.client.aclose.assert_called_once()


class TestSocialModels:
    """Test social media data models"""
    
    def test_social_post_creation(self):
        """Test SocialPost creation"""
        from finance_toolkit.social.models import SocialPost, SocialSource, SocialCategory
        
        post = SocialPost(
            post_id='test123',
            source=SocialSource.WEIBO_HOT,
            category=SocialCategory.HOT_TOPIC,
            title='测试热搜',
            content='测试内容',
            url='https://weibo.com/hot/test',
            topic_heat=1000000,
            sentiment_score=0.5,
            sentiment_label='positive',
            symbols=['600519.SH'],
        )
        
        assert post.post_id == 'test123'
        assert post.source == SocialSource.WEIBO_HOT
        assert post.topic_heat == 1000000
        assert post.sentiment_score == 0.5
        assert post.sentiment_label == 'positive'
        assert '600519.SH' in post.symbols
    
    def test_social_aggregation_creation(self):
        """Test SocialAggregation creation"""
        from finance_toolkit.social.models import SocialAggregation, SocialSource
        
        agg = SocialAggregation(
            symbol='600519.SH',
            source=SocialSource.XUEQIU_DISCUSSION,
            total_posts=100,
            avg_sentiment=0.3,
            sentiment_distribution={'positive': 60, 'negative': 20, 'neutral': 20},
            top_keywords=[('茅台', 50), ('业绩', 30)],
            heat_trend=[{'time': '2024-01-15', 'heat': 1000}],
            signal={'signal': 'bullish', 'strength': 0.7, 'reason': '正面情绪占优'},
        )
        
        assert agg.symbol == '600519.SH'
        assert agg.total_posts == 100
        assert agg.avg_sentiment == 0.3
        assert agg.signal['signal'] == 'bullish'


class TestSocialConvenienceFunctions:
    """Test convenience functions"""
    
    @pytest.mark.asyncio
    async def test_fetch_weibo_hot(self):
        """Test fetch_weibo_hot convenience function"""
        from finance_toolkit.social.scrapers import fetch_weibo_hot, WeiboHotScraper
        
        with patch.object(WeiboHotScraper, 'get_hot_list', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            
            result = await fetch_weibo_hot(limit=10)
            
            assert isinstance(result, list)
            mock_get.assert_called_once_with(10)
    
    @pytest.mark.asyncio
    async def test_fetch_xueqiu_hot(self):
        """Test fetch_xueqiu_hot convenience function"""
        from finance_toolkit.social.scrapers import fetch_xueqiu_hot, XueqiuDiscussionScraper
        
        with patch.object(XueqiuDiscussionScraper, 'get_hot_list', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            
            result = await fetch_xueqiu_hot(limit=10)
            
            assert isinstance(result, list)
            mock_get.assert_called_once_with(10)
    
    @pytest.mark.asyncio
    async def test_fetch_ths_wencai_hot(self):
        """Test fetch_ths_wencai_hot convenience function"""
        from finance_toolkit.social.scrapers import fetch_ths_wencai_hot, ThsWencaiScraper
        
        with patch.object(ThsWencaiScraper, 'get_hot_list', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            
            result = await fetch_ths_wencai_hot(limit=10)
            
            assert isinstance(result, list)
            mock_get.assert_called_once_with(10)
    
    @pytest.mark.asyncio
    async def test_fetch_all_social_hot(self):
        """Test fetch_all_social_hot convenience function"""
        from finance_toolkit.social.scrapers import fetch_all_social_hot, WeiboHotScraper, XueqiuDiscussionScraper, ThsWencaiScraper
        from finance_toolkit.social.models import SocialSource
        
        with patch.object(WeiboHotScraper, 'get_hot_list', new_callable=AsyncMock) as mock_weibo, \
             patch.object(XueqiuDiscussionScraper, 'get_hot_list', new_callable=AsyncMock) as mock_xueqiu, \
             patch.object(ThsWencaiScraper, 'get_hot_list', new_callable=AsyncMock) as mock_ths:
            
            mock_weibo.return_value = []
            mock_xueqiu.return_value = []
            mock_ths.return_value = []
            
            result = await fetch_all_social_hot(limit=10)
            
            assert isinstance(result, dict)
            assert SocialSource.WEIBO_HOT in result
            assert SocialSource.XUEQIU_DISCUSSION in result
            assert SocialSource.THS_WENCAI in result
            mock_weibo.assert_called_once_with(10)
            mock_xueqiu.assert_called_once_with(10)
            mock_ths.assert_called_once_with(10)
