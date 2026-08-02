# -*- coding: utf-8 -*-
"""
技术指标计算库
统一了 sina_kline_fetcher.py 和 indicators.py 的实现
支持 pandas/TA-Lib 两种计算模式
符合 finance-data-toolkit 架构
"""

import pandas as pd
from typing import List, Dict, Any, Optional, Union


# ============== 纯 Python 实现 (无依赖) ==============

def calc_ma(closes: Union[List[float], pd.Series], period: int) -> List[Optional[float]]:
    """简单移动平均 (SMA)"""
    closes = list(closes) if not isinstance(closes, list) else closes
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = sum(closes[i - period + 1:i + 1]) / period
    return result


def calc_ema(closes: Union[List[float], pd.Series], period: int) -> List[Optional[float]]:
    """指数移动平均 (EMA)"""
    closes = list(closes) if not isinstance(closes, list) else closes
    result = [None] * len(closes)
    if len(closes) < period:
        return result
    # 初始值用 SMA
    result[period - 1] = sum(closes[:period]) / period
    multiplier = 2 / (period + 1)
    for i in range(period, len(closes)):
        result[i] = (closes[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def calc_macd(closes: Union[List[float], pd.Series], 
              fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, List[Optional[float]]]:
    """MACD 指标: DIF, DEA, MACD柱 (标准 MACD 柱 = 2*(DIF-DEA))"""
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = [None] * len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif[i] = ema_fast[i] - ema_slow[i]
    # DEA 是 DIF 的 EMA
    dif_valid = [d for d in dif if d is not None]
    dea_full = [None] * len(closes)
    if len(dif_valid) >= signal:
        dea_calc = calc_ema(dif_valid, signal)
        start = next(i for i, d in enumerate(dif) if d is not None)
        for i, v in enumerate(dea_calc):
            dea_full[start + i] = v
    macd_hist = [None] * len(closes)
    for i in range(len(closes)):
        if dif[i] is not None and dea_full[i] is not None:
            macd_hist[i] = 2 * (dif[i] - dea_full[i])
    return {'DIF': dif, 'DEA': dea_full, 'MACD': macd_hist}


def calc_rsi(closes: Union[List[float], pd.Series], period: int = 14) -> List[Optional[float]]:
    """RSI 指标 (Wilder 平滑)"""
    closes = list(closes) if not isinstance(closes, list) else closes
    result = [None] * len(closes)
    if len(closes) < period + 1:
        return result
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        result[period] = 100
    else:
        rs = avg_gain / avg_loss
        result[period] = 100 - (100 / (1 + rs))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100 - (100 / (1 + rs))
    return result


def calc_boll(closes: Union[List[float], pd.Series], 
              period: int = 20, std_dev: float = 2) -> Dict[str, List[Optional[float]]]:
    """布林带: UPPER, MIDDLE, LOWER"""
    closes = list(closes) if not isinstance(closes, list) else closes
    middle = calc_ma(closes, period)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        mean = middle[i]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        upper[i] = mean + std_dev * std
        lower[i] = mean - std_dev * std
    return {'UPPER': upper, 'MIDDLE': middle, 'LOWER': lower}


def calc_kdj(highs: Union[List[float], pd.Series], 
             lows: Union[List[float], pd.Series], 
             closes: Union[List[float], pd.Series],
             n: int = 9, m1: int = 3, m2: int = 3) -> Dict[str, List[Optional[float]]]:
    """KDJ 随机指标"""
    highs = list(highs) if not isinstance(highs, list) else highs
    lows = list(lows) if not isinstance(lows, list) else lows
    closes = list(closes) if not isinstance(closes, list) else closes
    k_values = [None] * len(closes)
    d_values = [None] * len(closes)
    j_values = [None] * len(closes)
    prev_k = 50
    prev_d = 50
    for i in range(len(closes)):
        if i < n - 1:
            continue
        window_high = max(highs[i - n + 1:i + 1])
        window_low = min(lows[i - n + 1:i + 1])
        if window_high == window_low:
            rsv = 50
        else:
            rsv = (closes[i] - window_low) / (window_high - window_low) * 100
        k = (prev_k * (m1 - 1) + rsv) / m1
        d = (prev_d * (m2 - 1) + k) / m2
        j = 3 * k - 2 * d
        k_values[i] = k
        d_values[i] = d
        j_values[i] = j
        prev_k = k
        prev_d = d
    return {'K': k_values, 'D': d_values, 'J': j_values}


def calc_atr(highs: Union[List[float], pd.Series], 
             lows: Union[List[float], pd.Series], 
             closes: Union[List[float], pd.Series], 
             period: int = 14) -> List[Optional[float]]:
    """平均真实波幅 (ATR)"""
    highs = list(highs) if not isinstance(highs, list) else highs
    lows = list(lows) if not isinstance(lows, list) else lows
    closes = list(closes) if not isinstance(closes, list) else closes
    tr = [None] * len(closes)
    for i in range(1, len(closes)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
    return calc_ma(tr[1:], period)  # 简化：用 SMA 近似


def calc_cci(highs: Union[List[float], pd.Series], 
             lows: Union[List[float], pd.Series], 
             closes: Union[List[float], pd.Series], 
             period: int = 20) -> List[Optional[float]]:
    """顺势指标 (CCI)"""
    highs = list(highs) if not isinstance(highs, list) else highs
    lows = list(lows) if not isinstance(lows, list) else lows
    closes = list(closes) if not isinstance(closes, list) else closes
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    ma_tp = calc_ma(tp, period)
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = tp[i - period + 1:i + 1]
        mean = ma_tp[i]
        md = sum(abs(x - mean) for x in window) / period
        if md != 0:
            result[i] = (tp[i] - mean) / (0.015 * md)
    return result


def calc_williams_r(highs: Union[List[float], pd.Series], 
                    lows: Union[List[float], pd.Series], 
                    closes: Union[List[float], pd.Series], 
                    period: int = 14) -> List[Optional[float]]:
    """威廉指标 (Williams %R)"""
    highs = list(highs) if not isinstance(highs, list) else highs
    lows = list(lows) if not isinstance(lows, list) else lows
    closes = list(closes) if not isinstance(closes, list) else closes
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        highest_high = max(highs[i - period + 1:i + 1])
        lowest_low = min(lows[i - period + 1:i + 1])
        if highest_high != lowest_low:
            result[i] = (highest_high - closes[i]) / (highest_high - lowest_low) * -100
    return result


def calc_obv(closes: Union[List[float], pd.Series], 
             volumes: Union[List[float], pd.Series]) -> List[Optional[float]]:
    """能量潮指标 (OBV)"""
    closes = list(closes) if not isinstance(closes, list) else closes
    volumes = list(volumes) if not isinstance(volumes, list) else volumes
    obv = [0] * len(closes)
    obv[0] = volumes[0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv[i] = obv[i-1] + volumes[i]
        elif closes[i] < closes[i-1]:
            obv[i] = obv[i-1] - volumes[i]
        else:
            obv[i] = obv[i-1]
    return obv


def calc_mfi(highs: Union[List[float], pd.Series], 
             lows: Union[List[float], pd.Series], 
             closes: Union[List[float], pd.Series], 
             volumes: Union[List[float], pd.Series], 
             period: int = 14) -> List[Optional[float]]:
    """资金流量指标 (MFI)"""
    highs = list(highs) if not isinstance(highs, list) else highs
    lows = list(lows) if not isinstance(lows, list) else lows
    closes = list(closes) if not isinstance(closes, list) else closes
    volumes = list(volumes) if not isinstance(volumes, list) else volumes
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    mf = [tp[i] * volumes[i] for i in range(len(closes))]
    pos_mf = [0] * len(closes)
    neg_mf = [0] * len(closes)
    for i in range(1, len(closes)):
        if tp[i] > tp[i-1]:
            pos_mf[i] = mf[i]
        elif tp[i] < tp[i-1]:
            neg_mf[i] = mf[i]
    pos_sum = calc_ma(pos_mf, period)
    neg_sum = calc_ma(neg_mf, period)
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        if neg_sum[i] != 0:
            mfr = pos_sum[i] / neg_sum[i]
            result[i] = 100 - (100 / (1 + mfr))
    return result


# ============== 信号生成 ==============

def generate_signals(kline: List[Dict], indicators: Dict) -> Dict[str, str]:
    """根据技术指标生成交易信号"""
    if not kline:
        return {}
    last = kline[-1]
    signals = {}

    # MA 信号
    ma5 = indicators['MA'].get('MA5')
    ma10 = indicators['MA'].get('MA10')
    ma20 = indicators['MA'].get('MA20')
    ma60 = indicators['MA'].get('MA60')
    if all(v is not None for v in [ma5, ma10, ma20, ma60]):
        last_ma5 = ma5[-1]
        last_ma10 = ma10[-1]
        last_ma20 = ma20[-1]
        last_ma60 = ma60[-1]
        if last_ma5 > last_ma10 > last_ma20 > last_ma60:
            signals['MA_BULL'] = '多头排列 (强势)'
        elif last_ma5 < last_ma10 < last_ma20 < last_ma60:
            signals['MA_BEAR'] = '空头排列 (弱势)'
        else:
            signals['MA_MIXED'] = '均线交织 (震荡)'

    # MACD 信号
    macd = indicators['MACD']
    if macd['DIF'][-1] is not None and macd['DEA'][-1] is not None:
        dif_now = macd['DIF'][-1]
        dea_now = macd['DEA'][-1]
        dif_prev = macd['DIF'][-2] if macd['DIF'][-2] is not None else dif_now
        dea_prev = macd['DEA'][-2] if macd['DEA'][-2] is not None else dea_now
        if dif_prev <= dea_prev and dif_now > dea_now:
            signals['MACD_GOLDEN'] = 'MACD 金叉 (买入信号)'
        elif dif_prev >= dea_prev and dif_now < dea_now:
            signals['MACD_DEAD'] = 'MACD 死叉 (卖出信号)'
        elif dif_now > dea_now:
            signals['MACD_BULL'] = 'DIF 在 DEA 上方 (多头)'
        else:
            signals['MACD_BEAR'] = 'DIF 在 DEA 下方 (空头)'

    # RSI 信号
    rsi = indicators['RSI']
    if rsi[-1] is not None:
        rsi_val = rsi[-1]
        if rsi_val > 70:
            signals['RSI_OVERBOUGHT'] = f'RSI={rsi_val:.1f} 超买 (回调风险)'
        elif rsi_val < 30:
            signals['RSI_OVERSOLD'] = f'RSI={rsi_val:.1f} 超卖 (反弹机会)'
        else:
            signals['RSI_NORMAL'] = f'RSI={rsi_val:.1f} 正常区间'

    # BOLL 信号
    boll = indicators['BOLL']
    if boll['UPPER'][-1] is not None:
        close = last['close']
        upper = boll['UPPER'][-1]
        lower = boll['LOWER'][-1]
        middle = boll['MIDDLE'][-1]
        if close >= upper * 0.99:
            signals['BOLL_UPPER'] = '触及布林上轨 (超买)'
        elif close <= lower * 1.01:
            signals['BOLL_LOWER'] = '触及布林下轨 (超卖)'
        elif close > middle:
            signals['BOLL_ABOVE_MID'] = '价格在布林中轨上方 (偏强)'
        else:
            signals['BOLL_BELOW_MID'] = '价格在布林中轨下方 (偏弱)'

    # KDJ 信号
    kdj = indicators['KDJ']
    if kdj['K'][-1] is not None:
        k = kdj['K'][-1]
        d = kdj['D'][-1]
        j = kdj['J'][-1]
        k_prev = kdj['K'][-2] if kdj['K'][-2] is not None else k
        d_prev = kdj['D'][-2] if kdj['D'][-2] is not None else d
        if k_prev <= d_prev and k > d:
            signals['KDJ_GOLDEN'] = 'KDJ 金叉 (买入信号)'
        elif k_prev >= d_prev and k < d:
            signals['KDJ_DEAD'] = 'KDJ 死叉 (卖出信号)'
        elif j > 100:
            signals['KDJ_OVERBOUGHT'] = f'J={j:.1f} 超买'
        elif j < 0:
            signals['KDJ_OVERSOLD'] = f'J={j:.1f} 超卖'
        else:
            signals['KDJ_NORMAL'] = f'K={k:.1f} D={d:.1f} J={j:.1f}'

    return signals


# ============== 完整分析流程 ==============

def analyze_kline_data(kline: List[Dict]) -> Dict[str, Any]:
    """完整的 K 线技术分析：计算所有指标 + 生成信号 + 统计"""
    if not kline or len(kline) < 60:
        return {'error': 'K线数据不足'}

    closes = [k['close'] for k in kline]
    highs = [k['high'] for k in kline]
    lows = [k['low'] for k in kline]

    # 计算所有指标
    indicators = {
        'MA': {
            'MA5': calc_ma(closes, 5),
            'MA10': calc_ma(closes, 10),
            'MA20': calc_ma(closes, 20),
            'MA30': calc_ma(closes, 30),
            'MA60': calc_ma(closes, 60),
            'MA120': calc_ma(closes, 120),
            'MA250': calc_ma(closes, 250),
        },
        'EMA': {
            'EMA12': calc_ema(closes, 12),
            'EMA26': calc_ema(closes, 26),
        },
        'MACD': calc_macd(closes),
        'RSI': calc_rsi(closes, 14),
        'BOLL': calc_boll(closes, 20, 2),
        'KDJ': calc_kdj(highs, lows, closes),
    }

    # 生成信号
    signals = generate_signals(kline, indicators)

    # 提取最新指标值
    latest_indicators = {}
    for category, vals in indicators.items():
        if isinstance(vals, dict):
            for name, series in vals.items():
                if series and series[-1] is not None:
                    latest_indicators[f'{category}_{name}'] = round(series[-1], 4)
        elif isinstance(vals, list) and vals[-1] is not None:
            latest_indicators[category] = round(vals[-1], 4)

    # 价格统计
    last_20 = kline[-20:] if len(kline) >= 20 else kline
    last_60 = kline[-60:] if len(kline) >= 60 else kline
    price_stats = {
        'current_price': kline[-1]['close'],
        'period_high_20d': max(k['high'] for k in last_20),
        'period_low_20d': min(k['low'] for k in last_20),
        'period_high_60d': max(k['high'] for k in last_60),
        'period_low_60d': min(k['low'] for k in last_60),
        'avg_volume_20d': sum(k['volume'] for k in last_20) / len(last_20),
        'avg_volume_60d': sum(k['volume'] for k in last_60) / len(last_60),
        'change_1d_pct': round((kline[-1]['close'] - kline[-2]['close']) / kline[-2]['close'] * 100, 2) if len(kline) >= 2 else 0,
        'change_5d_pct': round((kline[-1]['close'] - kline[-5]['close']) / kline[-5]['close'] * 100, 2) if len(kline) >= 5 else 0,
        'change_20d_pct': round((kline[-1]['close'] - kline[-20]['close']) / kline[-20]['close'] * 100, 2) if len(kline) >= 20 else 0,
        'change_60d_pct': round((kline[-1]['close'] - kline[-60]['close']) / kline[-60]['close'] * 100, 2) if len(kline) >= 60 else 0,
    }

    return {
        'indicators': indicators,
        'latest_indicators': latest_indicators,
        'signals': signals,
        'price_stats': price_stats,
        'kline_count': len(kline),
        'date_range': {'start': kline[0]['date'], 'end': kline[-1]['date']},
    }


# ============== Pandas/TA-Lib 版本 (可选) ==============

try:
    import talib
    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False


def calc_indicators_talib(df: pd.DataFrame) -> pd.DataFrame:
    """使用 TA-Lib 计算技术指标 (需要安装 talib-binary)"""
    if not HAS_TALIB:
        raise ImportError("TA-Lib not installed. Run: pip install talib-binary")
    
    close = df['close'].values.astype(float)
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    volume = df['volume'].values.astype(float)

    # MA 系列
    for period in [5, 10, 20, 30, 60, 120, 250]:
        df[f'MA{period}'] = talib.SMA(close, timeperiod=period)

    # EMA 系列
    for period in [12, 26]:
        df[f'EMA{period}'] = talib.EMA(close, timeperiod=period)

    # MACD
    df['MACD_DIF'], df['MACD_DEA'], df['MACD_HIST'] = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    df['MACD_HIST'] = df['MACD_HIST'] * 2

    # RSI
    df['RSI6'] = talib.RSI(close, timeperiod=6)
    df['RSI12'] = talib.RSI(close, timeperiod=12)
    df['RSI24'] = talib.RSI(close, timeperiod=24)

    # BOLL
    df['BOLL_UPPER'], df['BOLL_MID'], df['BOLL_LOWER'] = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
    df['BOLL_WIDTH'] = (df['BOLL_UPPER'] - df['BOLL_LOWER']) / df['BOLL_MID']
    df['BOLL_PCT_B'] = (close - df['BOLL_LOWER']) / (df['BOLL_UPPER'] - df['BOLL_LOWER'])

    # KDJ
    df['KDJ_K'], df['KDJ_D'] = talib.STOCH(high, low, close, fastk_period=9, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0)
    df['KDJ_J'] = 3 * df['KDJ_K'] - 2 * df['KDJ_D']

    # 其他常用指标
    df['ATR'] = talib.ATR(high, low, close, timeperiod=14)
    df['CCI'] = talib.CCI(high, low, close, timeperiod=14)
    df['WILLR'] = talib.WILLR(high, low, close, timeperiod=14)
    df['OBV'] = talib.OBV(close, volume)
    df['MFI'] = talib.MFI(high, low, close, volume, timeperiod=14)

    # 成交量指标
    df['VOL_MA5'] = talib.SMA(volume, timeperiod=5)
    df['VOL_MA10'] = talib.SMA(volume, timeperiod=10)
    df['VOL_MA20'] = talib.SMA(volume, timeperiod=20)

    return df


def generate_signals_talib(df: pd.DataFrame) -> pd.DataFrame:
    """基于 TA-Lib 指标生成买卖信号"""
    if df.empty:
        return df

    signals = pd.DataFrame(index=df.index)
    signals['date'] = df['date']
    signals['close'] = df['close']

    # MA 信号
    signals['MA5_gt_MA10'] = (df['MA5'] > df['MA10']).astype(int)
    signals['MA10_gt_MA20'] = (df['MA10'] > df['MA20']).astype(int)
    signals['MA20_gt_MA60'] = (df['MA20'] > df['MA60']).astype(int)

    # MACD 信号
    signals['MACD_GOLDEN'] = ((df['MACD_DIF'] > df['MACD_DEA']) & (df['MACD_DIF'].shift(1) <= df['MACD_DEA'].shift(1))).astype(int)
    signals['MACD_DEAD'] = ((df['MACD_DIF'] < df['MACD_DEA']) & (df['MACD_DIF'].shift(1) >= df['MACD_DEA'].shift(1))).astype(int)
    signals['MACD_HIST_POS'] = (df['MACD_HIST'] > 0).astype(int)

    # RSI 信号
    signals['RSI_OVERSOLD'] = (df['RSI12'] < 30).astype(int)
    signals['RSI_OVERBOUGHT'] = (df['RSI12'] > 70).astype(int)

    # BOLL 信号
    signals['BOLL_UPPER_TOUCH'] = (df['close'] >= df['BOLL_UPPER']).astype(int)
    signals['BOLL_LOWER_TOUCH'] = (df['close'] <= df['BOLL_LOWER']).astype(int)
    signals['BOLL_SQUEEZE'] = (df['BOLL_WIDTH'] < df['BOLL_WIDTH'].rolling(20).mean() * 0.5).astype(int)

    # KDJ 信号
    signals['KDJ_GOLDEN'] = ((df['KDJ_K'] > df['KDJ_D']) & (df['KDJ_K'].shift(1) <= df['KDJ_D'].shift(1))).astype(int)
    signals['KDJ_DEAD'] = ((df['KDJ_K'] < df['KDJ_D']) & (df['KDJ_K'].shift(1) >= df['KDJ_D'].shift(1))).astype(int)
    signals['KDJ_OVERSOLD'] = (df['KDJ_J'] < 0).astype(int)
    signals['KDJ_OVERBOUGHT'] = (df['KDJ_J'] > 100).astype(int)

    # 综合评分
    score = 0
    score += signals['MA5_gt_MA10'] * 1
    score += signals['MA10_gt_MA20'] * 1
    score += signals['MA20_gt_MA60'] * 2
    score += signals['MACD_GOLDEN'] * 3
    score -= signals['MACD_DEAD'] * 3
    score += signals['MACD_HIST_POS'] * 1
    score += signals['RSI_OVERSOLD'] * 2
    score -= signals['RSI_OVERBOUGHT'] * 2
    score -= signals['BOLL_UPPER_TOUCH'] * 1
    score += signals['BOLL_LOWER_TOUCH'] * 2
    score += signals['BOLL_SQUEEZE'] * 1
    score += signals['KDJ_GOLDEN'] * 2
    score -= signals['KDJ_DEAD'] * 2
    score += signals['KDJ_OVERSOLD'] * 2
    score -= signals['KDJ_OVERBOUGHT'] * 2

    signals['TECH_SCORE'] = score

    def score_to_signal(s):
        if s >= 8:
            return '强烈买入'
        elif s >= 4:
            return '买入'
        elif s >= 1:
            return '弱买入'
        elif s >= -1:
            return '中性'
        elif s >= -4:
            return '弱卖出'
        elif s >= -8:
            return '卖出'
        else:
            return '强烈卖出'

    signals['SIGNAL'] = signals['TECH_SCORE'].apply(score_to_signal)
    return signals
