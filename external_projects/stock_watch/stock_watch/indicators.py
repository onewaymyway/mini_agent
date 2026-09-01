"""stock_watch/indicators.py — 历史行情技术指标信号。

输入 `data_sources.fetch_kline()`/`fetch_etf_kline()` 已经在用的
日K DataFrame（列名遵循 akshare 中文习惯：'收盘'/'成交量'/'最高'/'最低'），
输出若干 `Signal`。用 pandas 手写指标，不引入 talib（安装门槛高，且
这几个指标用 pandas 几行就能写清楚，没必要为此新增强依赖）。

信号列表（6 类，覆盖"趋势/动量/波动率/量能"四个维度）：
  - MA 金叉/死叉：短期均线上穿/下穿长期均线，趋势反转的经典信号。
  - MACD 金叉/死叉：DIF 与 DEA 交叉，趋势强弱切换信号。
  - RSI 超买/超卖：14日 RSI 突破 70/跌破 30，动量极端信号。
  - KDJ 超买/超卖：K线/D线交叉配合 J 线极值，短期超买超卖判断。
  - 放量突破：当日成交量相对近 N 日均量的倍数，量能异动的直接体现。
  - 布林带压缩突破：布林带宽度处于近期低位（横盘蓄势）后价格突破上下轨。

数据不足（K线条数不够计算某个窗口）时该信号直接跳过，不报错、不产出
占位信号——这是"数据不够，暂时没有这个维度的判断"，不是失败。
"""

from __future__ import annotations

from typing import List

from stock_watch.signals import Signal

_CLOSE_COL_CANDIDATES = ("收盘", "close")
_VOLUME_COL_CANDIDATES = ("成交量", "volume")
_HIGH_COL_CANDIDATES = ("最高", "high")
_LOW_COL_CANDIDATES = ("最低", "low")


def _col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


def compute_price_signals(
    df,
    *,
    ma_short: int = 5,
    ma_long: int = 20,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    rsi_window: int = 14,
    kdj_n: int = 9,
    kdj_m1: int = 3,
    kdj_m2: int = 3,
    volume_window: int = 20,
    boll_window: int = 20,
) -> List[Signal]:
    """给定日K DataFrame（按日期升序排列，最后一行是最新交易日），返回
    技术指标信号列表。DataFrame 结构不符合预期（缺列/行数太少）时对应
    信号跳过，不影响其它信号照常计算。
    """
    signals: List[Signal] = []
    close_col = _col(df, _CLOSE_COL_CANDIDATES)
    if close_col is None or df.empty:
        return signals

    # ── 趋势维度 ────────────────────────────────────────────────────────
    signals.extend(_ma_cross_signal(df, close_col, ma_short, ma_long))
    signals.extend(
        _macd_signal(df, close_col, macd_fast, macd_slow, macd_signal)
    )

    # ── 动量维度 ────────────────────────────────────────────────────────
    signals.extend(_rsi_signal(df, close_col, rsi_window))
    signals.extend(
        _kdj_signal(
            df,
            close_col,
            kdj_n,
            kdj_m1,
            kdj_m2,
        )
    )

    # ── 量能维度 ────────────────────────────────────────────────────────
    signals.extend(_volume_spike_signal(df, close_col, volume_window))

    # ── 波动率维度 ──────────────────────────────────────────────────────
    signals.extend(
        _bollinger_breakout_signal(df, close_col, boll_window)
    )

    return signals


# ───────────────────────────────────────────────────────────────────────────
# 1. MA 金叉/死叉（原有）
# ───────────────────────────────────────────────────────────────────────────

def _ma_cross_signal(df, close_col: str, short: int, long: int) -> List[Signal]:
    if len(df) < long + 1:
        return []
    close = df[close_col].astype(float)
    ma_short = close.rolling(short).mean()
    ma_long = close.rolling(long).mean()
    if ma_short.iloc[-2:].isna().any() or ma_long.iloc[-2:].isna().any():
        return []

    prev_diff = ma_short.iloc[-2] - ma_long.iloc[-2]
    curr_diff = ma_short.iloc[-1] - ma_long.iloc[-1]

    if prev_diff <= 0 < curr_diff:
        return [
            Signal(
                name="ma_golden_cross",
                category="price",
                score=8.0,
                reason=f"MA{short}上穿MA{long}（金叉），短期趋势转强",
            )
        ]
    if prev_diff >= 0 > curr_diff:
        return [
            Signal(
                name="ma_death_cross",
                category="price",
                score=-8.0,
                reason=f"MA{short}下穿MA{long}（死叉），短期趋势转弱",
            )
        ]
    return []


# ───────────────────────────────────────────────────────────────────────────
# 2. MACD 金叉/死叉
# ───────────────────────────────────────────────────────────────────────────

def _macd_signal(
    df,
    close_col: str,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> List[Signal]:
    """MACD（指数移动平均线）：
      DIF = EMA(close, fast) - EMA(close, slow)
      DEA = EMA(DIF, signal_period)
      MACD柱 = (DIF - DEA) * 2
    金叉（DIF 上穿 DEA）+ MACD柱转正 → 看涨；反之看跌。
    """
    if len(df) < slow + signal_period + 10:
        return []
    close = df[close_col].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal_period, adjust=False).mean()

    if dif.iloc[-2:].isna().any() or dea.iloc[-2:].isna().any():
        return []

    prev_diff = dif.iloc[-2] - dea.iloc[-2]
    curr_diff = dif.iloc[-1] - dea.iloc[-1]

    if prev_diff <= 0 < curr_diff:
        return [
            Signal(
                name="macd_golden_cross",
                category="price",
                score=7.0,
                reason=f"MACD DIF 上穿 DEA（金叉），趋势转强",
            )
        ]
    if prev_diff >= 0 > curr_diff:
        return [
            Signal(
                name="macd_death_cross",
                category="price",
                score=-7.0,
                reason=f"MACD DIF 下穿 DEA（死叉），趋势转弱",
            )
        ]
    return []


# ───────────────────────────────────────────────────────────────────────────
# 3. RSI 超买/超卖
# ───────────────────────────────────────────────────────────────────────────

def _rsi_signal(
    df,
    close_col: str,
    window: int = 14,
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> List[Signal]:
    """RSI（相对强弱指数）：
      RSI = 100 - 100/(1 + RS)
      RS = 近 window 天平均涨幅 / 平均跌幅
    RSI > overbought → 超买（可能回调）；RSI < oversold → 超卖（可能反弹）。
    """
    if len(df) < window + 1:
        return []
    close = df[close_col].astype(float)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window, min_periods=window).mean()
    avg_loss = loss.rolling(window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100.0 - 100.0 / (1.0 + rs)

    if rsi.iloc[-1] is None or rsi.iloc[-2] is None:
        return []

    last_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2]

    # 当前状态信号
    if last_rsi > overbought:
        return [
            Signal(
                name="rsi_overbought",
                category="price",
                score=-5.0,
                reason=f"RSI({window})={last_rsi:.1f}，超买区域，短期可能回调",
            )
        ]
    if last_rsi < oversold:
        return [
            Signal(
                name="rsi_oversold",
                category="price",
                score=5.0,
                reason=f"RSI({window})={last_rsi:.1f}，超卖区域，短期可能反弹",
            )
        ]

    # 穿越信号（从超买区回到正常，或从超卖区回到正常）
    if prev_rsi > overbought and last_rsi <= overbought:
        return [
            Signal(
                name="rsi_exit_overbought",
                category="price",
                score=4.0,
                reason=f"RSI({window})从超买区回落至{last_rsi:.1f}，抛压缓解",
            )
        ]
    if prev_rsi < oversold and last_rsi >= oversold:
        return [
            Signal(
                name="rsi_exit_oversold",
                category="price",
                score=4.0,
                reason=f"RSI({window})从超卖区回升至{last_rsi:.1f}，买盘回暖",
            )
        ]

    return []


# ───────────────────────────────────────────────────────────────────────────
# 4. KDJ 超买/超卖
# ───────────────────────────────────────────────────────────────────────────

def _kdj_signal(
    df,
    close_col: str,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> List[Signal]:
    """KDJ（随机指标）：
      RSV = (CLOSE - LN(n)) / (HN(n) - LN(n)) * 100
      K = SMA(RSV, m1)
      D = SMA(K, m2)
      J = 3*K - 2*D
    KDJ 在 80 以上为超买区，20 以下为超卖区；K线上穿D线为金叉，反之为死叉。
    """
    if len(df) < n + 1:
        return []
    close = df[close_col].astype(float)
    high_col = _col(df, _HIGH_COL_CANDIDATES)
    low_col = _col(df, _LOW_COL_CANDIDATES)
    if high_col is None or low_col is None:
        return []
    high = df[high_col].astype(float)
    low = df[low_col].astype(float)

    lowest_low = low.rolling(n).min()
    highest_high = high.rolling(n).max()
    rsv = ((close - lowest_low) / (highest_high - lowest_low) * 100).replace(
        [float("inf"), float("-inf")], float("nan")
    )
    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d

    if k.iloc[-2:].isna().any() or d.iloc[-2:].isna().any():
        return []

    last_k, last_d, last_j = k.iloc[-1], d.iloc[-1], j.iloc[-1]
    prev_k, prev_d, prev_j = k.iloc[-2], d.iloc[-2], j.iloc[-2]
    signals: List[Signal] = []

    # 金叉
    if prev_k <= prev_d < last_k and last_k > last_d:
        if last_j < 80:
            signals.append(
                Signal(
                    name="kdj_golden_cross",
                    category="price",
                    score=6.0,
                    reason=f"KDJ金叉 K={last_k:.1f}>D={last_d:.1f}，J={last_j:.1f}未超买，趋势转强",
                )
            )
        else:
            signals.append(
                Signal(
                    name="kdj_golden_cross_weak",
                    category="price",
                    score=3.0,
                    reason=f"KDJ金叉但J={last_j:.1f}>80超买区，信号偏弱",
                )
            )

    # 死叉
    if prev_k >= prev_d > last_k and last_k < last_d:
        if last_j > 20:
            signals.append(
                Signal(
                    name="kdj_death_cross",
                    category="price",
                    score=-6.0,
                    reason=f"KDJ死叉 K={last_k:.1f}<D={last_d:.1f}，J={last_j:.1f}未超卖，趋势转弱",
                )
            )
        else:
            signals.append(
                Signal(
                    name="kdj_death_cross_weak",
                    category="price",
                    score=-3.0,
                    reason=f"KDJ死叉但J={last_j:.1f}<20超卖区，信号偏弱",
                )
            )

    # 极端值提示（单独信号，不影响交叉信号）
    if last_j > 100:
        signals.append(
            Signal(
                name="kdj_extreme_overbought",
                category="price",
                score=-4.0,
                reason=f"KDJ J值={last_j:.1f}>100，极端超买，短期风险极高",
            )
        )
    elif last_j < 0:
        signals.append(
            Signal(
                name="kdj_extreme_oversold",
                category="price",
                score=4.0,
                reason=f"KDJ J值={last_j:.1f}<0，极端超卖，短期可能有反弹",
            )
        )

    return signals


# ───────────────────────────────────────────────────────────────────────────
# 5. 放量突破（原有）
# ───────────────────────────────────────────────────────────────────────────

def _volume_spike_signal(
    df,
    close_col: str,
    window: int = 20,
) -> List[Signal]:
    vol_col = _col(df, _VOLUME_COL_CANDIDATES)
    if vol_col is None or len(df) < window + 1:
        return []
    volume = df[vol_col].astype(float)
    avg_vol = volume.iloc[-(window + 1):-1].mean()
    if avg_vol <= 0:
        return []
    ratio = volume.iloc[-1] / avg_vol
    close = df[close_col].astype(float)
    price_change = close.iloc[-1] - close.iloc[-2] if len(close) >= 2 else 0.0

    if ratio >= 2.0:
        direction = "放量上涨" if price_change > 0 else "放量下跌"
        score = 6.0 if price_change > 0 else -6.0
        return [
            Signal(
                name="volume_spike",
                category="price",
                score=score,
                reason=f"{direction}，成交量为近{window}日均量的{ratio:.1f}倍",
            )
        ]
    return []


# ───────────────────────────────────────────────────────────────────────────
# 6. 布林带压缩突破（原有）
# ───────────────────────────────────────────────────────────────────────────

def _bollinger_breakout_signal(
    df,
    close_col: str,
    window: int = 20,
) -> List[Signal]:
    if len(df) < window + 5:
        return []
    close = df[close_col].astype(float)
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    width = (upper - lower) / mid

    if width.iloc[-6:-1].isna().any() or width.iloc[-1] is None:
        return []
    recent_width = width.dropna()
    if recent_width.empty:
        return []
    was_narrow = width.iloc[-2] <= recent_width.quantile(0.25)
    last_close = close.iloc[-1]

    if was_narrow and last_close > upper.iloc[-2]:
        return [
            Signal(
                name="bollinger_squeeze_breakout_up",
                category="price",
                score=7.0,
                reason="波动率压缩后向上突破布林带上轨，横盘蓄势后选择方向",
            )
        ]
    if was_narrow and last_close < lower.iloc[-2]:
        return [
            Signal(
                name="bollinger_squeeze_breakout_down",
                category="price",
                score=-7.0,
                reason="波动率压缩后向下突破布林带下轨，横盘蓄势后选择方向",
            )
        ]
    return []


# ───────────────────────────────────────────────────────────────────────────
# 综合评分辅助函数
# ───────────────────────────────────────────────────────────────────────────

def score_signals(signals: List[Signal]) -> float:
    """对信号列表求和，得到综合技术评分（越高越看涨）。"""
    return sum(s.score for s in signals)


def top_signals(signals: List[Signal], n: int = 3) -> List[Signal]:
    """按 |score| 降序取前 N 条最有意义的信号。"""
    return sorted(signals, key=lambda s: abs(s.score), reverse=True)[:n]
