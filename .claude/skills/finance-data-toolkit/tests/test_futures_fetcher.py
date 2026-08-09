"""
期货数据抓取模块单元测试
"""

import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# 创建 mock akshare
mock_ak = MagicMock()


class MockDataFrame:
    """模拟 DataFrame"""
    def __init__(self, data):
        self.data = data
    
    def iterrows(self):
        for row in self.data:
            yield '', row
    
    @property
    def empty(self):
        return len(self.data) == 0


class TestFuturesSpot:
    """期货实时行情测试"""

    def test_fetch_futures_spot_success(self):
        """成功获取期货行情"""
        mock_ak.futures_spot_price.return_value = MockDataFrame([
            {
                'symbol': 'IF2406',
                'name': '沪深300',
                'last_price': 3800.0,
                'change': 50.0,
                'change_pct': 1.33,
                'volume': 100000,
                'open_interest': 500000,
                'high': 3820.0,
                'low': 3780.0,
                'open': 3790.0,
                'prev_close': 3750.0,
            }
        ])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_spot
            result = fetch_futures_spot()
            assert len(result) == 1
            assert result[0]['symbol'] == 'IF2406'
            assert result[0]['last_price'] == 3800.0
            assert result[0]['change_pct'] == 1.33

    def test_fetch_futures_spot_empty(self):
        """空数据返回"""
        mock_ak.futures_spot_price.return_value = MockDataFrame([])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_spot
            result = fetch_futures_spot()
            assert result == []

    def test_fetch_futures_spot_akshare_unavailable(self):
        """akshare 不可用"""
        with patch.dict(sys.modules, {'akshare': None}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_spot
            result = fetch_futures_spot()
            assert result == []


class TestFuturesKline:
    """期货K线测试"""

    def test_fetch_futures_kline_success(self):
        """成功获取期货K线"""
        mock_ak.futures_main_sina.return_value = MockDataFrame([
            {
                'date': '20240601',
                'open': 3750.0,
                'high': 3800.0,
                'low': 3740.0,
                'close': 3780.0,
                'volume': 100000,
                'open_interest': 500000,
            }
        ])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_kline
            result = fetch_futures_kline('IF2406')
            assert len(result) == 1
            assert result[0]['symbol'] == 'IF2406'
            assert result[0]['close'] == 3780.0

    def test_fetch_futures_kline_empty(self):
        """空K线数据"""
        mock_ak.futures_main_sina.return_value = MockDataFrame([])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_kline
            result = fetch_futures_kline('IF2406')
            assert result == []

    def test_fetch_futures_kline_with_date_range(self):
        """带日期范围的K线"""
        mock_ak.futures_main_sina.return_value = MockDataFrame([
            {'date': '20240601', 'open': 3750.0, 'high': 3800.0, 'low': 3740.0, 'close': 3780.0, 'volume': 100000, 'open_interest': 500000}
        ])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_kline
            result = fetch_futures_kline('IF2406', '20240601', '20240630')
            assert len(result) == 1

    def test_fetch_futures_kline_akshare_unavailable(self):
        """akshare 不可用"""
        with patch.dict(sys.modules, {'akshare': None}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_kline
            result = fetch_futures_kline('IF2406')
            assert result == []


class TestFuturesPosition:
    """期货持仓测试"""

    def test_fetch_futures_position_success(self):
        """成功获取持仓数据"""
        mock_ak.futures_position_detail.return_value = MockDataFrame([
            {
                'symbol': 'IF2406',
                'date': '20240601',
                'long_position': 250000,
                'short_position': 250000,
                'change': 1000,
                'total_position': 500000,
            }
        ])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_position
            result = fetch_futures_position()
            assert len(result) == 1
            assert result[0]['symbol'] == 'IF2406'
            assert result[0]['long_position'] == 250000

    def test_fetch_futures_position_empty(self):
        """空持仓数据"""
        mock_ak.futures_position_detail.return_value = MockDataFrame([])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_position
            result = fetch_futures_position()
            assert result == []

    def test_fetch_futures_position_akshare_unavailable(self):
        """akshare 不可用"""
        with patch.dict(sys.modules, {'akshare': None}):
            from finance_toolkit.data_fetching.futures_fetcher import fetch_futures_position
            result = fetch_futures_position()
            assert result == []


class TestDataStructures:
    """数据结构测试"""

    def test_futures_spot_structure(self):
        """期货行情数据结构"""
        from finance_toolkit.data_fetching.futures_fetcher import FuturesSpot
        spot = FuturesSpot(
            symbol='IF2406',
            name='沪深300',
            last_price=3800.0,
            change=50.0,
            change_pct=1.33,
            volume=100000,
            open_interest=500000,
            high=3820.0,
            low=3780.0,
            open=3790.0,
            prev_close=3750.0,
            timestamp=datetime.now(),
        )
        d = spot.to_dict()
        assert d['symbol'] == 'IF2406'
        assert d['last_price'] == 3800.0
        assert 'timestamp' in d

    def test_futures_kline_structure(self):
        """期货K线数据结构"""
        from finance_toolkit.data_fetching.futures_fetcher import FuturesKline
        kline = FuturesKline(
            symbol='IF2406',
            date='20240601',
            open=3750.0,
            high=3800.0,
            low=3740.0,
            close=3780.0,
            volume=100000,
            open_interest=500000,
        )
        d = kline.to_dict()
        assert d['symbol'] == 'IF2406'
        assert d['close'] == 3780.0

    def test_futures_position_structure(self):
        """期货持仓数据结构"""
        from finance_toolkit.data_fetching.futures_fetcher import FuturesPosition
        position = FuturesPosition(
            symbol='IF2406',
            date='20240601',
            long_position=250000,
            short_position=250000,
            change=1000,
            total_position=500000,
        )
        d = position.to_dict()
        assert d['symbol'] == 'IF2406'
        assert d['long_position'] == 250000


class TestFuturesDataFetcher:
    """FuturesDataFetcher 便捷类测试"""

    def test_get_futures_spot(self):
        """获取期货行情"""
        mock_ak.futures_spot_price.return_value = MockDataFrame([
            {'symbol': 'IF2406', 'name': '沪深300', 'last_price': 3800.0, 'change': 50.0, 'change_pct': 1.33, 'volume': 100000, 'open_interest': 500000, 'high': 3820.0, 'low': 3780.0, 'open': 3790.0, 'prev_close': 3750.0}
        ])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            import finance_toolkit.data_fetching.futures_fetcher as ff
            ff.ak = mock_ak
            from finance_toolkit.data_fetching.futures_fetcher import FuturesDataFetcher
            fetcher = FuturesDataFetcher()
            result = fetcher.get_futures_spot()
            assert len(result) == 1

    def test_get_futures_kline(self):
        """获取期货K线"""
        mock_ak.futures_main_sina.return_value = MockDataFrame([
            {'date': '20240601', 'open': 3750.0, 'high': 3800.0, 'low': 3740.0, 'close': 3780.0, 'volume': 100000, 'open_interest': 500000}
        ])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            import finance_toolkit.data_fetching.futures_fetcher as ff
            ff.ak = mock_ak
            from finance_toolkit.data_fetching.futures_fetcher import FuturesDataFetcher
            fetcher = FuturesDataFetcher()
            result = fetcher.get_futures_kline('IF2406')
            assert len(result) == 1

    def test_get_futures_position(self):
        """获取期货持仓"""
        mock_ak.futures_position_detail.return_value = MockDataFrame([
            {'symbol': 'IF2406', 'date': '20240601', 'long_position': 250000, 'short_position': 250000, 'change': 1000, 'total_position': 500000}
        ])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            import finance_toolkit.data_fetching.futures_fetcher as ff
            ff.ak = mock_ak
            from finance_toolkit.data_fetching.futures_fetcher import FuturesDataFetcher
            fetcher = FuturesDataFetcher()
            result = fetcher.get_futures_position()
            assert len(result) == 1

    def test_get_futures_summary(self):
        """获取期货数据摘要"""
        mock_ak.futures_spot_price.return_value = MockDataFrame([
            {'symbol': 'IF2406', 'name': '沪深300', 'last_price': 3800.0, 'change': 50.0, 'change_pct': 1.33, 'volume': 100000, 'open_interest': 500000, 'high': 3820.0, 'low': 3780.0, 'open': 3790.0, 'prev_close': 3750.0}
        ])
        mock_ak.futures_position_detail.return_value = MockDataFrame([
            {'symbol': 'IF2406', 'date': '20240601', 'long_position': 250000, 'short_position': 250000, 'change': 1000, 'total_position': 500000}
        ])
        
        with patch.dict(sys.modules, {'akshare': mock_ak}):
            import finance_toolkit.data_fetching.futures_fetcher as ff
            ff.ak = mock_ak
            from finance_toolkit.data_fetching.futures_fetcher import FuturesDataFetcher
            fetcher = FuturesDataFetcher()
            result = fetcher.get_futures_summary()
            assert result['spot_count'] == 1
            assert result['position_count'] == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
