# -*- coding: utf-8 -*-
"""
新浪财经 K 线数据抓取器
数据源: 新浪财经 JSONP API
符合 finance-data-toolkit 统一数据契约
"""

import sys
import os
import json
import time
import argparse
import urllib.request
import urllib.error
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class FinanceData:
    """统一金融数据契约"""
    source: str
    data_type: str
    symbol: str
    timestamp: str
    payload: Dict[str, Any]
    raw: Optional[Dict] = None
    meta: Optional[Dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"


def to_sina_symbol(code: str) -> str:
    """将 6 位股票代码转换为新浪格式: 603000 -> sh603000, 000001 -> sz000001"""
    code = code.strip()
    if code.startswith(('sh', 'sz')):
        return code
    if code.startswith(('60', '68', '90')):
        return f'sh{code}'
    else:
        return f'sz{code}'


def fetch_kline(code: str, scale: str = '240', datalen: int = 1023,
                ma: str = 'no', retries: int = 3) -> List[Dict]:
    """从新浪财经获取 K 线数据"""
    symbol = to_sina_symbol(code)
    url = f"{SINA_KLINE_URL}?symbol={symbol}&scale={scale}&ma={ma}&datalen={datalen}"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn/',
        'Accept': '*/*',
    }

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode('utf-8', errors='ignore')

            # 提取 JSONP 数据: var=(...);
            idx = text.find('var=(')
            if idx < 0:
                raise ValueError("Invalid response: no 'var=(' found")
            end = text.rfind(');')
            if end < 0:
                raise ValueError("Invalid response: no ');' found")
            json_str = text[idx + 5:end]
            data = json.loads(json_str)
            if not data:
                raise ValueError("Empty data returned")
            return data

        except (urllib.error.URLError, json.JSONDecodeError, ValueError) as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  [retry {attempt+1}/{retries}] {e}, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
            else:
                raise


# ============== 技术指标计算 ==============

def calc_ma(closes: List[float], period: int) -> List[Optional[float]]:
    """简单移动平均"""
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = sum(closes[i - period + 1:i + 1]) / period
    return result


def calc_ema(closes: List[float], period: int) -> List[Optional[float]]:
    """指数移动平均"""
    result = [None] * len(closes)
    if len(closes) < period:
        return result
    result[period - 1] = sum(closes[:period]) / period
    multiplier = 2 / (period + 1)
    for i in range(period, len(closes)):
        result[i] = (closes[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def calc_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, List[Optional[float]]]:
    """MACD 指标: DIF, DEA, MACD柱"""
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = [None] * len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif[i] = ema_fast[i] - ema_slow[i]
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


def calc_rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """RSI 指标"""
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


def calc_boll(closes: List[float], period: int = 20, std_dev: float = 2) -> Dict[str, List[Optional[float]]]:
    """布林带: UPPER, MIDDLE, LOWER"""
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


def calc_kdj(highs: List[float], lows: List[float], closes: List[float],
             n: int = 9, m1: int = 3, m2: int = 3) -> Dict[str, List[Optional[float]]]:
    """KDJ 随机指标"""
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


def analyze_stock(code: str, datalen: int = 1023, scale: str = '240', output_dir: str = None) -> FinanceData:
    """分析单只股票: 抓取 K 线 + 计算技术指标 + 生成信号"""
    print(f"\n{'='*60}")
    print(f"分析股票: {code} (scale={scale})")
    print(f"{'='*60}")

    # 1. 抓取 K 线
    print(f"[1/4] 抓取 K 线数据 (scale={scale}, datalen={datalen})...")
    raw = fetch_kline(code, scale=scale, datalen=datalen)
    print(f"  ✓ 获取 {len(raw)} 条 K 线")
    if raw:
        print(f"  ✓ 时间范围: {raw[0]['day']} ~ {raw[-1]['day']}")

    # 2. 数据预处理
    print("[2/4] 数据预处理...")
    kline = []
    for row in raw:
        kline.append({
            'date': row['day'],
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
            'volume': int(row['volume']),
        })
    closes = [k['close'] for k in kline]
    highs = [k['high'] for k in kline]
    lows = [k['low'] for k in kline]

    # 3. 计算技术指标
    print("[3/4] 计算技术指标...")
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
    print("  ✓ MA / EMA / MACD / RSI / BOLL / KDJ 计算完成")

    # 4. 生成信号
    print("[4/4] 生成交易信号...")
    signals = generate_signals(kline, indicators)
    for sig, desc in signals.items():
        print(f"  • {desc}")

    # 5. 提取最新指标值
    latest_indicators = {}
    for category, vals in indicators.items():
        if isinstance(vals, dict):
            for name, series in vals.items():
                if series and series[-1] is not None:
                    latest_indicators[f'{category}_{name}'] = round(series[-1], 4)
        elif isinstance(vals, list) and vals[-1] is not None:
            latest_indicators[category] = round(vals[-1], 4)

    # 6. 价格统计
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

    # 7. 组装结果
    result = FinanceData(
        source='sina',
        data_type='kline',
        symbol=code,
        timestamp=datetime.now().isoformat(),
        payload={
            'sina_symbol': to_sina_symbol(code),
            'kline_count': len(kline),
            'date_range': {
                'start': kline[0]['date'],
                'end': kline[-1]['date'],
            },
            'price_stats': price_stats,
            'latest_indicators': latest_indicators,
            'signals': signals,
            'kline_raw': kline,
        },
        meta={'scale': scale, 'datalen': datalen}
    )

    # 8. 保存结果
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        raw_path = os.path.join(output_dir, f'{code}_kline_raw_{ts}.json')
        with open(raw_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n  💾 结果已保存: {raw_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description='新浪财经 K 线抓取 + 技术指标分析')
    parser.add_argument('codes', nargs='+', help='股票代码列表 (如 603000 600000)')
    parser.add_argument('--datalen', type=int, default=1023, help='K 线数据条数 (默认 1023)')
    parser.add_argument('--scale', default='240', help='K 线周期: 240=日线, 60=60分钟, 30=30分钟, 15=15分钟, 5=5分钟, 1=1分钟 (默认 240)')
    parser.add_argument('--output-dir', default=None, help='输出目录')
    parser.add_argument('--delay', type=float, default=1.0, help='请求间隔秒数')
    args = parser.parse_args()

    output_dir = args.output_dir or os.path.join(os.path.dirname(__file__), '..', '..', 'temp', 'kline_results')

    results = []
    for i, code in enumerate(args.codes):
        if i > 0:
            time.sleep(args.delay)
        try:
            result = analyze_stock(code, datalen=args.datalen, scale=args.scale, output_dir=output_dir)
            results.append(result)
        except Exception as e:
            print(f"\n❌ {code} 分析失败: {e}")
            import traceback
            traceback.print_exc()

    # 保存汇总
    if results:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        summary_path = os.path.join(output_dir, f'kline_summary_{ts}.json')
        summary = []
        for r in results:
            summary.append({
                'symbol': r.symbol,
                'sina_symbol': r.payload['sina_symbol'],
                'kline_count': r.payload['kline_count'],
                'date_range': r.payload['date_range'],
                'price_stats': r.payload['price_stats'],
                'latest_indicators': r.payload['latest_indicators'],
                'signals': r.payload['signals'],
            })
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n{'='*60}")
        print(f"📊 汇总报告: {summary_path}")
        print(f"   成功分析 {len(results)}/{len(args.codes)} 只股票")
        print(f"{'='*60}")


if __name__ == '__main__':
    main()