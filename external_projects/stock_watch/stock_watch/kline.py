"""stock_watch/kline.py — K 线数据获取 + 绘图（功能 2）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import matplotlib
import matplotlib.pyplot as plt
from stock_watch.data_sources import DataSourceError, fetch_etf_kline, fetch_kline

# 设置中文字体，避免 K 线图中中文显示为方框
_matplotlib_font_configured = False


def _configure_chinese_font():
    """首次调用时配置 matplotlib 使用中文字体。"""
    global _matplotlib_font_configured
    if _matplotlib_font_configured:
        return
    _matplotlib_font_configured = True
    # Windows 优先用 SimHei，macOS 用 PingFang SC
    import platform
    system = platform.system()
    if system == "Windows":
        plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Noto Sans SC", "sans-serif"]
    elif system == "Darwin":
        plt.rcParams["font.sans-serif"] = ["PingFang SC", "Hei", "Noto Sans SC", "sans-serif"]
    else:
        plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "SimHei", "sans-serif"]
    # 解决负号显示问题
    plt.rcParams["axes.unicode_minus"] = False
    # 清除 font manager 缓存，确保新配置生效
    matplotlib.font_manager._load_fontmanager(try_read_cache=False)

logger = logging.getLogger("stock_watch.kline")

# akshare 返回的常见列名（中文），统一映射成 mplfinance 需要的英文列名
_COLUMN_MAP = {
    "日期": "Date",
    "开盘": "Open",
    "收盘": "Close",
    "最高": "High",
    "最低": "Low",
    "成交量": "Volume",
}


def _normalize_df(df):
    import pandas as pd

    df = df.rename(columns=_COLUMN_MAP)
    missing = [c for c in ("Date", "Open", "Close", "High", "Low", "Volume") if c not in df.columns]
    if missing:
        raise DataSourceError(f"K 线数据缺少必要列: {missing}，akshare 返回列名可能已变化")
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def get_kline_df(code: str, entry_type: str, days: int, adjust: str = "qfq"):
    """获取标准化后的 K 线 DataFrame（列：Open/High/Low/Close/Volume，索引：Date）。"""
    if entry_type == "etf":
        raw = fetch_etf_kline(code, days=days, adjust=adjust)
    else:
        raw = fetch_kline(code, market="", days=days, adjust=adjust)
    return _normalize_df(raw)


def plot_kline(
    code: str,
    name: str,
    entry_type: str,
    out_dir: Path,
    days: int = 120,
    adjust: str = "qfq",
) -> Optional[Path]:
    """生成并保存单个标的的 K 线图，返回图片路径；失败抛 DataSourceError。"""
    _configure_chinese_font()
    import mplfinance as mpf

    df = get_kline_df(code, entry_type, days=days, adjust=adjust)
    if df.empty:
        raise DataSourceError(f"{code} 没有可用的 K 线数据")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{code}_{name}.png".replace("/", "_")

    mpf.plot(
        df,
        type="candle",
        volume=True,
        style="charles",
        title=f"{name}({code})",
        savefig=dict(fname=str(out_path), dpi=150, bbox_inches="tight"),
    )
    logger.info("K 线图已生成: %s", out_path)
    return out_path
