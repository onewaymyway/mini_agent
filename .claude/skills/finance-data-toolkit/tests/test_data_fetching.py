"""
Tests for data_fetching module
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestFetchRealtimeQuote:
    """Test fetch_realtime_quote function"""
    
    @pytest.mark.asyncio
    async def test_fetch_realtime_quote_akshare(self, sample_akshare_quote):
        """Test fetching realtime quote from AKShare"""
        from finance_toolkit.data_fetching import fetch_realtime_quote
    
        with patch('finance_toolkit.data_fetching.fetchers._fetch_akshare_realtime') as mock_fetch:
            import pandas as pd
            df = pd.DataFrame([sample_akshare_quote])
            mock_fetch.return_value = df
            
            results = fetch_realtime_quote(['600000.SH', '000001.SZ'], source='akshare')
            
            assert len(results) == 1
            assert results[0].symbol == '600000.SH'
    
    @pytest.mark.asyncio
    async def test_fetch_realtime_quote_sina(self):
        """Test fetching realtime quote from Sina"""
        from finance_toolkit.data_fetching import fetch_realtime_quote
        
        with patch('finance_toolkit.data_fetching.fetchers.httpx.Client') as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = 'var hq_str_sh600000="浦发银行,10.50,10.45,10.50,10.55,10.40,100000,2000000,2024-01-15,15:00:00,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0";'
            mock_resp.encoding = 'gbk'
            mock_client.get.return_value = mock_resp
            
            results = fetch_realtime_quote(['600000.SH'], source='sina')
            
            assert len(results) == 1
            assert results[0].symbol == '600000.SH'
            assert results[0].payload['price'] == 10.50


class TestFetchKline:
    """Test fetch_kline function"""
    
    @pytest.mark.asyncio
    async def test_fetch_kline_sina_daily(self, sample_akshare_kline):
        """Test fetching daily K-line from Sina"""
        from finance_toolkit.data_fetching import fetch_kline
        
        # Mock urllib.request.urlopen (sina_kline_fetcher uses urllib, not httpx)
        # Note: urlopen is used as a context manager, so we need to mock __enter__ and __exit__
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'var=([{"day":"2024-01-01 00:00:00","open":10.0,"high":10.3,"low":9.9,"close":10.2,"volume":1000000},{"day":"2024-01-02 00:00:00","open":10.2,"high":10.4,"low":10.1,"close":10.3,"volume":1100000}]);'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *args: None
        
        with patch('urllib.request.urlopen', return_value=mock_resp):
            results = fetch_kline('600000.SH', period='daily', start='20240101', end='20240103', source='sina')
            
            assert len(results) == 2
            assert results[0]['close'] == 10.2
    
    @pytest.mark.asyncio
    async def test_fetch_kline_yahoo_minute(self):
        """Test fetching minute K-line from Yahoo (via AKShare fallback)"""
        from finance_toolkit.data_fetching import fetch_kline
        
        with patch('finance_toolkit.data_fetching.fetchers._fetch_akshare_kline') as mock_fetch:
            import pandas as pd
            df = pd.DataFrame([
                {'日期': '2024-01-01', '开盘': 150.0, '收盘': 150.1, '最高': 150.2, '最低': 149.9, '成交量': 10000},
                {'日期': '2024-01-02', '开盘': 150.1, '收盘': 150.2, '最高': 150.3, '最低': 150.0, '成交量': 11000},
            ])
            mock_fetch.return_value = df
            
            results = fetch_kline('AAPL', period='1m', source='akshare')
            
            assert len(results) == 2
            assert results[0]['close'] == 150.1


class TestFetchFinancial:
    """Test fetch_financial function"""
    
    @pytest.mark.asyncio
    async def test_fetch_financial_akshare(self):
        """Test fetching financial reports from AKShare"""
        from finance_toolkit.data_fetching import fetch_financial
        
        with patch('finance_toolkit.data_fetching.fetchers.ak.stock_financial_report_sina') as mock_financial:
            import pandas as pd
            df = pd.DataFrame([
                {'报告期': '2023-12-31', '营业收入': 1000000, '净利润': 100000},
            ])
            mock_financial.return_value = df
            
            results = fetch_financial('600000.SH', source='akshare')
            
            assert len(results) >= 1
            # Check that we got some financial data
            assert results[0].data_type == 'financial'
            assert results[0].symbol == '600000.SH'


class TestFetchDividend:
    """Test fetch_dividend function"""
    
    @pytest.mark.asyncio
    async def test_fetch_dividend_akshare(self):
        """Test fetching dividend data from AKShare"""
        from finance_toolkit.data_fetching import fetch_dividend
        
        with patch('finance_toolkit.data_fetching.fetchers.ak.stock_fhps_em') as mock_dividend:
            import pandas as pd
            df = pd.DataFrame([
                {'报告期': '2023-12-31', '每股分红': 0.5, '分红方案': '10派5元'},
            ])
            mock_dividend.return_value = df
            
            results = fetch_dividend('600000.SH', source='akshare')
            
            assert len(results) >= 1
            assert results[0].data_type == 'dividend'
            assert results[0].symbol == '600000.SH'


class TestFetchLHB:
    """Test fetch_lhb function"""
    
    @pytest.mark.asyncio
    async def test_fetch_lhb_akshare(self):
        """Test fetching dragon-tiger list from AKShare"""
        from finance_toolkit.data_fetching import fetch_lhb
        
        with patch('finance_toolkit.data_fetching.fetchers.ak.stock_lhb_detail_em') as mock_lhb:
            import pandas as pd
            df = pd.DataFrame([
                {'上榜日期': '2024-01-01', '上榜原因': '日涨幅偏离值达到7%', '买入金额': 10000000, '卖出金额': 5000000, '净额': 5000000, '营业部': '测试营业部'},
            ])
            mock_lhb.return_value = df
            
            results = fetch_lhb(symbol='600000.SH', source='akshare')
            
            assert len(results) >= 1
            assert results[0].data_type == 'lhb'
            assert results[0].symbol == '600000.SH'


class TestFetchNorthbound:
    """Test fetch_northbound function"""
    
    @pytest.mark.asyncio
    async def test_fetch_northbound_akshare(self):
        """Test fetching northbound capital from AKShare"""
        from finance_toolkit.data_fetching import fetch_northbound
        
        with patch('finance_toolkit.data_fetching.fetchers.ak.stock_hsgt_hist_em') as mock_northbound:
            import pandas as pd
            df = pd.DataFrame([
                {'日期': '2024-01-01', '沪股通净流入': 100000000, '深股通净流入': 50000000},
            ])
            mock_northbound.return_value = df
            
            results = fetch_northbound(source='akshare')
            
            assert len(results) >= 1
            assert results[0].data_type == 'northbound'
            assert results[0].symbol == 'NORTHBOUND'


class TestFetchStockBasic:
    """Test fetch_stock_basic function"""
    
    @pytest.mark.asyncio
    async def test_fetch_stock_basic_akshare(self):
        """Test fetching stock basic info from AKShare"""
        from finance_toolkit.data_fetching import fetch_stock_basic
        
        with patch('finance_toolkit.data_fetching.fetchers._fetch_akshare_stock_basic') as mock_basic:
            import pandas as pd
            df = pd.DataFrame([
                {'代码': '600000', '名称': '浦发银行', '最新价': 10.5},
                {'代码': '000001', '名称': '平安银行', '最新价': 12.3},
            ])
            mock_basic.return_value = df
            
            results = fetch_stock_basic(source='akshare')
            
            assert len(results) >= 1
            assert results[0].data_type == 'stock_basic'
            assert 'name' in results[0].payload


class TestGubaScrapers:
    """Test Guba (Eastmoney stock forum) scrapers"""
    
    @pytest.mark.asyncio
    async def test_sync_fetch_guba_posts(self):
        """Test sync fetch guba posts"""
        from finance_toolkit.data_fetching import sync_fetch_guba_posts
        from finance_toolkit.data_fetching.guba_scraper import GubaPost
        
        with patch('finance_toolkit.data_fetching.guba_scraper.EastmoneyGubaAPI') as mock_api_class:
            mock_api = AsyncMock()
            mock_api_class.return_value.__aenter__.return_value = mock_api
            
            mock_posts = [
                GubaPost(
                    post_id='123456',
                    title='测试帖子',
                    author='测试用户',
                    author_id='user123',
                    author_followers=100,
                    author_influence=50,
                    read_count=1000,
                    comment_count=100,
                    like_count=50,
                    publish_time='2024-01-15T10:00:00',
                    update_time='2024-01-15T10:00:00',
                    content='测试内容',
                    stock_codes=['600519'],
                    board='主板',
                    url='https://guba.eastmoney.com/news,123456.html',
                )
            ]
            # get_post_list is an async generator
            async def mock_get_post_list(*args, **kwargs):
                for post in mock_posts:
                    yield post
            mock_api.get_post_list = mock_get_post_list
            
            results = sync_fetch_guba_posts('600519', page=1, page_size=10, sort='time')
            
            assert len(results) == 1
            assert results[0].post_id == '123456'
            assert results[0].title == '测试帖子'
    
    @pytest.mark.asyncio
    async def test_sync_fetch_guba_hot_posts(self):
        """Test sync fetch guba hot posts"""
        from finance_toolkit.data_fetching import sync_fetch_guba_hot_posts
        from finance_toolkit.data_fetching.guba_scraper import GubaPost
        
        with patch('finance_toolkit.data_fetching.guba_scraper.EastmoneyGubaAPI') as mock_api_class:
            mock_api = AsyncMock()
            mock_api_class.return_value.__aenter__.return_value = mock_api
            
            mock_posts = [
                GubaPost(
                    post_id='hot123',
                    title='热门帖子',
                    author='大V',
                    author_id='dav123',
                    author_followers=10000,
                    author_influence=90,
                    read_count=100000,
                    comment_count=5000,
                    like_count=2000,
                    publish_time='2024-01-15T10:00:00',
                    update_time='2024-01-15T10:00:00',
                    content='热门内容',
                    stock_codes=['600519'],
                    board='概念',
                    url='https://guba.eastmoney.com/news,hot123.html',
                )
            ]
            # get_hot_posts returns a List[GubaPost], not an async generator
            async def mock_get_hot_posts(*args, **kwargs):
                return mock_posts
            mock_api.get_hot_posts = mock_get_hot_posts
            
            results = sync_fetch_guba_hot_posts('concept', top_n=5)
            
            assert len(results) == 1
            assert results[0].post_id == 'hot123'


class TestAnalyzeStock:
    """Test analyze_stock function"""
    
    @pytest.mark.asyncio
    async def test_analyze_stock(self):
        """Test one-click stock analysis"""
        from finance_toolkit import analyze_stock
        
        # Create mock kline data with enough points (>= 60)
        mock_kline = []
        base_price = 10.0
        for i in range(100):
            mock_kline.append({
                'date': f'2024-01-{i+1:02d}' if i < 30 else f'2024-02-{i-29:02d}' if i < 60 else f'2024-03-{i-59:02d}',
                'open': base_price + i * 0.01,
                'high': base_price + i * 0.01 + 0.1,
                'low': base_price + i * 0.01 - 0.1,
                'close': base_price + i * 0.01 + 0.05,
                'volume': 1000000 + i * 10000,
            })
        
        with patch('finance_toolkit.data_fetching.fetch_kline') as mock_fetch_kline, \
             patch('finance_toolkit.technical_analysis.analyze_kline_data') as mock_analyze:
            
            mock_fetch_kline.return_value = mock_kline
            mock_analyze.return_value = {
                'price_stats': {'current_price': 10.5},
                'signals': {'ma': 'bullish', 'macd': 'bullish'},
                'latest_indicators': {'ma5': 10.4, 'ma20': 10.2},
            }
            
            result = analyze_stock('600000.SH')
            
            assert 'price_stats' in result
            assert 'signals' in result
            assert result['price_stats']['current_price'] == 10.5


class TestBatchFetch:
    """Test batch fetch functions"""
    
    @pytest.mark.asyncio
    async def test_batch_fetch_stocks(self):
        """Test batch fetching stock quotes"""
        from finance_toolkit.batch_processing import batch_fetch_stocks
        
        with patch('finance_toolkit.batch_processing.batch_fetcher.fetch_single_stock') as mock_fetch:
            mock_fetch.return_value = {
                'symbol': '600000',
                'success': True,
                'data': {'name': '浦发银行', 'price': 10.5, 'change_pct': 2.5}
            }
            
            summary = batch_fetch_stocks(['600000', '000001', '600519'])
            
            assert summary['success'] == 3
            assert summary['failed'] == 0
    
    @pytest.mark.asyncio
    async def test_batch_fetch_klines(self):
        """Test batch fetching K-lines"""
        from finance_toolkit.batch_processing import batch_fetch_klines
        
        # Mock fetch_single_kline to return different results based on input symbol
        # fetch_single_kline signature: (symbol, port, tab_id, scale='240', datalen=1023)
        def mock_fetch_side_effect(symbol, port, tab_id, scale='240', datalen=1023):
            return {
                'symbol': symbol,
                'success': True,
                'kline': [{'date': '2024-01-01', 'open': 10.0, 'close': 10.1, 'high': 10.2, 'low': 9.9, 'volume': 1000000}]
            }
        
        with patch('finance_toolkit.batch_processing.batch_fetcher.fetch_single_kline', side_effect=mock_fetch_side_effect):
            results = batch_fetch_klines(['600000', '000001'], port=9333, tab_id='test')
            
            # batch_fetch_klines returns a summary dict with 'results' list
            assert 'results' in results
            assert results['total'] == 2
            assert results['success'] == 2
            # Check that both symbols are in the results list
            result_symbols = [r['symbol'] for r in results['results']]
            assert '600000' in result_symbols
            assert '000001' in result_symbols
