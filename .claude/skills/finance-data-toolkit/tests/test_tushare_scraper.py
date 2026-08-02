"""
Tests for Tushare scraper

使用 Mock 模拟 Tushare Pro API，无需真实 Token。
"""

import pytest
from unittest.mock import MagicMock, patch
import pandas as pd


class TestTushareScraper:
    """Test TushareScraper class"""
    
    @pytest.fixture
    def scraper(self):
        """Create scraper instance with mocked pro_api"""
        from finance_toolkit.scrapers.tushare_scraper import TushareScraper
        with patch('finance_toolkit.scrapers.tushare_scraper.ts.pro_api') as mock_pro_api:
            mock_pro = MagicMock()
            mock_pro_api.return_value = mock_pro
            scraper = TushareScraper(token='test_token')
            scraper.pro = mock_pro
            yield scraper
    
    @pytest.mark.asyncio
    async def test_fetch_realtime_quote(self, scraper):
        """Test fetching realtime quote"""
        # Mock realtime_quote 接口（优先使用）
        mock_df = pd.DataFrame({
            'ts_code': ['600000.SH'],
            'open': [10.30],
            'high': [10.60],
            'low': [10.25],
            'price': [10.50],
            'pre_close': [10.25],
            'vol': [1000000],
            'amount': [10500000],
            'pct_chg': [2.5],
            'change': [0.25],
            'turnover_rate': [1.5],
            'pe_ttm': [15.0],
            'pb': [2.0],
            'total_mv': [100000000],
            'circ_mv': [80000000],
        })
        scraper.pro.realtime_quote.return_value = mock_df
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'quote'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].source == 'tushare'
        assert results[0].data_type == 'quote'
        assert results[0].symbol == '600000.SH'
        assert results[0].payload['close'] == 10.50
    
    @pytest.mark.asyncio
    async def test_fetch_kline_daily(self, scraper):
        """Test fetching daily K-line"""
        mock_df = pd.DataFrame({
            'ts_code': ['600000.SH'] * 3,
            'trade_date': ['20240103', '20240102', '20240101'],
            'open': [10.1, 10.2, 10.0],
            'high': [10.5, 10.4, 10.3],
            'low': [10.0, 10.0, 9.9],
            'close': [10.3, 10.1, 10.2],
            'vol': [1100000, 1200000, 1000000],
            'amount': [11330000, 12120000, 10200000],
            'pct_chg': [1.5, -1.0, 2.0],
        })
        scraper.pro.daily.return_value = mock_df
        
        adj_df = pd.DataFrame({
            'ts_code': ['600000.SH'] * 3,
            'trade_date': ['20240103', '20240102', '20240101'],
            'adj_factor': [1.0, 1.0, 1.0],
        })
        scraper.pro.adj_factor.return_value = adj_df
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'kline', period='daily', start='20240101', end='20240103'):
            results.append(data)
        
        # 每个 symbol 返回一条 FinanceData，payload.data 包含所有 K 线
        assert len(results) == 1
        assert results[0].data_type == 'kline'
        assert results[0].payload['count'] == 3
        assert len(results[0].payload['data']) == 3
    
    @pytest.mark.asyncio
    async def test_fetch_financial(self, scraper):
        """Test fetching financial reports"""
        # Mock fina_indicator (先调用)
        indicator_df = pd.DataFrame({'ts_code': ['600000.SH'], 'end_date': ['20231231'], 'revenue': [1000000]})
        scraper.pro.fina_indicator.return_value = indicator_df
        
        income_df = pd.DataFrame({'ts_code': ['600000.SH'], 'end_date': ['20231231'], 'revenue': [1000000], 'n_income': [100000]})
        balance_df = pd.DataFrame({'ts_code': ['600000.SH'], 'end_date': ['20231231'], 'total_assets': [5000000], 'total_liab': [3000000]})
        cashflow_df = pd.DataFrame({'ts_code': ['600000.SH'], 'end_date': ['20231231'], 'n_cashflow_act': [200000]})
        
        scraper.pro.income.return_value = income_df
        scraper.pro.balancesheet.return_value = balance_df
        scraper.pro.cashflow.return_value = cashflow_df
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'financial'):
            results.append(data)
        
        # 返回 4 条：indicator + income + balancesheet + cashflow
        assert len(results) == 4
        meta_types = {r.meta['report_type'] for r in results}
        assert meta_types == {'indicator', 'income', 'balancesheet', 'cashflow'}
        # payload 是列表格式
        assert isinstance(results[0].payload, list)
    
    @pytest.mark.asyncio
    async def test_fetch_dividend(self, scraper):
        """Test fetching dividend data"""
        div_df = pd.DataFrame({
            'ts_code': ['600000.SH'],
            'div_proc': ['实施'],
            'stk_div': [0],
            'cash_div': [0.5],
            'record_date': ['20230601'],
            'ex_date': ['20230605'],
            'pay_date': ['20230610'],
        })
        scraper.pro.dividend.return_value = div_df
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'dividend'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].data_type == 'dividend'
        # payload 是列表格式
        assert isinstance(results[0].payload, list)
        assert len(results[0].payload) == 1
        assert results[0].payload[0]['cash_div'] == 0.5
    
    @pytest.mark.asyncio
    async def test_fetch_lhb(self, scraper):
        """Test fetching dragon-tiger list"""
        lhb_df = pd.DataFrame({
            'trade_date': ['20240101'],
            'ts_code': ['600000.SH'],
            'reason': ['日涨幅偏离值达到7%'],
            'buy_amount': [10000000],
            'sell_amount': [8000000],
            'net_amount': [2000000],
            'dept_name': ['机构专用'],
        })
        scraper.pro.top_inst.return_value = lhb_df
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'lhb', start='20240101', end='20240131'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].data_type == 'lhb'
    
    @pytest.mark.asyncio
    async def test_fetch_northbound(self, scraper):
        """Test fetching northbound capital data"""
        nb_df = pd.DataFrame({
            'trade_date': ['20240101'],
            'sh_buy': [100000000],
            'sh_sell': [80000000],
            'sz_buy': [50000000],
            'sz_sell': [40000000],
        })
        scraper.pro.moneyflow_hsgt.return_value = nb_df
        
        results = []
        async for data in scraper.fetch([], 'northbound', start='20240101', end='20240131'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].data_type == 'northbound'
    
    @pytest.mark.asyncio
    async def test_health_check_success(self, scraper):
        """Test health check when API is available"""
        scraper.pro.stock_basic.return_value = pd.DataFrame({'ts_code': ['600000.SH']})
        
        result = await scraper.health_check()
        assert result is True
    
    @pytest.mark.asyncio
    async def test_health_check_failure(self, scraper):
        """Test health check when API fails"""
        scraper.pro.stock_basic.side_effect = Exception("Token invalid")
        
        result = await scraper.health_check()
        assert result is False
    
    @pytest.mark.asyncio
    async def test_supported_types(self, scraper):
        """Test supported data types"""
        types = scraper.supported_types
        assert 'quote' in types
        assert 'kline' in types
        assert 'financial' in types
        assert 'dividend' in types
        assert 'lhb' in types
        assert 'northbound' in types
    
    @pytest.mark.asyncio
    async def test_source_name(self, scraper):
        """Test source name property"""
        assert scraper.source_name == 'tushare'
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        """Test close method"""
        await scraper.close()  # Should not raise


class TestTushareScraperEdgeCases:
    """Test edge cases and error handling"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.scrapers.tushare_scraper import TushareScraper
        with patch('finance_toolkit.scrapers.tushare_scraper.ts.pro_api') as mock_pro_api:
            mock_pro = MagicMock()
            mock_pro_api.return_value = mock_pro
            scraper = TushareScraper(token='test_token')
            scraper.pro = mock_pro
            yield scraper
    
    @pytest.mark.asyncio
    async def test_empty_symbol_list(self, scraper):
        """Test fetching with empty symbol list"""
        results = []
        async for data in scraper.fetch([], 'quote'):
            results.append(data)
        assert len(results) == 0
    
    @pytest.mark.asyncio
    async def test_api_error_handling(self, scraper):
        """Test handling of API errors"""
        scraper.pro.daily.side_effect = Exception("API rate limit")
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'quote'):
            results.append(data)
        # Should not raise, just return empty
        assert len(results) == 0
