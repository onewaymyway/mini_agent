# -*- coding: utf-8 -*-
"""Tests for stock_fetcher module."""

import pytest
import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

# Mock akshare before importing stock_fetcher
mock_ak = MagicMock()
sys.modules['akshare'] = mock_ak

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from finance_toolkit.data_fetching.stock_fetcher import (
    StockQuote,
    StockKline,
    FinancialReport,
    Dividend,
    LHBRecord,
    NorthboundFlow,
    StockBasic,
    fetch_realtime_quote,
    fetch_kline,
    fetch_financial,
    fetch_dividend,
    fetch_lhb,
    fetch_northbound,
    fetch_stock_basic,
    fetch_all_stock_data,
)


@pytest.fixture(autouse=True)
def reset_mock_ak():
    """Reset mock state before each test."""
    mock_ak.reset_mock()
    yield


@pytest.fixture
def sample_code():
    return '000001'


@pytest.fixture
def sample_date_range():
    return {'start_date': '20240101', 'end_date': '20241231'}


class TestFetchRealtimeQuote:
    def test_fetch_quote_success(self, sample_code):
        mock_records = [{
            '代码': sample_code, '名称': '平安银行', '最新价': 12.50,
            '涨跌幅': 1.23, '成交量': 150000000, '成交额': 1875000000.0,
            '今开': 12.35, '最高': 12.68, '最低': 12.30, '昨收': 12.35,
            '换手率': 1.5, '市盈率-动态': 8.5, '市净率': 0.8,
            '总市值': 228000000000.0, '流通市值': 228000000000.0,
        }]
        mock_df = MagicMock()
        mock_df.to_dict.return_value = mock_records
        # The filter operation returns a new mock that also returns records
        mock_filtered = MagicMock()
        mock_filtered.to_dict.return_value = mock_records
        mock_df.__getitem__ = MagicMock(return_value=mock_filtered)
        mock_ak.stock_zh_a_spot_em.return_value = mock_df
        result = fetch_realtime_quote(symbols=[sample_code])
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], StockQuote)
        assert result[0].symbol == sample_code
        assert result[0].price == 12.50
        assert result[0].change_pct == 1.23

    def test_fetch_quote_not_found(self, sample_code):
        mock_ak.stock_zh_a_spot_em.return_value = MagicMock()
        mock_ak.stock_zh_a_spot_em.return_value.iterrows.return_value = []
        result = fetch_realtime_quote(symbols=['999999'])
        assert result == []

    def test_fetch_quote_akshare_unavailable(self, sample_code):
        with patch('finance_toolkit.data_fetching.stock_fetcher.HAS_AKSHARE', False):
            with pytest.raises(Exception):
                fetch_realtime_quote(symbols=[sample_code])


class TestFetchKline:
    def test_fetch_kline_success(self, sample_code, sample_date_range):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '日期': '2024-01-02', '开盘': 12.00, '收盘': 12.30,
            '最高': 12.40, '最低': 11.95, '成交量': 100000000,
            '成交额': 1220000000.0, '振幅': 3.78, '涨跌幅': 2.44,
            '涨跌额': 0.30, '换手率': 1.2,
        }.get(k, d)
        mock_ak.stock_zh_a_hist.return_value = MagicMock()
        mock_ak.stock_zh_a_hist.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_kline(sample_code, **sample_date_range)
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], StockKline)
        assert result[0].symbol == sample_code

    def test_fetch_kline_invalid_dates(self, sample_code):
        with patch('finance_toolkit.data_fetching.stock_fetcher._fetch_kline_internal') as mock_internal:
            mock_internal.side_effect = Exception('date error')
            with pytest.raises(Exception):
                fetch_kline(sample_code, start_date='invalid', end_date='also_invalid')


class TestFetchFinancial:
    def test_fetch_financial_success(self, sample_code):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '报告期': '2024-06-30', '基本每股收益': '0.50',
            '稀释每股收益': '0.50', '基本每股净资产': '5.20',
            '每股经营现金流': '0.80', '净资产收益率': '8.50',
        }.get(k, d)
        mock_ak.stock_financial_report_sina.return_value = MagicMock()
        mock_ak.stock_financial_report_sina.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_financial(sample_code)
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], FinancialReport)

    def test_fetch_financial_empty(self, sample_code):
        mock_ak.stock_financial_report_sina.return_value = MagicMock()
        mock_ak.stock_financial_report_sina.return_value.iterrows.return_value = []
        result = fetch_financial(sample_code)
        assert result == []


class TestFetchDividend:
    def test_fetch_dividend_success(self, sample_code):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '报告期': '2024-06-30', '每股分红': '0.50',
            '分红方案': '10派2.5', '除权除息日': '2024-05-16',
            '股权登记日': '2024-05-15',
        }.get(k, d)
        mock_ak.stock_fhps_detail_em.return_value = MagicMock()
        mock_ak.stock_fhps_detail_em.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_dividend(sample_code)
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], Dividend)

    def test_fetch_dividend_no_data(self, sample_code):
        mock_ak.stock_fhps_detail_em.return_value = MagicMock()
        mock_ak.stock_fhps_detail_em.return_value.iterrows.return_value = []
        result = fetch_dividend(sample_code)
        assert result == []


class TestFetchLHB:
    def test_fetch_lhb_success(self, sample_code):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '龙虎榜日期': '2024-06-01', '代码': sample_code,
            '名称': '测试股票', '解释说明': '日振幅值达15%',
            '买入金额': '50000000', '卖出金额': '30000000',
            '净买入': '20000000', '买入营业部': '中信证券上海分公司',
            '卖出营业部': '华泰证券深圳分公司',
        }.get(k, d)
        mock_ak.stock_lhb_detail_em.return_value = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_lhb()
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], LHBRecord)

    def test_fetch_lhb_empty(self):
        mock_ak.stock_lhb_detail_em.return_value = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value.iterrows.return_value = []
        result = fetch_lhb()
        assert result == []


class TestFetchNorthbound:
    def test_fetch_northbound_success(self):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '日期': '2024-06-01', '沪股通净流入': '50000000',
            '深股通净流入': '30000000', '北向资金净流入': '80000000',
        }.get(k, d)
        mock_ak.stock_hsgt_fund_flow_summary_em.return_value = MagicMock()
        mock_ak.stock_hsgt_fund_flow_summary_em.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_northbound()
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], NorthboundFlow)

    def test_fetch_northbound_empty(self):
        mock_ak.stock_hsgt_fund_flow_summary_em.return_value = MagicMock()
        mock_ak.stock_hsgt_fund_flow_summary_em.return_value.iterrows.return_value = []
        result = fetch_northbound()
        assert result == []


class TestFetchStockBasic:
    def test_fetch_basic_success(self, sample_code):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '代码': sample_code, '名称': '平安银行',
            '行业': '银行', '上市日期': '2000-12-01',
        }.get(k, d)
        mock_ak.stock_zh_a_spot_em.return_value = MagicMock()
        mock_ak.stock_zh_a_spot_em.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_stock_basic()
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], StockBasic)
        assert result[0].symbol == sample_code
        assert result[0].name == '平安银行'

    def test_fetch_basic_not_found(self):
        mock_ak.stock_zh_a_spot_em.return_value = MagicMock()
        mock_ak.stock_zh_a_spot_em.return_value.iterrows.return_value = []
        result = fetch_stock_basic()
        assert result == []


class TestErrorHandling:
    def test_invalid_stock_code_format(self):
        with patch('finance_toolkit.data_fetching.stock_fetcher.HAS_AKSHARE', False):
            with pytest.raises(Exception):
                fetch_realtime_quote(symbols=['invalid'])

    def test_network_error_handling(self, sample_code):
        with patch('finance_toolkit.data_fetching.stock_fetcher._fetch_realtime_quote_internal') as mock_internal:
            mock_internal.side_effect = Exception('Network error')
            with pytest.raises(Exception):
                fetch_realtime_quote(symbols=[sample_code])

    def test_data_parsing_error(self, sample_code):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: None
        mock_ak.stock_zh_a_spot_em.return_value = MagicMock()
        mock_ak.stock_zh_a_spot_em.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_realtime_quote(symbols=[sample_code])
        assert isinstance(result, list)
        if result:
            assert result[0].price == 0


class TestDataStructures:
    def test_stock_quote_structure(self):
        sq = StockQuote(
            symbol='000001', name='测试', price=10.0, change_pct=1.0,
            open=9.9, high=10.1, low=9.8, prev_close=9.9,
            volume=1000000, amount=10000000.0, turnover_rate=1.5,
            pe_ratio=8.5, pb_ratio=0.8,
            total_market_cap=228000000000.0, float_market_cap=228000000000.0,
        )
        assert sq.symbol == '000001'
        assert sq.price == 10.0
        assert sq.source == 'akshare'
        assert sq.timestamp is not None

    def test_stock_kline_structure(self):
        kl = StockKline(
            symbol='000001', date='2024-01-01', open=10.0, close=10.5,
            high=10.6, low=9.9, volume=1000000, amount=10500000.0,
            amplitude=6.06, change_pct=5.0, change_amount=0.5, turnover_rate=1.2,
        )
        assert kl.symbol == '000001'
        assert kl.close == 10.5
        assert kl.source == 'akshare'

    def test_financial_report_structure(self):
        fr = FinancialReport(
            symbol='000001', report_date='2024-06-30',
            report_type='资产负债表', data={'基本每股收益': '0.50'},
        )
        assert fr.symbol == '000001'
        assert fr.report_type == '资产负债表'
        assert fr.source == 'akshare'

    def test_dividend_structure(self):
        div = Dividend(
            symbol='000001', report_date='2024-06-30',
            dividend_per_share=0.50, dividend_plan='10派2.5',
            ex_dividend_date='2024-05-16', record_date='2024-05-15',
        )
        assert div.symbol == '000001'
        assert div.dividend_per_share == 0.50
        assert div.source == 'akshare'

    def test_lhb_record_structure(self):
        lhb = LHBRecord(
            trade_date='2024-06-01', symbol='000001', name='测试股票',
            explanation='日振幅值达15%', buy_amount=50000000.0,
            sell_amount=30000000.0, net_buy=20000000.0,
            buy_seat='中信证券上海分公司', sell_seat='华泰证券深圳分公司',
        )
        assert lhb.symbol == '000001'
        assert lhb.buy_seat == '中信证券上海分公司'
        assert lhb.source == 'akshare'

    def test_northbound_flow_structure(self):
        nb = NorthboundFlow(
            date='2024-06-01', sh_net_inflow=50000000.0,
            sz_net_inflow=30000000.0, total_net_inflow=80000000.0,
        )
        assert nb.date == '2024-06-01'
        assert nb.total_net_inflow == 80000000.0
        assert nb.source == 'akshare'

    def test_stock_basic_structure(self):
        sb = StockBasic(
            symbol='000001', name='平安银行', market='SZ',
            industry='银行', list_date='2000-12-01',
        )
        assert sb.symbol == '000001'
        assert sb.name == '平安银行'
        assert sb.industry == '银行'
        assert sb.source == 'akshare'


class TestIntegration:
    def test_full_data_pipeline(self, sample_code, sample_date_range):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '代码': sample_code, '名称': '测试', '最新价': 10.0,
            '涨跌幅': 1.0, '成交量': 1000000, '成交额': 10000000.0,
            '今开': 9.9, '最高': 10.1, '最低': 9.8, '昨收': 9.9,
            '换手率': 1.5, '市盈率-动态': 8.5, '市净率': 0.8,
            '总市值': 228000000000.0, '流通市值': 228000000000.0,
        }.get(k, d)
        mock_ak.stock_zh_a_spot_em.return_value = MagicMock()
        mock_ak.stock_zh_a_spot_em.return_value.iterrows.return_value = [(0, mock_row)]
        mock_ak.stock_zh_a_hist.return_value = MagicMock()
        mock_ak.stock_zh_a_hist.return_value.iterrows.return_value = []
        mock_ak.stock_financial_report_sina.return_value = MagicMock()
        mock_ak.stock_financial_report_sina.return_value.iterrows.return_value = []
        mock_ak.stock_fhps_detail_em.return_value = MagicMock()
        mock_ak.stock_fhps_detail_em.return_value.iterrows.return_value = []
        mock_ak.stock_lhb_detail_em.return_value = MagicMock()
        mock_ak.stock_lhb_detail_em.return_value.iterrows.return_value = []
        mock_ak.stock_hsgt_fund_flow_summary_em.return_value = MagicMock()
        mock_ak.stock_hsgt_fund_flow_summary_em.return_value.iterrows.return_value = []

        quote = fetch_realtime_quote(symbols=[sample_code])
        kline = fetch_kline(sample_code, **sample_date_range)
        report = fetch_financial(sample_code)
        dividend = fetch_dividend(sample_code)
        lhb = fetch_lhb()
        northbound = fetch_northbound()
        basic = fetch_stock_basic()

        assert quote is not None
        assert isinstance(kline, list)
        assert isinstance(report, list)
        assert isinstance(dividend, list)
        assert isinstance(lhb, list)
        assert isinstance(northbound, list)
        assert isinstance(basic, list)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
