"""
Tests for Yahoo Finance scraper

使用 Mock 模拟 yfinance 库，无需真实 API 调用。
"""

import pytest
from unittest.mock import MagicMock, patch
import pandas as pd


class TestYahooScraper:
    """Test YahooScraper class"""
    
    @pytest.fixture
    def scraper(self):
        """Create scraper instance with mocked yfinance"""
        from finance_toolkit.scrapers.yahoo_scraper import YahooScraper
        with patch('finance_toolkit.scrapers.yahoo_scraper.yf') as mock_yf:
            scraper = YahooScraper()
            scraper._yf = mock_yf
            yield scraper
    
    @pytest.mark.asyncio
    async def test_fetch_realtime_quote(self, scraper):
        """Test fetching realtime quote"""
        mock_ticker = MagicMock()
        # 使用 MagicMock 模拟 fast_info，支持 .get() 调用
        mock_fast_info = MagicMock()
        mock_fast_info.get = MagicMock(side_effect=lambda key, default=0: {
            'lastPrice': 150.0,
            'open': 149.0,
            'dayHigh': 151.0,
            'dayLow': 148.5,
            'previousClose': 149.5,
            'lastVolume': 1000000,
            'marketCap': 2500000000000,
            'currency': 'USD',
            'timezone': 'America/New_York',
        }.get(key, default))
        mock_ticker.fast_info = mock_fast_info
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'quote'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].source == 'yahoo'
        assert results[0].data_type == 'quote'
        assert results[0].symbol == 'AAPL'
        assert results[0].payload['close'] == 150.0
        assert results[0].payload['open'] == 149.0
        assert results[0].payload['market_cap'] == 2500000000000
    
    @pytest.mark.asyncio
    async def test_fetch_kline_daily(self, scraper):
        """Test fetching daily K-line"""
        mock_ticker = MagicMock()
        # 注意：实现中会 reset_index()，索引名称必须是 'Date'
        mock_hist = pd.DataFrame({
            'Open': [149.0, 148.0, 147.0],
            'High': [151.0, 150.0, 149.0],
            'Low': [148.5, 147.5, 146.5],
            'Close': [150.0, 149.0, 148.0],
            'Volume': [1000000, 1100000, 900000],
        }, index=pd.to_datetime(['2024-01-01', '2024-01-02', '2024-01-03']))
        mock_hist.index.name = 'Date'  # 关键：设置索引名称
        mock_ticker.history.return_value = mock_hist
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'kline', period='daily', start='2024-01-01', end='2024-01-03'):
            results.append(data)
        
        # 实现返回单条 FinanceData，payload['data'] 包含所有 K 线
        assert len(results) == 1
        assert results[0].data_type == 'kline'
        assert 'data' in results[0].payload
        assert len(results[0].payload['data']) == 3
        assert results[0].payload['count'] == 3
    
    @pytest.mark.asyncio
    async def test_fetch_kline_minute(self, scraper):
        """Test fetching minute K-line (1m, 5m, 15m, 30m, 60m)"""
        mock_ticker = MagicMock()
        mock_hist = pd.DataFrame({
            'Open': [150.0, 150.1],
            'High': [150.2, 150.3],
            'Low': [149.9, 150.0],
            'Close': [150.1, 150.2],
            'Volume': [10000, 11000],
        }, index=pd.to_datetime(['2024-01-01 09:30', '2024-01-01 09:31']))
        mock_hist.index.name = 'Date'  # 关键：设置索引名称
        mock_ticker.history.return_value = mock_hist
        scraper._yf.Ticker.return_value = mock_ticker
        
        for period in ['1m', '5m', '15m', '30m', '60m']:
            results = []
            async for data in scraper.fetch(['AAPL'], 'kline', period=period):
                results.append(data)
            # 实现返回单条 FinanceData，payload['data'] 包含所有 K 线
            assert len(results) == 1
            assert len(results[0].payload['data']) == 2
    
    @pytest.mark.asyncio
    async def test_fetch_financial(self, scraper):
        """Test fetching financial statements"""
        mock_ticker = MagicMock()
        # 创建空的 DataFrame 模拟报表
        mock_income = pd.DataFrame({'2023-12-31': {'Total Revenue': 1000000, 'Net Income': 100000}})
        mock_balance = pd.DataFrame({'2023-12-31': {'Total Assets': 5000000, 'Total Liab': 3000000}})
        mock_cashflow = pd.DataFrame({'2023-12-31': {'Operating Cash Flow': 200000}})
        mock_quarterly = pd.DataFrame()
        
        mock_ticker.income_stmt = mock_income
        mock_ticker.balance_sheet = mock_balance
        mock_ticker.cashflow = mock_cashflow
        mock_ticker.quarterly_income_stmt = mock_quarterly
        mock_ticker.quarterly_balance_sheet = mock_quarterly
        mock_ticker.quarterly_cashflow = mock_quarterly
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'financial'):
            results.append(data)
        
        # 实现返回单条 FinanceData，包含所有报表
        assert len(results) == 1
        assert results[0].data_type == 'financial'
        assert 'annual' in results[0].payload
        assert 'quarterly' in results[0].payload
        assert 'income_statement' in results[0].payload['annual']
    
    @pytest.mark.asyncio
    async def test_fetch_dividend(self, scraper):
        """Test fetching dividend data"""
        mock_ticker = MagicMock()
        mock_dividends = pd.Series([0.5, 0.5], index=pd.to_datetime(['2023-06-01', '2023-09-01']))
        mock_splits = pd.Series([], dtype=float)
        mock_ticker.dividends = mock_dividends
        mock_ticker.splits = mock_splits
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'dividend'):
            results.append(data)
        
        # 实现返回单条 FinanceData，payload['dividends'] 是列表
        assert len(results) == 1
        assert results[0].data_type == 'dividend'
        assert 'dividends' in results[0].payload
        assert len(results[0].payload['dividends']) == 2
        assert results[0].payload['dividends'][0]['dividend'] == 0.5
    
    @pytest.mark.asyncio
    async def test_fetch_options(self, scraper):
        """Test fetching option chain"""
        mock_ticker = MagicMock()
        mock_ticker.options = ['2024-01-19', '2024-02-16']
        mock_chain = pd.DataFrame({
            'contractSymbol': ['AAPL240119C00150000'],
            'strike': [150.0],
            'lastPrice': [5.0],
            'bid': [4.8],
            'ask': [5.2],
            'volume': [100],
            'openInterest': [500],
            'impliedVolatility': [0.25],
        })
        mock_ticker.option_chain.return_value = MagicMock(calls=mock_chain, puts=mock_chain)
        # 使用 MagicMock 模拟 fast_info
        mock_fast_info = MagicMock()
        mock_fast_info.get = MagicMock(side_effect=lambda key, default=None: {'lastPrice': 150.0}.get(key, default))
        mock_ticker.fast_info = mock_fast_info
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'options'):
            results.append(data)
        
        # 实现返回单条 FinanceData，包含 calls 和 puts
        assert len(results) == 1
        assert results[0].data_type == 'options'
        assert 'calls' in results[0].payload
        assert 'puts' in results[0].payload
    
    @pytest.mark.asyncio
    async def test_fetch_info(self, scraper):
        """Test fetching company info"""
        mock_ticker = MagicMock()
        mock_ticker.info = {
            'symbol': 'AAPL',
            'longName': 'Apple Inc.',
            'sector': 'Technology',
            'industry': 'Consumer Electronics',
            'country': 'United States',
            'website': 'https://www.apple.com',
            'fullTimeEmployees': 160000,
        }
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'info'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].data_type == 'info'
        assert results[0].payload['longName'] == 'Apple Inc.'
    
    @pytest.mark.asyncio
    async def test_fetch_holders(self, scraper):
        """Test fetching institutional holders"""
        mock_ticker = MagicMock()
        mock_inst = pd.DataFrame({
            'Holder': ['Vanguard Group'],
            'Shares': [1000000],
            'Date Reported': ['2023-12-31'],
            '% Out': [0.05],
            'Value': [150000000],
        })
        mock_mutual = pd.DataFrame()
        mock_major = pd.DataFrame()
        mock_insider = pd.DataFrame()
        
        mock_ticker.institutional_holders = mock_inst
        mock_ticker.mutualfund_holders = mock_mutual
        mock_ticker.major_holders = mock_major
        mock_ticker.insider_transactions = mock_insider
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'holders'):
            results.append(data)
        
        # 实现返回单条 FinanceData，包含各类持有人信息
        assert len(results) == 1
        assert results[0].data_type == 'holders'
        assert 'institutional_holders' in results[0].payload
        assert len(results[0].payload['institutional_holders']) == 1
    
    @pytest.mark.asyncio
    async def test_fetch_recommendations(self, scraper):
        """Test fetching analyst recommendations"""
        mock_ticker = MagicMock()
        mock_ticker.recommendations = pd.DataFrame({
            'Firm': ['Goldman Sachs'],
            'To Grade': ['Buy'],
            'From Grade': ['Neutral'],
            'Action': ['up'],
        }, index=pd.to_datetime(['2024-01-01']))
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'recommendations'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].data_type == 'recommendations'
    
    @pytest.mark.asyncio
    async def test_fetch_calendar(self, scraper):
        """Test fetching earnings calendar"""
        mock_ticker = MagicMock()
        mock_ticker.calendar = pd.DataFrame({
            'Earnings Date': [pd.Timestamp('2024-01-25')],
            'EPS Estimate': [2.0],
            'Revenue Estimate': [100000000],
        })
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'calendar'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].data_type == 'calendar'
    
    @pytest.mark.asyncio
    async def test_health_check(self, scraper):
        """Test health check"""
        mock_ticker = MagicMock()
        mock_ticker.info = {'symbol': 'AAPL'}
        scraper._yf.Ticker.return_value = mock_ticker
        
        result = await scraper.health_check()
        assert result is True
    
    @pytest.mark.asyncio
    async def test_supported_types(self, scraper):
        """Test supported data types"""
        types = scraper.supported_types
        expected = {'quote', 'kline', 'financial', 'dividend', 'options', 'info', 'holders', 'recommendations', 'calendar'}
        assert expected.issubset(set(types))
    
    @pytest.mark.asyncio
    async def test_source_name(self, scraper):
        """Test source name"""
        assert scraper.source_name == 'yahoo'
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        """Test close method"""
        await scraper.close()  # Should not raise


class TestYahooScraperEdgeCases:
    """Test edge cases"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.scrapers.yahoo_scraper import YahooScraper
        with patch('finance_toolkit.scrapers.yahoo_scraper.yf') as mock_yf:
            scraper = YahooScraper()
            scraper._yf = mock_yf
            yield scraper
    
    @pytest.mark.asyncio
    async def test_empty_symbol_list(self, scraper):
        """Test empty symbol list"""
        results = []
        async for data in scraper.fetch([], 'quote'):
            results.append(data)
        assert len(results) == 0
    
    @pytest.mark.asyncio
    async def test_ticker_error(self, scraper):
        """Test handling of ticker errors"""
        # 模拟异常时，实现会返回带 error 字段的 FinanceData 而不是空列表
        scraper._yf.Ticker.side_effect = Exception("Network error")
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'quote'):
            results.append(data)
        # 实现返回带 error 字段的记录
        assert len(results) == 1
        assert 'error' in results[0].payload
        assert 'Network error' in results[0].payload['error']
    
    @pytest.mark.asyncio
    async def test_empty_history(self, scraper):
        """Test empty history response"""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        scraper._yf.Ticker.return_value = mock_ticker
        
        results = []
        async for data in scraper.fetch(['AAPL'], 'kline'):
            results.append(data)
        # 实现返回带 error 字段的记录而不是空列表
        assert len(results) == 1
        assert 'error' in results[0].payload
        assert results[0].payload['error'] == 'Empty data'
