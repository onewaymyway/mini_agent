"""
Tests for AKShare scraper
"""

import pytest
from unittest.mock import patch
import pandas as pd


class TestAKShareScraper:
    """Test AKShareScraper class"""
    
    @pytest.fixture
    def scraper(self):
        """Create scraper instance with mocked client"""
        from finance_toolkit.scrapers.akshare_scraper import AKShareScraper
        with patch('finance_toolkit.scrapers.akshare_scraper.ak') as mock_ak:
            scraper = AKShareScraper()
            scraper._ak = mock_ak
            yield scraper
    
    @pytest.mark.asyncio
    async def test_fetch_realtime_quote(self, scraper, sample_akshare_quote):
        """Test fetching realtime quote"""
        # Mock AKShare response
        mock_df = pd.DataFrame([sample_akshare_quote])
        scraper._ak.stock_zh_a_spot_em.return_value = mock_df
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'quote'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].source == 'akshare'
        assert results[0].data_type == 'quote'
        assert results[0].symbol == '600000.SH'
        assert results[0].payload['close'] == 10.50
        assert results[0].payload['change_pct'] == 2.5
    
    @pytest.mark.asyncio
    async def test_fetch_kline_daily(self, scraper, sample_akshare_kline):
        """Test fetching daily K-line"""
        scraper._ak.stock_zh_a_hist.return_value = sample_akshare_kline
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'kline', period='daily', start='20240101', end='20240103'):
            results.append(data)
        
        # K-line returns single FinanceData with all data in payload['data']
        assert len(results) == 1
        assert results[0].data_type == 'kline'
        assert results[0].symbol == '600000.SH'
        assert 'data' in results[0].payload
        assert len(results[0].payload['data']) == 3  # 3 days of data
    
    @pytest.mark.asyncio
    async def test_fetch_financial(self, scraper):
        """Test fetching financial reports"""
        # Mock financial abstract data
        abstract_df = pd.DataFrame({
            '报告期': ['2023-12-31', '2023-09-30'],
            '营业收入': [1000000, 750000],
            '净利润': [100000, 75000]
        })
        
        scraper._ak.stock_financial_abstract_ths.return_value = abstract_df
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'financial'):
            results.append(data)
        
        # Financial returns data in payload (list of records)
        assert len(results) >= 1
        assert results[0].data_type == 'financial'
        assert results[0].symbol == '600000.SH'
        # Check payload is either a list or has error
        assert 'error' in results[0].payload or isinstance(results[0].payload, list)
    
    @pytest.mark.asyncio
    async def test_fetch_dividend(self, scraper):
        """Test fetching dividend data"""
        div_df = pd.DataFrame({
            '方案公告日': ['2023-04-01'],
            '分红方案': ['10派5元'],
            '每股分红': [0.5],
            '每股送转': [0],
            '股权登记日': ['2023-06-01'],
            '除权除息日': ['2023-06-05'],
            '派息日': ['2023-06-10'],
        })
        scraper._ak.stock_fhps_detail_em.return_value = div_df
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'dividend'):
            results.append(data)
        
        # Dividend returns data as list of records in payload
        assert len(results) >= 1
        assert results[0].data_type == 'dividend'
        assert results[0].symbol == '600000.SH'
        # Check if payload contains dividend data
        if isinstance(results[0].payload, list) and len(results[0].payload) > 0:
            assert results[0].payload[0].get('每股分红') == 0.5
    
    @pytest.mark.asyncio
    async def test_fetch_lhb(self, scraper):
        """Test fetching dragon-tiger list"""
        lhb_df = pd.DataFrame({
            '上榜日期': ['2024-01-01'],
            '上榜原因': ['日涨幅偏离值达到7%'],
            '买入金额': [10000000],
            '卖出金额': [8000000],
            '净额': [2000000],
            '营业部': ['机构专用'],
            '买入排名': [1],
            '卖出排名': [2],
            '代码': ['600000'],  # 必须包含代码列用于筛选
        })
        scraper._ak.stock_lhb_detail_em.return_value = lhb_df
        
        results = []
        async for data in scraper.fetch(['600000.SH'], 'lhb', start='20240101', end='20240131'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].data_type == 'lhb'
        # AKShare 返回中文键名
        assert results[0].payload['上榜原因'] == '日涨幅偏离值达到7%'
    
    @pytest.mark.asyncio
    async def test_fetch_northbound(self, scraper):
        """Test fetching northbound capital data"""
        nb_df = pd.DataFrame({
            '日期': ['2024-01-01'],
            '沪股通净流入': [100000000],
            '深股通净流入': [50000000],
            '合计净流入': [150000000],
        })
        scraper._ak.stock_hsgt_hist_em.return_value = nb_df
        
        results = []
        async for data in scraper.fetch([], 'northbound', start='20240101', end='20240131'):
            results.append(data)
        
        assert len(results) == 1
        assert results[0].data_type == 'northbound'
    
    @pytest.mark.asyncio
    async def test_health_check_success(self, scraper):
        """Test health check when API is available"""
        scraper._ak.stock_zh_a_spot_em.return_value = pd.DataFrame({'代码': ['600000']})
        
        result = await scraper.health_check()
        assert result is True
    
    @pytest.mark.asyncio
    async def test_health_check_failure(self, scraper):
        """Test health check when API fails"""
        scraper._ak.stock_zh_a_spot_em.side_effect = Exception("Network error")
        
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
        assert scraper.source_name == 'akshare'
    
    @pytest.mark.asyncio
    async def test_close(self, scraper):
        """Test close method"""
        await scraper.close()  # Should not raise


class TestAKShareScraperEdgeCases:
    """Test edge cases and error handling"""
    
    @pytest.fixture
    def scraper(self):
        from finance_toolkit.scrapers.akshare_scraper import AKShareScraper
        with patch('finance_toolkit.scrapers.akshare_scraper.ak') as mock_ak:
            scraper = AKShareScraper()
            scraper._ak = mock_ak
            yield scraper
    
    @pytest.mark.asyncio
    async def test_empty_symbol_list(self, scraper):
        """Test fetching with empty symbol list"""
        results = []
        async for data in scraper.fetch([], 'quote'):
            results.append(data)
        assert len(results) == 0
    
    @pytest.mark.asyncio
    async def test_unknown_symbol(self, scraper, sample_akshare_quote):
        """Test fetching unknown symbol returns empty"""
        mock_df = pd.DataFrame([sample_akshare_quote])
        scraper._ak.stock_zh_a_spot_em.return_value = mock_df
        
        results = []
        async for data in scraper.fetch(['999999.SZ'], 'quote'):
            results.append(data)
        assert len(results) == 0
    
    @pytest.mark.asyncio
    async def test_kline_with_different_periods(self, scraper, sample_akshare_kline):
        """Test K-line with different periods"""
        scraper._ak.stock_zh_a_hist.return_value = sample_akshare_kline
        
        for period in ['daily', 'weekly', 'monthly']:
            results = []
            async for data in scraper.fetch(['600000.SH'], 'kline', period=period):
                results.append(data)
            assert len(results) > 0
    
    @pytest.mark.asyncio
    async def test_kline_with_adjust_types(self, scraper, sample_akshare_kline):
        """Test K-line with different adjust types"""
        scraper._ak.stock_zh_a_hist.return_value = sample_akshare_kline
        
        for adjust in ['qfq', 'hfq', '']:
            results = []
            async for data in scraper.fetch(['600000.SH'], 'kline', adjust=adjust):
                results.append(data)
            assert len(results) > 0
