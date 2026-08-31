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
    import platform
    import os
    system = platform.system()
    # Windows 优先使用微软雅黑（路径直指定，规避字体缓存问题）
    if system == "Windows":
        yahei_path = r"C:\Windows\Fonts\msyh.ttc"
        if os.path.exists(yahei_path):
            prop = matplotlib.font_manager.FontProperties(fname=yahei_path)
            plt.rcParams["font.family"] = prop.get_name()
        else:
            plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans SC", "sans-serif"]
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

# CDP 返回的列名（英文）
_CDP_COLUMN_MAP = {
    "date": "Date",
    "open": "Open",
    "close": "Close",
    "high": "High",
    "low": "Low",
    "volume": "Volume",
}


def _normalize_df(df):
    import pandas as pd

    # 检测列名语言，选择对应的映射表
    if "date" in df.columns:
        col_map = _CDP_COLUMN_MAP
    else:
        col_map = _COLUMN_MAP

    df = df.rename(columns=col_map)
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
        # A 股需要提供 market，这里根据代码前缀判断
        market = "sh" if code.startswith(("6", "51")) else "sz"
        raw = fetch_kline(code, market=market, days=days, adjust=adjust)
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
    from matplotlib import font_manager

    df = get_kline_df(code, entry_type, days=days, adjust=adjust)
    if df.empty:
        raise DataSourceError(f"{code} 没有可用的 K 线数据")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{code}_{name}.png"

    # 使用微软雅黑字体显示中文标题
    import os
    yahei_path = r"C:\Windows\Fonts\msyh.ttc"
    fp = font_manager.FontProperties(fname=yahei_path) if os.path.exists(yahei_path) else None

    result = mpf.plot(
        df,
        type="candle",
        volume=True,
        style="charles",
        title="",  # 不设置标题，手动添加
        returnfig=True,
    )
    fig, axes = result

    # 用指定字体添加中文标题
    if fp:
        axes[0].set_title(f"{name} ({code})", fontsize=14, fontproperties=fp)
    else:
        axes[0].set_title(f"{name} ({code})", fontsize=14)

    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    logger.info("K 线图已生成: %s", out_path)
    return out_path
