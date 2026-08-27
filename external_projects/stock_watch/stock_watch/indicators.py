"""stock_watch/indicators.py — 历史行情技术指标信号（阶段3a）。

对应 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段
3a：输入 `data_sources.fetch_kline()`/`fetch_etf_kline()` 已经在用的
日K DataFrame（列名遵循 akshare 中文习惯：'收盘'/'成交量'/'最高'/'最低'），
输出若干 `Signal`。用 pandas 手写指标，不引入 talib（安装门槛高，且
这几个指标用 pandas 几行就能写清楚，没必要为此新增强依赖）。

三个信号，覆盖"趋势/量能/波动率"三个维度，均是可解释的经典技术分析
概念，不是黑盒模型：
  - MA 金叉/死叉：短期均线上穿/下穿长期均线，趋势反转的经典信号。
  - 放量突破：当日成交量相对近 N 日均量的倍数，量能异动的直接体现。
  - 波动率压缩后突破：布林带宽度处于近期低位（横盘蓄势）后价格向上/
    向下突破布林带，捕捉"横盘后选择方向"这类形态。

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
    df, *, ma_short: int = 5, ma_long: int = 20, volume_window: int = 20,
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

    signals.extend(_ma_cross_signal(df, close_col, ma_short, ma_long))
    signals.extend(_volume_spike_signal(df, close_col, volume_window))
    signals.extend(_bollinger_breakout_signal(df, close_col, boll_window))
    return signals


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


def _volume_spike_signal(df, close_col: str, window: int) -> List[Signal]:
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


def _bollinger_breakout_signal(df, close_col: str, window: int) -> List[Signal]:
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
    # 布林带宽度处于近期（近20个窗口，若不足则用现有全部）低位，
    # 视为"横盘蓄势"；随后价格突破上/下轨，视为"选择方向"。
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
