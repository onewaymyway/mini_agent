"""
Tests for technical analysis module
"""

import pytest
import pandas as pd
import numpy as np


class TestTechnicalIndicators:
    """Test technical indicator calculations"""
    
    @pytest.fixture
    def sample_kline_data(self):
        """Create sample K-line data for testing"""
        dates = pd.date_range('2024-01-01', periods=100, freq='D')
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(100) * 0.5)
        high = close + np.abs(np.random.randn(100) * 0.3)
        low = close - np.abs(np.random.randn(100) * 0.3)
        open_ = close + np.random.randn(100) * 0.2
        volume = np.abs(np.random.randn(100) * 1000000) + 500000
        
        return pd.DataFrame({
            'open': open_,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume,
        }, index=dates)
    
    def test_calc_ma(self, sample_kline_data):
        """Test MA calculation"""
        from finance_toolkit.technical_analysis import calc_ma
        
        ma5 = calc_ma(sample_kline_data['close'].tolist(), 5)
        ma20 = calc_ma(sample_kline_data['close'].tolist(), 20)
        
        assert len(ma5) == len(sample_kline_data)
        assert len(ma20) == len(sample_kline_data)
        assert ma5[-1] != ma20[-1]
        # First 4 values should be None for MA5
        assert all(v is None for v in ma5[:4])
        assert all(v is not None for v in ma5[4:])
    
    def test_calc_ema(self, sample_kline_data):
        """Test EMA calculation"""
        from finance_toolkit.technical_analysis import calc_ema
        
        ema12 = calc_ema(sample_kline_data['close'].tolist(), 12)
        
        assert len(ema12) == len(sample_kline_data)
        assert not all(v is None for v in ema12)
        # EMA has leading None values until period-1
        assert ema12[11] is not None  # First valid value at index period-1
    
    def test_calc_macd(self, sample_kline_data):
        """Test MACD calculation"""
        from finance_toolkit.technical_analysis import calc_macd
        
        macd_dict = calc_macd(sample_kline_data['close'].tolist())
        
        assert 'DIF' in macd_dict
        assert 'DEA' in macd_dict
        assert 'MACD' in macd_dict
        assert len(macd_dict['DIF']) == len(sample_kline_data)
        # MACD = 2 * (DIF - DEA)
        for i in range(len(macd_dict['DIF'])):
            if macd_dict['DIF'][i] is not None and macd_dict['DEA'][i] is not None:
                expected = 2 * (macd_dict['DIF'][i] - macd_dict['DEA'][i])
                assert abs(macd_dict['MACD'][i] - expected) < 0.001
    
    def test_calc_rsi(self, sample_kline_data):
        """Test RSI calculation"""
        from finance_toolkit.technical_analysis import calc_rsi
        
        rsi14 = calc_rsi(sample_kline_data['close'].tolist(), 14)
        
        assert len(rsi14) == len(sample_kline_data)
        # RSI should be between 0 and 100
        valid_rsi = [v for v in rsi14 if v is not None]
        assert all(0 <= v <= 100 for v in valid_rsi)
    
    def test_calc_boll(self, sample_kline_data):
        """Test Bollinger Bands calculation"""
        from finance_toolkit.technical_analysis import calc_boll
        
        boll_dict = calc_boll(sample_kline_data['close'].tolist(), 20, 2)
        
        assert 'UPPER' in boll_dict
        assert 'MIDDLE' in boll_dict
        assert 'LOWER' in boll_dict
        assert len(boll_dict['UPPER']) == len(sample_kline_data)
        # Upper > Middle > Lower
        for i in range(len(boll_dict['UPPER'])):
            if boll_dict['UPPER'][i] is not None:
                assert boll_dict['UPPER'][i] > boll_dict['MIDDLE'][i]
                assert boll_dict['MIDDLE'][i] > boll_dict['LOWER'][i]
    
    def test_calc_kdj(self, sample_kline_data):
        """Test KDJ calculation"""
        from finance_toolkit.technical_analysis import calc_kdj
        
        kdj_dict = calc_kdj(
            sample_kline_data['high'].tolist(),
            sample_kline_data['low'].tolist(),
            sample_kline_data['close'].tolist()
        )
        
        assert 'K' in kdj_dict
        assert 'D' in kdj_dict
        assert 'J' in kdj_dict
        assert len(kdj_dict['K']) == len(sample_kline_data)
        # J = 3*K - 2*D
        for i in range(len(kdj_dict['K'])):
            if kdj_dict['K'][i] is not None and kdj_dict['D'][i] is not None:
                expected = 3 * kdj_dict['K'][i] - 2 * kdj_dict['D'][i]
                assert abs(kdj_dict['J'][i] - expected) < 0.001
    
    def test_generate_signals(self, sample_kline_data):
        """Test signal generation"""
        from finance_toolkit.technical_analysis import generate_signals, calc_ma, calc_macd, calc_rsi, calc_boll, calc_kdj
        
        # First calculate indicators in the format expected by generate_signals
        closes = sample_kline_data['close'].tolist()
        highs = sample_kline_data['high'].tolist()
        lows = sample_kline_data['low'].tolist()
        
        indicators = {
            'MA': {
                'MA5': calc_ma(closes, 5),
                'MA10': calc_ma(closes, 10),
                'MA20': calc_ma(closes, 20),
                'MA60': calc_ma(closes, 60),
            },
            'MACD': calc_macd(closes),
            'RSI': calc_rsi(closes, 14),
            'BOLL': calc_boll(closes, 20, 2),
            'KDJ': calc_kdj(highs, lows, closes),
        }
        
        # Convert to list of dicts format
        kline_list = []
        for idx, row in sample_kline_data.iterrows():
            kline_list.append({
                'date': idx.strftime('%Y-%m-%d'),
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume'],
            })
        
        signals = generate_signals(kline_list, indicators)
        
        assert isinstance(signals, dict)
        # Should have various signal types
        # Check that at least some signals are generated
        assert len(signals) > 0
        for sig_name, sig_value in signals.items():
            assert isinstance(sig_value, str)
    
    def test_analyze_kline_data(self, sample_kline_data):
        """Test full K-line analysis"""
        from finance_toolkit.technical_analysis import analyze_kline_data
        
        # Convert to list of dicts format expected by analyze_kline_data
        kline_list = []
        for idx, row in sample_kline_data.iterrows():
            kline_list.append({
                'date': idx.strftime('%Y-%m-%d'),
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume'],
            })
        
        result = analyze_kline_data(kline_list)
        
        assert 'price_stats' in result
        assert 'latest_indicators' in result
        assert 'signals' in result
        # Note: 'trend' is not in the result, check for actual keys
        assert 'indicators' in result
        assert 'kline_count' in result
        assert 'date_range' in result
        
        # Check price stats
        assert 'current_price' in result['price_stats']
        assert 'change_1d_pct' in result['price_stats']
        assert 'period_high_20d' in result['price_stats']
        assert 'period_low_20d' in result['price_stats']
        
        # Check signals
        assert isinstance(result['signals'], dict)
        for sig_name, sig_value in result['signals'].items():
            assert isinstance(sig_value, str)


class TestTechnicalAnalysisEdgeCases:
    """Test edge cases"""
    
    def test_empty_dataframe(self):
        """Test with empty dataframe"""
        from finance_toolkit.technical_analysis import calc_ma
        
        empty_list = []
        result = calc_ma(empty_list, 5)
        assert len(result) == 0
    
    def test_single_row(self):
        """Test with single row"""
        from finance_toolkit.technical_analysis import calc_ma
        
        single = [100.0]
        result = calc_ma(single, 5)
        assert len(result) == 1
        assert result[0] is None
    
    def test_constant_price(self):
        """Test with constant price"""
        from finance_toolkit.technical_analysis import calc_rsi, calc_boll
        
        constant = [100.0] * 50
        rsi = calc_rsi(constant, 14)
        # For constant price, RSI = 100 (no losses, Wilder smoothing)
        valid = [v for v in rsi if v is not None]
        if len(valid) > 0:
            # For constant price, RSI = 100 (avg_loss == 0)
            assert all(abs(v - 100) <= 1 for v in valid)
        
        boll = calc_boll(constant, 20, 2)
        valid_boll = [(boll['UPPER'][i], boll['MIDDLE'][i], boll['LOWER'][i]) 
                      for i in range(len(boll['UPPER'])) 
                      if boll['UPPER'][i] is not None]
        if len(valid_boll) > 0:
            # Upper and lower should equal middle for constant price (std = 0)
            for u, m, lower in valid_boll:
                assert abs(u - m) < 0.1
                assert abs(lower - m) < 0.1
    
    def test_nan_handling(self):
        """Test handling of NaN values in input"""
        from finance_toolkit.technical_analysis import calc_ma
        
        data = [100, 101, float('nan'), 103, 104, 105]
        result = calc_ma(data, 3)
        # Should handle NaN gracefully
        assert len(result) == len(data)
