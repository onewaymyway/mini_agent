# -*- coding: utf-8 -*-
"""
A股数据抓取模块 - 扩展版本

支持的数据类型:
- 实时行情 (stock_zh_a_spot_em)
- 历史K线 (stock_zh_a_hist)
- 财务数据 (stock_financial_report_sina)
- 分红数据 (stock_fhps_detail_em)
- 龙虎榜 (stock_lhb_detail_em)
- 北向资金 (stock_hsgt_fund_flow_summary_em)
- 股票基本信息 (stock_zh_a_spot_em)

多市场支持:
- A股 (sh/sz)
- 港股 (hk)
- 美股 (us)
"""

import logging
from datetime import datetime, timedelta
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
class StockKline:
    """A股历史K线数据"""
    symbol: str
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: int
    amount: float
    amplitude: float
    change_pct: float
    change_amount: float
    turnover_rate: float
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class FinancialReport:
    """财务数据"""
    symbol: str
    report_date: str
    report_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class Dividend:
    """分红数据"""
    symbol: str
    report_date: str
    dividend_per_share: float
    dividend_plan: str
    ex_dividend_date: str = ""
    record_date: str = ""
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class LHBRecord:
    """龙虎榜数据"""
    trade_date: str
    symbol: str
    name: str
    explanation: str
    buy_amount: float
    sell_amount: float
    net_buy: float
    buy_seat: str
    sell_seat: str
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class NorthboundFlow:
    """北向资金流向"""
    date: str
    sh_net_inflow: float
    sz_net_inflow: float
    total_net_inflow: float
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class StockQuote:
    """实时行情数据"""
    symbol: str
    name: str
    price: float
    change_pct: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int
    amount: float
    turnover_rate: float
    pe_ratio: float
    pb_ratio: float
    total_market_cap: float
    float_market_cap: float
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class StockBasic:
    """股票基本信息"""
    symbol: str
    name: str
    market: str
    industry: str
    list_date: str
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class HKStockQuote:
    """港股实时行情"""
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    market_cap: float
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class USStockQuote:
    """美股实时行情"""
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    market_cap: float
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ============== 核心抓取函数 ==============

def fetch_kline(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    period: str = "daily",
    adjust: str = "qfq"
) -> List[StockKline]:
    """获取A股历史K线数据"""
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(symbol=symbol, period=period, start_date=start_date, end_date=end_date, adjust=adjust)
    results = []
    for _, row in df.iterrows():
        results.append(StockKline(
            symbol=symbol, date=str(row.get("日期", "")),
            open=float(row.get("开盘", 0) or 0), close=float(row.get("收盘", 0) or 0),
            high=float(row.get("最高", 0) or 0), low=float(row.get("最低", 0) or 0),
            volume=int(row.get("成交量", 0) or 0), amount=float(row.get("成交额", 0) or 0),
            amplitude=float(row.get("振幅", 0) or 0), change_pct=float(row.get("涨跌幅", 0) or 0),
            change_amount=float(row.get("涨跌额", 0) or 0), turnover_rate=float(row.get("换手率", 0) or 0),
        ))
    return results


def fetch_financial(symbol: str, report_type: str = "资产负债表") -> List[FinancialReport]:
    """获取A股财务数据"""
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    df = ak.stock_financial_report_sina(stock=symbol, symbol=report_type)
    results = []
    for _, row in df.iterrows():
        results.append(FinancialReport(
            symbol=symbol, report_date=str(row.get("报告期", "")),
            report_type=report_type, data={k: v for k, v in row.items() if k != "报告期"},
        ))
    return results


def fetch_dividend(symbol: str) -> List[Dividend]:
    """获取A股分红数据"""
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    df = ak.stock_fhps_detail_em(symbol=symbol)
    results = []
    for _, row in df.iterrows():
        results.append(Dividend(
            symbol=symbol, report_date=str(row.get("报告期", "")),
            dividend_per_share=float(row.get("每股分红", 0) or 0),
            dividend_plan=str(row.get("分红方案", "")),
            ex_dividend_date=str(row.get("除权除息日", "")),
            record_date=str(row.get("股权登记日", "")),
        ))
    return results


def fetch_lhb(start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[LHBRecord]:
    """获取龙虎榜数据"""
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    df = ak.stock_lhb_detail_em(start_date=start_date, end_date=end_date)
    results = []
    for _, row in df.iterrows():
        results.append(LHBRecord(
            trade_date=str(row.get("龙虎榜日期", "")), symbol=str(row.get("代码", "")),
            name=str(row.get("名称", "")), explanation=str(row.get("解释说明", "")),
            buy_amount=float(row.get("买入金额", 0) or 0), sell_amount=float(row.get("卖出金额", 0) or 0),
            net_buy=float(row.get("净买入", 0) or 0), buy_seat=str(row.get("买入营业部", "")),
            sell_seat=str(row.get("卖出营业部", "")),
        ))
    return results


def fetch_northbound() -> List[NorthboundFlow]:
    """获取北向资金流向数据"""
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    df = ak.stock_hsgt_fund_flow_summary_em()
    results = []
    for _, row in df.iterrows():
        results.append(NorthboundFlow(
            date=str(row.get("日期", "")),
            sh_net_inflow=float(row.get("沪股通净流入", 0) or 0),
            sz_net_inflow=float(row.get("深股通净流入", 0) or 0),
            total_net_inflow=float(row.get("北向资金净流入", 0) or 0),
        ))
    return results


def fetch_realtime_quote(symbols: Optional[List[str]] = None) -> List[StockQuote]:
    """获取A股实时行情"""
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    try:
        df = ak.stock_zh_a_spot_em()
        if symbols:
            df = df[df["代码"].isin(symbols)]
        results = []
        for _, row in df.iterrows():
            results.append(StockQuote(
                symbol=str(row.get("代码", "")), name=str(row.get("名称", "")),
                price=float(row.get("最新价", 0) or 0), change_pct=float(row.get("涨跌幅", 0) or 0),
                open=float(row.get("今开", 0) or 0), high=float(row.get("最高", 0) or 0),
                low=float(row.get("最低", 0) or 0), prev_close=float(row.get("昨收", 0) or 0),
                volume=int(row.get("成交量", 0) or 0), amount=float(row.get("成交额", 0) or 0),
                turnover_rate=float(row.get("换手率", 0) or 0),
                pe_ratio=float(row.get("市盈率-动态", 0) or 0), pb_ratio=float(row.get("市净率", 0) or 0),
                total_market_cap=float(row.get("总市值", 0) or 0),
                float_market_cap=float(row.get("流通市值", 0) or 0),
            ))
        return results
    except Exception as e:
        logger.error(f"实时行情获取失败: {e}")
        return []


def fetch_stock_basic(symbols: Optional[List[str]] = None) -> List[StockBasic]:
    """获取A股基本信息"""
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    try:
        df = ak.stock_zh_a_spot_em()
        if symbols:
            df = df[df["代码"].isin(symbols)]
        results = []
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            results.append(StockBasic(
                symbol=code, name=str(row.get("名称", "")),
                market="SH" if code.startswith("6") else "SZ",
                industry=str(row.get("行业", "")), list_date=str(row.get("上市日期", "")),
            ))
        return results
    except Exception as e:
        logger.error(f"股票基本信息获取失败: {e}")
        return []


def fetch_hk_quote(symbols: Optional[List[str]] = None) -> List[HKStockQuote]:
    """获取港股实时行情"""
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    try:
        df = ak.stock_hk_spot_em()
        if symbols:
            df = df[df["代码"].isin(symbols)]
        results = []
        for _, row in df.iterrows():
            results.append(HKStockQuote(
                symbol=str(row.get("代码", "")), name=str(row.get("名称", "")),
                price=float(row.get("最新价", 0) or 0), change_pct=float(row.get("涨跌幅", 0) or 0),
                volume=int(row.get("成交量", 0) or 0), market_cap=float(row.get("总市值", 0) or 0),
            ))
        return results
    except Exception as e:
        logger.error(f"港股行情获取失败: {e}")
        return []


def fetch_us_quote(symbols: Optional[List[str]] = None) -> List[USStockQuote]:
    """获取美股实时行情"""
    if not HAS_AKSHARE:
        raise ImportError("akshare 未安装")
    try:
        df = ak.stock_us_spot_em()
        if symbols:
            df = df[df["代码"].isin(symbols)]
        results = []
        for _, row in df.iterrows():
            results.append(USStockQuote(
                symbol=str(row.get("代码", "")), name=str(row.get("名称", "")),
                price=float(row.get("最新价", 0) or 0), change_pct=float(row.get("涨跌幅", 0) or 0),
                volume=int(row.get("成交量", 0) or 0), market_cap=float(row.get("总市值", 0) or 0),
            ))
        return results
    except Exception as e:
        logger.error(f"美股行情获取失败: {e}")
        return []


# ============== 便捷入口 ==============

class StockDataFetcher:
    """多市场股票数据抓取器"""
    
    # === A股 ===
    def get_kline(self, symbol: str, start_date: Optional[str] = None, end_date: Optional[str] = None, period: str = "daily", adjust: str = "qfq") -> List[StockKline]:
        return fetch_kline(symbol, start_date, end_date, period, adjust)
    
    def get_financial(self, symbol: str, report_type: str = "资产负债表") -> List[FinancialReport]:
        return fetch_financial(symbol, report_type)
    
    def get_dividend(self, symbol: str) -> List[Dividend]:
        return fetch_dividend(symbol)
    
    def get_lhb(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[LHBRecord]:
        return fetch_lhb(start_date, end_date)
    
    def get_northbound(self) -> List[NorthboundFlow]:
        return fetch_northbound()
    
    def get_realtime_quote(self, symbols: Optional[List[str]] = None) -> List[StockQuote]:
        return fetch_realtime_quote(symbols)
    
    def get_stock_basic(self, symbols: Optional[List[str]] = None) -> List[StockBasic]:
        return fetch_stock_basic(symbols)
    
    # === 港股 ===
    def get_hk_quote(self, symbols: Optional[List[str]] = None) -> List[HKStockQuote]:
        return fetch_hk_quote(symbols)
    
    # === 美股 ===
    def get_us_quote(self, symbols: Optional[List[str]] = None) -> List[USStockQuote]:
        return fetch_us_quote(symbols)
    
    # === 综合 ===
    def get_all_a_stock(self, symbol: str) -> Dict[str, Any]:
        """获取A股所有可用数据"""
        return {
            "quote": self.get_realtime_quote([symbol]),
            "financial": self.get_financial(symbol),
            "dividend": self.get_dividend(symbol),
        }


fetcher = StockDataFetcher()

__all__ = [
    "StockKline", "FinancialReport", "Dividend", "LHBRecord", "NorthboundFlow",
    "StockQuote", "StockBasic", "HKStockQuote", "USStockQuote",
    "fetch_kline", "fetch_financial", "fetch_dividend", "fetch_lhb", "fetch_northbound",
    "fetch_realtime_quote", "fetch_stock_basic", "fetch_hk_quote", "fetch_us_quote",
    "StockDataFetcher", "fetcher",
]
