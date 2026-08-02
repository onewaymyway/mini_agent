"""
L4 特征工程清洗器
衍生指标计算：K线形态、技术指标雏形、波动率等
"""

from typing import Dict, List
from .pipeline import BaseCleaner, CleanLevel, CleanResult


class FeatureEngineer(BaseCleaner):
    """L4: 特征工程 - 单根 K 线形态特征"""
    
    level = CleanLevel.L4_FEATURE
    source_types = ['kline']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        
        # 仅对 K 线数据计算形态特征
        if raw_data.get('data_type') == 'kline':
            self._compute_candle_features(payload)
        
        return CleanResult(data=raw_data, level=self.level, passed=True)
    
    def _compute_candle_features(self, payload: Dict):
        """计算单根 K 线形态特征"""
        required = ['open', 'high', 'low', 'close']
        if not all(k in payload for k in required):
            return
        
        o, h, low_price, c = payload['open'], payload['high'], payload['low'], payload['close']
        
        # 实体大小
        body = abs(c - o)
        total_range = h - low_price
        
        if total_range > 0:
            payload['body_ratio'] = body / total_range  # 实体占比
            payload['upper_shadow'] = h - max(o, c)    # 上影线
            payload['lower_shadow'] = min(o, c) - low_price    # 下影线
            payload['upper_shadow_ratio'] = payload['upper_shadow'] / total_range
            payload['lower_shadow_ratio'] = payload['lower_shadow'] / total_range
        else:
            payload['body_ratio'] = 0
            payload['upper_shadow'] = 0
            payload['lower_shadow'] = 0
            payload['upper_shadow_ratio'] = 0
            payload['lower_shadow_ratio'] = 0
        
        # K 线类型判断
        payload['is_bullish'] = c > o  # 阳线
        payload['is_bearish'] = c < o  # 阴线
        payload['is_doji'] = payload['body_ratio'] < 0.1  # 十字星
        
        # 锤头线 / 上吊线
        if payload['body_ratio'] > 0:
            payload['is_hammer'] = (payload['lower_shadow'] > 2 * body and
                                     payload['upper_shadow'] < 0.1 * body)
            payload['is_hanging_man'] = payload['is_hammer'] and payload['is_bullish']
            payload['is_inverted_hammer'] = (payload['upper_shadow'] > 2 * body and
                                              payload['lower_shadow'] < 0.1 * body)
            payload['is_shooting_star'] = payload['is_inverted_hammer'] and payload['is_bearish']
        
        # 长实体
        payload['is_long_body'] = payload['body_ratio'] > 0.7
        payload['is_marubozu'] = (payload['upper_shadow_ratio'] < 0.05 and
                                   payload['lower_shadow_ratio'] < 0.05)
        
        # 涨跌幅
        if 'pre_close' in payload and payload['pre_close']:
            payload['pct_chg'] = (c - payload['pre_close']) / payload['pre_close'] * 100
        
        # 振幅
        if 'pre_close' in payload and payload['pre_close']:
            payload['amplitude'] = (h - low_price) / payload['pre_close'] * 100


class TechnicalFeatureEngineer(BaseCleaner):
    """L4: 技术指标特征 (需要历史窗口，此处提供单步计算接口)"""
    
    level = CleanLevel.L4_FEATURE
    source_types = ['kline']
    
    def __init__(self, window: int = 20):
        self.window = window
        self.history: List[Dict] = []
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        
        if raw_data.get('data_type') == 'kline':
            # 加入历史
            self.history.append(payload)
            if len(self.history) > self.window:
                self.history.pop(0)
            
            # 计算移动平均等
            if len(self.history) >= 5:
                self._compute_ma_features(payload)
            if len(self.history) >= 14:
                self._compute_rsi(payload)
            if len(self.history) >= 20:
                self._compute_bollinger(payload)
                self._compute_atr(payload)
        
        return CleanResult(data=raw_data, level=self.level, passed=True)
    
    def _compute_ma_features(self, payload: Dict):
        """计算移动平均"""
        closes = [p.get('close') for p in self.history if p.get('close') is not None]
        if len(closes) >= 5:
            payload['ma5'] = sum(closes[-5:]) / 5
        if len(closes) >= 10:
            payload['ma10'] = sum(closes[-10:]) / 10
        if len(closes) >= 20:
            payload['ma20'] = sum(closes[-20:]) / 20
        if len(closes) >= 60:
            payload['ma60'] = sum(closes[-60:]) / 60
        
        # 均线多空排列
        if all(k in payload for k in ['ma5', 'ma10', 'ma20']):
            payload['ma_bullish'] = payload['ma5'] > payload['ma10'] > payload['ma20']
            payload['ma_bearish'] = payload['ma5'] < payload['ma10'] < payload['ma20']
    
    def _compute_rsi(self, payload: Dict):
        """计算 RSI (14)"""
        closes = [p.get('close') for p in self.history if p.get('close') is not None]
        if len(closes) < 15:
            return
        
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            if diff > 0:
                gains.append(diff)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(-diff)
        
        avg_gain = sum(gains[-14:]) / 14
        avg_loss = sum(losses[-14:]) / 14
        
        if avg_loss == 0:
            payload['rsi'] = 100
        else:
            rs = avg_gain / avg_loss
            payload['rsi'] = 100 - 100 / (1 + rs)
    
    def _compute_bollinger(self, payload: Dict):
        """计算布林带 (20, 2)"""
        closes = [p.get('close') for p in self.history if p.get('close') is not None]
        if len(closes) < 20:
            return
        
        recent = closes[-20:]
        mean = sum(recent) / 20
        std = (sum((x - mean) ** 2 for x in recent) / 20) ** 0.5
        
        payload['boll_mid'] = mean
        payload['boll_upper'] = mean + 2 * std
        payload['boll_lower'] = mean - 2 * std
        payload['boll_width'] = (payload['boll_upper'] - payload['boll_lower']) / mean if mean else 0
        
        # 价格位置
        if payload['boll_upper'] != payload['boll_lower']:
            payload['boll_position'] = (payload.get('close', 0) - payload['boll_lower']) / (payload['boll_upper'] - payload['boll_lower'])
    
    def _compute_atr(self, payload: Dict):
        """计算 ATR (14)"""
        if len(self.history) < 15:
            return
        
        trs = []
        for i in range(1, len(self.history)):
            h = self.history[i].get('high')
            low_price = self.history[i].get('low')
            pc = self.history[i-1].get('close')
            if h is not None and low_price is not None and pc is not None:
                tr = max(h - low_price, abs(h - pc), abs(low_price - pc))
                trs.append(tr)
        
        if len(trs) >= 14:
            payload['atr'] = sum(trs[-14:]) / 14
    
    def reset(self):
        """重置历史"""
        self.history.clear()


class VolatilityFeatureEngineer(BaseCleaner):
    """L4: 波动率特征"""
    
    level = CleanLevel.L4_FEATURE
    source_types = ['kline', 'quote']
    
    def clean(self, raw_data: Dict) -> CleanResult:
        payload = raw_data.get('payload', {})
        
        # 历史波动率 (需要窗口，此处仅单步)
        if 'close' in payload and 'pre_close' in payload and payload['pre_close']:
            daily_ret = (payload['close'] - payload['pre_close']) / payload['pre_close']
            payload['daily_return'] = daily_ret
            payload['abs_return'] = abs(daily_ret)
        
        # Parkinson 波动率 (使用高低价)
        if all(k in payload for k in ['high', 'low']) and payload['high'] and payload['low']:
            import math
            payload['parkinson_vol'] = math.log(payload['high'] / payload['low']) ** 2 / (4 * math.log(2))
        
        # Garman-Klass 波动率
        if all(k in payload for k in ['open', 'high', 'low', 'close']):
            import math
            o, h, low_price, c = payload['open'], payload['high'], payload['low'], payload['close']
            if o and h and low_price and c:
                payload['gk_vol'] = 0.5 * math.log(h/low_price)**2 - (2*math.log(2)-1) * math.log(c/o)**2
        
        return CleanResult(data=raw_data, level=self.level, passed=True)