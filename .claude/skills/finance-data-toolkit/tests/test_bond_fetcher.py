# -*- coding: utf-8 -*-
"""Tests for bond_fetcher module."""

import pytest
import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

# Mock akshare before importing bond_fetcher
mock_ak = MagicMock()
sys.modules['akshare'] = mock_ak

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from finance_toolkit.data_fetching.bond_fetcher import (
    BondYield,
    ConvertibleBond,
    ConvertibleBondKline,
    CorporateBond,
    fetch_bond_yield,
    fetch_convertible_bond_spot,
    fetch_convertible_bond_history,
    fetch_corporate_bond_spot,
    BondDataFetcher,
)


@pytest.fixture(autouse=True)
def reset_mock_ak():
    mock_ak.reset_mock()
    yield


@pytest.fixture
def sample_bond_code():
    return '127045'


class TestBondYield:
    def test_fetch_bond_yield_success(self):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '日期': '2024-06-01', '1年': 2.10, '2年': 2.30,
            '3年': 2.45, '5年': 2.60, '10年': 2.75,
        }.get(k, d)
        mock_ak.bond_china_yield.return_value = MagicMock()
        mock_ak.bond_china_yield.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_bond_yield()
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], BondYield)
        assert result[0].date == '2024-06-01'
        assert result[0].yield_1y == 2.10
        assert result[0].yield_10y == 2.75
        assert result[0].source == 'akshare'

    def test_fetch_bond_yield_empty(self):
        mock_ak.bond_china_yield.return_value = MagicMock()
        mock_ak.bond_china_yield.return_value.iterrows.return_value = []
        assert fetch_bond_yield() == []

    def test_fetch_bond_yield_akshare_unavailable(self):
        with patch('finance_toolkit.data_fetching.bond_fetcher.HAS_AKSHARE', False):
            with pytest.raises(ImportError):
                fetch_bond_yield()


class TestConvertibleBond:
    def test_fetch_convertible_bond_spot_success(self):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '代码': '127045', '名称': '23招路转债',
            '股票代码': '000043', '股票名称': '神州高铁',
            '现价': 125.50, '昨收': 124.00,
            '涨跌幅': 1.21, '溢价率': 5.30,
            '转股价': 3.50, '到期日': '2029-01-15',
            '成交量': 50000, '成交额': 6275000.0,
        }.get(k, d)
        mock_ak.bond_zh_hs_cov_spot.return_value = MagicMock()
        mock_ak.bond_zh_hs_cov_spot.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_convertible_bond_spot()
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], ConvertibleBond)
        assert result[0].bond_code == '127045'
        assert result[0].bond_name == '23招路转债'
        assert result[0].price == 125.50
        assert result[0].change_pct == 1.21
        assert result[0].premium == 5.30
        assert result[0].source == 'akshare'

    def test_fetch_convertible_bond_spot_empty(self):
        mock_ak.bond_zh_hs_cov_spot.return_value = MagicMock()
        mock_ak.bond_zh_hs_cov_spot.return_value.iterrows.return_value = []
        assert fetch_convertible_bond_spot() == []

    def test_fetch_convertible_bond_spot_akshare_unavailable(self):
        with patch('finance_toolkit.data_fetching.bond_fetcher.HAS_AKSHARE', False):
            with pytest.raises(ImportError):
                fetch_convertible_bond_spot()


class TestConvertibleBondKline:
    def test_fetch_convertible_bond_history_success(self, sample_bond_code):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '日期': '2024-06-01', '开盘': 124.00, '收盘': 125.50,
            '最高': 126.00, '最低': 123.50, '成交量': 50000,
            '成交额': 6275000.0, '昨收': 124.00,
        }.get(k, d)
        mock_ak.bond_zh_hs_cov_daily.return_value = MagicMock()
        mock_ak.bond_zh_hs_cov_daily.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_convertible_bond_history(sample_bond_code)
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], ConvertibleBondKline)
        assert result[0].bond_code == sample_bond_code
        assert result[0].close == 125.50
        assert abs(result[0].change_pct - 1.21) < 0.01

    def test_fetch_convertible_bond_history_with_date_range(self, sample_bond_code):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '日期': '2024-06-01', '开盘': 124.00, '收盘': 125.50,
            '最高': 126.00, '最低': 123.50, '成交量': 50000,
            '成交额': 6275000.0, '昨收': 124.00,
        }.get(k, d)
        mock_ak.bond_zh_hs_cov_daily.return_value = MagicMock()
        mock_ak.bond_zh_hs_cov_daily.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_convertible_bond_history(sample_bond_code, start_date='20240101', end_date='20241231')
        assert len(result) > 0
        mock_ak.bond_zh_hs_cov_daily.assert_called_once()

    def test_fetch_convertible_bond_history_empty(self, sample_bond_code):
        mock_ak.bond_zh_hs_cov_daily.return_value = MagicMock()
        mock_ak.bond_zh_hs_cov_daily.return_value.iterrows.return_value = []
        assert fetch_convertible_bond_history(sample_bond_code) == []

    def test_fetch_convertible_bond_history_akshare_unavailable(self):
        with patch('finance_toolkit.data_fetching.bond_fetcher.HAS_AKSHARE', False):
            with pytest.raises(ImportError):
                fetch_convertible_bond_history('127045')


class TestCorporateBond:
    def test_fetch_corporate_bond_spot_success(self):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '代码': '127045', '名称': '23招路转债',
            '收盘价': 125.50, '到期收益率': 3.20,
            '涨跌': 1.50, '涨跌幅': 1.21,
            '成交量': 50000,
        }.get(k, d)
        mock_ak.bond_zh_hs_daily.return_value = MagicMock()
        mock_ak.bond_zh_hs_daily.return_value.iterrows.return_value = [(0, mock_row)]
        result = fetch_corporate_bond_spot()
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], CorporateBond)
        assert result[0].bond_code == '127045'
        assert result[0].price == 125.50
        assert result[0].yield_rate == 3.20
        assert result[0].source == 'akshare'

    def test_fetch_corporate_bond_spot_empty(self):
        mock_ak.bond_zh_hs_daily.return_value = MagicMock()
        mock_ak.bond_zh_hs_daily.return_value.iterrows.return_value = []
        assert fetch_corporate_bond_spot() == []

    def test_fetch_corporate_bond_spot_akshare_unavailable(self):
        with patch('finance_toolkit.data_fetching.bond_fetcher.HAS_AKSHARE', False):
            with pytest.raises(ImportError):
                fetch_corporate_bond_spot()


class TestDataStructures:
    def test_bond_yield_structure(self):
        by = BondYield(
            date='2024-06-01', yield_1y=2.10, yield_2y=2.30,
            yield_3y=2.45, yield_5y=2.60, yield_10y=2.75,
        )
        assert by.date == '2024-06-01'
        assert by.yield_1y == 2.10
        assert by.yield_10y == 2.75
        assert by.source == 'akshare'
        assert by.timestamp is not None

    def test_convertible_bond_structure(self):
        cb = ConvertibleBond(
            bond_code='127045', bond_name='23招路转债',
            stock_code='000043', stock_name='神州高铁',
            price=125.50, prev_close=124.00,
            change_pct=1.21, premium=5.30,
            conversion_price=3.50, maturity_date='2029-01-15',
            volume=50000, amount=6275000.0,
        )
        assert cb.bond_code == '127045'
        assert cb.price == 125.50
        assert cb.premium == 5.30
        assert cb.source == 'akshare'

    def test_convertible_bond_kline_structure(self):
        ckl = ConvertibleBondKline(
            bond_code='127045', date='2024-06-01',
            open=124.00, close=125.50, high=126.00,
            low=123.50, volume=50000, amount=6275000.0,
            change_pct=1.21,
        )
        assert ckl.bond_code == '127045'
        assert ckl.close == 125.50
        assert ckl.source == 'akshare'

    def test_corporate_bond_structure(self):
        corp = CorporateBond(
            bond_code='127045', bond_name='23招路转债',
            price=125.50, yield_rate=3.20,
            change=1.50, change_pct=1.21,
            volume=50000,
        )
        assert corp.bond_code == '127045'
        assert corp.price == 125.50
        assert corp.yield_rate == 3.20
        assert corp.source == 'akshare'


class TestBondDataFetcher:
    def test_get_bond_yield(self):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '日期': '2024-06-01', '1年': 2.10, '2年': 2.30,
            '3年': 2.45, '5年': 2.60, '10年': 2.75,
        }.get(k, d)
        mock_ak.bond_china_yield.return_value = MagicMock()
        mock_ak.bond_china_yield.return_value.iterrows.return_value = [(0, mock_row)]
        fetcher = BondDataFetcher()
        result = fetcher.get_bond_yield()
        assert len(result) > 0
        assert isinstance(result[0], BondYield)

    def test_get_convertible_bond_spot(self):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '代码': '127045', '名称': '23招路转债',
            '股票代码': '000043', '股票名称': '神州高铁',
            '现价': 125.50, '昨收': 124.00,
            '涨跌幅': 1.21, '溢价率': 5.30,
            '转股价': 3.50, '到期日': '2029-01-15',
            '成交量': 50000, '成交额': 6275000.0,
        }.get(k, d)
        mock_ak.bond_zh_hs_cov_spot.return_value = MagicMock()
        mock_ak.bond_zh_hs_cov_spot.return_value.iterrows.return_value = [(0, mock_row)]
        fetcher = BondDataFetcher()
        result = fetcher.get_convertible_bond_spot()
        assert len(result) > 0
        assert isinstance(result[0], ConvertibleBond)

    def test_get_convertible_bond_history(self, sample_bond_code):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '日期': '2024-06-01', '开盘': 124.00, '收盘': 125.50,
            '最高': 126.00, '最低': 123.50, '成交量': 50000,
            '成交额': 6275000.0, '昨收': 124.00,
        }.get(k, d)
        mock_ak.bond_zh_hs_cov_daily.return_value = MagicMock()
        mock_ak.bond_zh_hs_cov_daily.return_value.iterrows.return_value = [(0, mock_row)]
        fetcher = BondDataFetcher()
        result = fetcher.get_convertible_bond_history(sample_bond_code)
        assert len(result) > 0
        assert isinstance(result[0], ConvertibleBondKline)

    def test_get_corporate_bond_spot(self):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '代码': '127045', '名称': '23招路转债',
            '收盘价': 125.50, '到期收益率': 3.20,
            '涨跌': 1.50, '涨跌幅': 1.21,
            '成交量': 50000,
        }.get(k, d)
        mock_ak.bond_zh_hs_daily.return_value = MagicMock()
        mock_ak.bond_zh_hs_daily.return_value.iterrows.return_value = [(0, mock_row)]
        fetcher = BondDataFetcher()
        result = fetcher.get_corporate_bond_spot()
        assert len(result) > 0
        assert isinstance(result[0], CorporateBond)

    def test_get_bond_summary(self, sample_bond_code):
        mock_row = MagicMock()
        mock_row.get = lambda k, d=None: {
            '代码': '127045', '名称': '23招路转债',
            '股票代码': '000043', '股票名称': '神州高铁',
            '现价': 125.50, '昨收': 124.00,
            '涨跌幅': 1.21, '溢价率': 5.30,
            '转股价': 3.50, '到期日': '2029-01-15',
            '成交量': 50000, '成交额': 6275000.0,
        }.get(k, d)
        mock_ak.bond_zh_hs_cov_spot.return_value = MagicMock()
        mock_ak.bond_zh_hs_cov_spot.return_value.iterrows.return_value = [(0, mock_row)]
        mock_hist_row = MagicMock()
        mock_hist_row.get = lambda k, d=None: {
            '日期': '2024-06-01', '开盘': 124.00, '收盘': 125.50,
            '最高': 126.00, '最低': 123.50, '成交量': 50000,
            '成交额': 6275000.0, '昨收': 124.00,
        }.get(k, d)
        mock_ak.bond_zh_hs_cov_daily.return_value = MagicMock()
        mock_ak.bond_zh_hs_cov_daily.return_value.iterrows.return_value = [(0, mock_hist_row)]
        fetcher = BondDataFetcher()
        result = fetcher.get_bond_summary(sample_bond_code)
        assert 'spot' in result
        assert 'history' in result
        assert len(result['spot']) > 0
        assert len(result['history']) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
