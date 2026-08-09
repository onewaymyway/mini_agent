# -*- coding: utf-8 -*-
"""
Fund data fetcher module tests
"""

import pytest
import sys
from pathlib import Path

# Add project path
sys.path.insert(0, str(Path(__file__).parent.parent))

from finance_toolkit.data_fetching.fund_fetcher import (
    ETFQuote,
    ETFKline,
    LOFQuote,
    FundNAV,
    FundHoldings,
    FundRank,
    fetch_etf_quote,
    fetch_etf_kline,
    fetch_lof_quote,
    fetch_fund_nav,
    fetch_fund_nav_history,
    fetch_fund_holdings,
    fetch_fund_rank,
    fetch_fund_list,
    FundDataFetcher,
    fetcher,
)


class TestETFQuote:
    """ETF real-time quote tests"""
    
    def test_fetch_etf_quote(self):
        """Test ETF quote fetching"""
        results = fetch_etf_quote(['510300'])
        assert isinstance(results, list)
        if results:
            assert isinstance(results[0], ETFQuote)
            assert results[0].code == '510300'
    
    def test_fetch_etf_quote_all(self):
        """Test fetching all ETF quotes"""
        results = fetch_etf_quote()
        assert isinstance(results, list)


class TestETFKline:
    """ETF historical K-line tests"""
    
    def test_fetch_etf_kline(self):
        """Test ETF K-line fetching"""
        results = fetch_etf_kline('510300', '20240101', '20241231')
        assert isinstance(results, list)
        if results:
            assert isinstance(results[0], ETFKline)
            assert results[0].code == '510300'


class TestLOFQuote:
    """LOF real-time quote tests"""
    
    def test_fetch_lof_quote(self):
        """Test LOF quote fetching"""
        results = fetch_lof_quote(['501011'])
        assert isinstance(results, list)


class TestFundNAV:
    """Open-end fund NAV tests"""
    
    def test_fetch_fund_nav(self):
        """Test fund NAV fetching"""
        results = fetch_fund_nav('110011', '20240101', '20241231')
        assert isinstance(results, list)
        if results:
            assert isinstance(results[0], FundNAV)
            assert results[0].code == '110011'
            assert results[0].unit_nav > 0
    
    def test_fetch_fund_nav_history(self):
        """Test fund historical NAV fetching"""
        results = fetch_fund_nav_history('110011')
        assert isinstance(results, list)
        if results:
            assert isinstance(results[0], FundNAV)


class TestFundHoldings:
    """Fund holdings tests"""
    
    def test_fetch_fund_holdings(self):
        """Test fund holdings fetching"""
        results = fetch_fund_holdings('110011', '2024')
        assert isinstance(results, list)


class TestFundRank:
    """Fund ranking tests"""
    
    def test_fetch_fund_rank(self):
        """Test fund ranking fetching"""
        results = fetch_fund_rank('全部')
        assert isinstance(results, list)
        if results:
            assert isinstance(results[0], FundRank)
            assert results[0].rank == 1
            assert results[0].fund_code
            assert results[0].fund_name
    
    def test_fetch_fund_rank_stock(self):
        """Test stock fund ranking"""
        results = fetch_fund_rank('股票型')
        assert isinstance(results, list)


class TestFundList:
    """Fund list tests"""
    
    def test_fetch_fund_list(self):
        """Test fund list fetching"""
        results = fetch_fund_list('全部')
        assert isinstance(results, list)
        if results:
            assert 'code' in results[0]
            assert 'name' in results[0]


class TestFundDataFetcher:
    """Convenience class tests"""
    
    def test_fetcher_instance(self):
        """Test fetcher instance"""
        assert isinstance(fetcher, FundDataFetcher)
    
    def test_get_fund_summary(self):
        """Test fund summary info"""
        summary = fetcher.get_fund_summary('110011')
        assert isinstance(summary, dict)
        assert 'nav' in summary
        assert 'holdings' in summary


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
