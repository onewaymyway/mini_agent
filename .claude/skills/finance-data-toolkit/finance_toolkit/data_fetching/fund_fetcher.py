# -*- coding: utf-8 -*-
"""
基金数据抓取模块

支持:
- ETF实时行情 (fund_etf_spot_em)
- ETF历史K线 (fund_etf_hist_sina)
- LOF实时行情 (fund_lof_spot_em)
- 场外基金净值 (fund_open_fund_info_em)
- 基金持仓 (fund_portfolio_hold_em)
- 基金排行 (fund_open_fund_rank_em)
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

logger = logging.getLogger(__name__)


# ============== 数据模型 ==============

@dataclass
class ETFQuote:
    """ETF实时行情"""
    code: str
    name: str
    price: float
    change_pct: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int
    amount: float
    nav: float  # 净值
    premium: float  # 溢价率
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ETFKline:
    """ETF历史K线"""
    code: str
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: int
    amount: float
    change_pct: float
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class LOFQuote:
    """LOF实时行情"""
    code: str
    name: str
    price: float
    change_pct: float
    nav: float  # 净值
    premium: float  # 溢价率
    volume: int
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class FundNAV:
    """场外基金净值"""
    code: str
    name: str
    nav_date: str
    unit_nav: float  # 单位净值
    accum_nav: float  # 累计净值
    change_pct: float  # 日涨跌幅
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class FundHoldings:
    """基金持仓"""
    fund_code: str
    fund_name: str
    report_date: str
    stock_code: str
    stock_name: str
    hold_ratio: float  # 持仓占比
    hold_amount: float  # 持仓金额
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class FundRank:
    """基金排行"""
    rank: int
    fund_code: str
    fund_name: str
    fund_type: str
    nav: float
    change_1w: float  # 近1周涨跌
    change_1m: float  # 近1月涨跌
    change_3m: float  # 近3月涨跌
    change_6m: float  # 近6月涨跌
    change_1y: float  # 近1年涨跌
    change_ytd: float  # 今年以来涨跌
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ============== ETF数据抓取 ==============

def fetch_etf_quote(symbols: Optional[List[str]] = None) -> List[ETFQuote]:
    """
    获取ETF实时行情
    
    Args:
        symbols: ETF代码列表，如 ["510300", "159915"]，默认获取全部
    
    Returns:
        ETFQuote 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装，请执行: pip install akshare")
    
    try:
        df = ak.fund_etf_spot_em()
        if symbols:
            df = df[df["代码"].isin(symbols)]
        
        results = []
        for _, row in df.iterrows():
            try:
                price = float(row.get("最新价", 0) or 0)
                prev_close = float(row.get("昨收", 0) or 0)
                change_pct = float(row.get("涨跌幅", 0) or 0)
                nav = float(row.get("最新净值", 0) or 0)
                
                # 计算溢价率
                premium = 0.0
                if price > 0 and nav > 0:
                    premium = (price - nav) / nav * 100
                
                results.append(ETFQuote(
                    code=str(row.get("代码", "")),
                    name=str(row.get("名称", "")),
                    price=price,
                    change_pct=change_pct,
                    open=float(row.get("今开", 0) or 0),
                    high=float(row.get("最高", 0) or 0),
                    low=float(row.get("最低", 0) or 0),
                    prev_close=prev_close,
                    volume=int(row.get("成交量", 0) or 0),
                    amount=float(row.get("成交额", 0) or 0),
                    nav=nav,
                    premium=premium,
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"ETF行情获取失败: {e}")
        return []


def fetch_etf_kline(
    symbol: str,
    start_date: str = "20240101",
    end_date: Optional[str] = None,
) -> List[ETFKline]:
    """
    获取ETF历史K线

    Args:
        symbol: ETF代码，如 "510300"（不带前缀）
        start_date: 开始日期，格式 "YYYYMMDD"
        end_date: 结束日期，格式 "YYYYMMDD"，默认为今天

    Returns:
        ETFKline 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")

    try:
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")

        # fund_etf_hist_sina 需要带交易所前缀
        symbol_input = symbol
        if not symbol.startswith("sh") and not symbol.startswith("sz"):
            # 根据代码判断交易所
            if symbol.startswith(("5", "1")):
                symbol_input = f"sh{symbol}"
            else:
                symbol_input = f"sz{symbol}"

        df = ak.fund_etf_hist_sina(symbol=symbol_input)
        
        results = []
        for _, row in df.iterrows():
            try:
                close = float(row.get("收盘", 0) or 0)
                prev_close = float(row.get("昨收", 0) or 0)
                change_pct = 0.0
                if prev_close > 0:
                    change_pct = (close - prev_close) / prev_close * 100
                
                results.append(ETFKline(
                    code=symbol,
                    date=str(row.get("日期", "")),
                    open=float(row.get("开盘", 0) or 0),
                    close=close,
                    high=float(row.get("最高", 0) or 0),
                    low=float(row.get("最低", 0) or 0),
                    volume=int(row.get("成交量", 0) or 0),
                    amount=float(row.get("成交额", 0) or 0),
                    change_pct=change_pct,
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"ETF K线获取失败: {e}")
        return []


# ============== LOF数据抓取 ==============

def fetch_lof_quote(symbols: Optional[List[str]] = None) -> List[LOFQuote]:
    """
    获取LOF实时行情
    
    Args:
        symbols: LOF代码列表
    
    Returns:
        LOFQuote 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    
    try:
        df = ak.fund_lof_spot_em()
        if symbols:
            df = df[df["代码"].isin(symbols)]
        
        results = []
        for _, row in df.iterrows():
            try:
                price = float(row.get("最新价", 0) or 0)
                nav = float(row.get("最新净值", 0) or 0)
                
                premium = 0.0
                if price > 0 and nav > 0:
                    premium = (price - nav) / nav * 100
                
                results.append(LOFQuote(
                    code=str(row.get("代码", "")),
                    name=str(row.get("名称", "")),
                    price=price,
                    change_pct=float(row.get("涨跌幅", 0) or 0),
                    nav=nav,
                    premium=premium,
                    volume=int(row.get("成交量", 0) or 0),
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"LOF行情获取失败: {e}")
        return []


# ============== 场外基金数据抓取 ==============

def fetch_fund_nav(
    fund_code: str,
    start_date: str = "20240101",
    end_date: Optional[str] = None
) -> List[FundNAV]:
    """
    获取场外基金净值数据
    
    Args:
        fund_code: 基金代码，如 "110011"
        start_date: 开始日期，格式 "YYYYMMDD"
        end_date: 结束日期，格式 "YYYYMMDD"
    
    Returns:
        FundNAV 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    
    try:
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        
        results = []
        for _, row in df.iterrows():
            try:
                unit_nav = float(row.get("单位净值", 0) or 0)
                accum_nav = float(row.get("累计净值", 0) or 0)
                
                results.append(FundNAV(
                    code=fund_code,
                    name=str(row.get("基金简称", "")),
                    nav_date=str(row.get("净值日期", "")),
                    unit_nav=unit_nav,
                    accum_nav=accum_nav,
                    change_pct=float(row.get("日增长率", 0) or 0),
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"基金净值获取失败: {e}")
        return []


def fetch_fund_nav_history(fund_code: str) -> List[FundNAV]:
    """
    获取基金历史净值（另一种接口）
    
    Args:
        fund_code: 基金代码
    
    Returns:
        FundNAV 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        
        results = []
        for _, row in df.iterrows():
            try:
                results.append(FundNAV(
                    code=fund_code,
                    name=str(row.get("基金简称", "")),
                    nav_date=str(row.get("净值日期", "")),
                    unit_nav=float(row.get("单位净值", 0) or 0),
                    accum_nav=float(row.get("累计净值", 0) or 0),
                    change_pct=float(row.get("日增长率", 0) or 0),
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"基金历史净值获取失败: {e}")
        return []


# ============== 基金持仓数据抓取 ==============

def fetch_fund_holdings(fund_code: str, report_date: Optional[str] = None) -> List[FundHoldings]:
    """
    获取基金持仓数据
    
    Args:
        fund_code: 基金代码
        report_date: 报告期，如 "2024-03-31"，默认最新
    
    Returns:
        FundHoldings 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    
    try:
        df = ak.fund_portfolio_hold_em(symbol=fund_code, date=report_date)
        
        results = []
        for _, row in df.iterrows():
            try:
                results.append(FundHoldings(
                    fund_code=fund_code,
                    fund_name=str(row.get("基金名称", "")),
                    report_date=str(row.get("报告期", "")),
                    stock_code=str(row.get("股票代码", "")),
                    stock_name=str(row.get("股票名称", "")),
                    hold_ratio=float(row.get("持仓占比", 0) or 0),
                    hold_amount=float(row.get("持仓金额", 0) or 0),
                ))
            except (ValueError, TypeError):
                continue
        
        return results
    except Exception as e:
        logger.error(f"基金持仓获取失败: {e}")
        return []


# ============== 基金排行数据抓取 ==============

def fetch_fund_rank(fund_type: str = "全部") -> List[FundRank]:
    """
    获取基金排行数据

    Args:
        fund_type: 基金类型，如 "全部"/"股票型"/"债券型"/"混合型"/"指数型"

    Returns:
        FundRank 列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")

    try:
        df = ak.fund_open_fund_rank_em(symbol=fund_type)

        results = []
        for idx, (_, row) in enumerate(df.iterrows(), 1):
            try:
                results.append(FundRank(
                    rank=idx,
                    fund_code=str(row.get("基金代码", "")),
                    fund_name=str(row.get("基金简称", "")),
                    fund_type=str(row.get("基金类型", "")),
                    nav=float(row.get("单位净值", 0) or 0),
                    change_1w=float(row.get("近1周", 0) or 0),
                    change_1m=float(row.get("近1月", 0) or 0),
                    change_3m=float(row.get("近3月", 0) or 0),
                    change_6m=float(row.get("近6月", 0) or 0),
                    change_1y=float(row.get("近1年", 0) or 0),
                    change_ytd=float(row.get("今年来", 0) or 0),
                ))
            except (ValueError, TypeError):
                continue

        return results
    except Exception as e:
        logger.error(f"基金排行获取失败: {e}")
        return []


# ============== 基金列表 ==============

def fetch_fund_list(fund_type: str = "全部") -> List[Dict[str, Any]]:
    """
    获取基金列表
    
    Args:
        fund_type: 基金类型，如 "全部"/"股票型"/"债券型"/"混合型"/"指数型"
    
    Returns:
        基金列表
    """
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    
    try:
        df = ak.fund_name_em()
        
        if fund_type != "全部":
            df = df[df["基金类型"].str.contains(fund_type, na=False)]
        
        results = []
        for _, row in df.iterrows():
            results.append({
                "code": str(row.get("基金代码", "")),
                "name": str(row.get("基金简称", "")),
                "type": str(row.get("基金类型", "")),
                "company": str(row.get("基金公司", "")),
            })
        
        return results
    except Exception as e:
        logger.error(f"基金列表获取失败: {e}")
        return []


# ============== 便捷入口 ==============

class FundDataFetcher:
    """基金数据抓取器"""
    
    # === ETF ===
    def get_etf_quote(self, symbols: Optional[List[str]] = None) -> List[ETFQuote]:
        return fetch_etf_quote(symbols)
    
    def get_etf_kline(
        self,
        symbol: str,
        start_date: str = "20240101",
        end_date: Optional[str] = None
    ) -> List[ETFKline]:
        return fetch_etf_kline(symbol, start_date, end_date)
    
    # === LOF ===
    def get_lof_quote(self, symbols: Optional[List[str]] = None) -> List[LOFQuote]:
        return fetch_lof_quote(symbols)
    
    # === 场外基金 ===
    def get_fund_nav(self, fund_code: str, start_date: str = "20240101", end_date: Optional[str] = None) -> List[FundNAV]:
        return fetch_fund_nav(fund_code, start_date, end_date)
    
    def get_fund_nav_history(self, fund_code: str) -> List[FundNAV]:
        return fetch_fund_nav_history(fund_code)
    
    def get_fund_holdings(self, fund_code: str, report_date: Optional[str] = None) -> List[FundHoldings]:
        return fetch_fund_holdings(fund_code, report_date)
    
    def get_fund_rank(self, fund_type: str = "全部") -> List[FundRank]:
        return fetch_fund_rank(fund_type)
    
    def get_fund_list(self, fund_type: str = "全部") -> List[Dict[str, Any]]:
        return fetch_fund_list(fund_type)
    
    # === 综合 ===
    def get_fund_summary(self, fund_code: str) -> Dict[str, Any]:
        """获取基金综合信息"""
        return {
            "nav": self.get_fund_nav_history(fund_code),
            "holdings": self.get_fund_holdings(fund_code),
        }


fetcher = FundDataFetcher()


__all__ = [
    # 数据模型
    "ETFQuote", "ETFKline", "LOFQuote", "FundNAV", "FundHoldings", "FundRank",
    # 抓取函数
    "fetch_etf_quote", "fetch_etf_kline",
    "fetch_lof_quote",
    "fetch_fund_nav", "fetch_fund_nav_history",
    "fetch_fund_holdings", "fetch_fund_rank", "fetch_fund_list",
    # 便捷类
    "FundDataFetcher", "fetcher",
]
