# -*- coding: utf-8 -*-
"""
Stock data fetcher module
Supports: quote, kline, financial, dividend, LHB, northbound, basic
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from ..resilience import retry_with_backoff
from ..exceptions import DataError, SourceUnavailableError
from ..validation import (
    validate_kline_data,
    validate_quote_data,
    DataQualityValidator,
    QualityReport,
)

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

logger = logging.getLogger(__name__)


@dataclass
class StockQuote:
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
class StockKline:
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
    symbol: str
    report_date: str
    report_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class Dividend:
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
    date: str
    sh_net_inflow: float
    sz_net_inflow: float
    total_net_inflow: float
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class StockBasic:
    symbol: str
    name: str
    market: str
    industry: str
    list_date: str
    source: str = "akshare"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ============== 实时行情 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_realtime_quote_internal(symbols=None):
    df = ak.stock_zh_a_spot_em()
    if symbols:
        df = df[df["代码"].isin(symbols)]
    return df.to_dict("records")


def fetch_realtime_quote(symbols=None, source="akshare", validate=True):
    """获取实时行情，返回 (quotes, quality_issues)"""
    if source != "akshare" or not HAS_AKSHARE:
        raise SourceUnavailableError(source, f"数据源 {source} 不可用")
    validator = DataQualityValidator()
    try:
        records = _fetch_realtime_quote_internal(symbols)
        results = []
        quality_issues = []
        for rec in records:
            quote_obj = StockQuote(
                symbol=rec.get("代码", ""),
                name=rec.get("名称", ""),
                price=float(rec.get("最新价", 0) or 0),
                change_pct=float(rec.get("涨跌幅", 0) or 0),
                open=float(rec.get("今开", 0) or 0),
                high=float(rec.get("最高", 0) or 0),
                low=float(rec.get("最低", 0) or 0),
                prev_close=float(rec.get("昨收", 0) or 0),
                volume=int(rec.get("成交量", 0) or 0),
                amount=float(rec.get("成交额", 0) or 0),
                turnover_rate=float(rec.get("换手率", 0) or 0),
                pe_ratio=float(rec.get("市盈率-动态", 0) or 0),
                pb_ratio=float(rec.get("市净率", 0) or 0),
                total_market_cap=float(rec.get("总市值", 0) or 0),
                float_market_cap=float(rec.get("流通市值", 0) or 0),
            )
            results.append(quote_obj)
            if validate and quote_obj.symbol:
                q_dict = {
                    'close': quote_obj.price,
                    'open': quote_obj.open,
                    'high': quote_obj.high,
                    'low': quote_obj.low,
                    'pre_close': quote_obj.prev_close,
                    'volume': quote_obj.volume,
                    'amount': quote_obj.amount,
                    'change_pct': quote_obj.change_pct,
                    'symbol': quote_obj.symbol,
                }
                q_report = validator.validate_quote(q_dict, symbol=quote_obj.symbol)
                if not q_report.is_valid:
                    quality_issues.extend(q_report.issues)
        if quality_issues:
            logger.warning(f"实时行情验证发现 {len(quality_issues)} 个问题")
        return results, quality_issues
    except Exception as e:
        logger.error(f"实时行情获取失败: {e}")
        raise DataError(f"实时行情获取失败: {e}", data_type="quote")


# ============== 历史K线 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_kline_internal(symbol, start, end, period="daily"):
    return ak.stock_zh_a_hist(symbol=symbol, period=period, start_date=start, end_date=end, adjust="qfq")


def fetch_kline(symbol, start_date=None, end_date=None, period="daily", source="akshare", validate=True):
    """获取K线数据"""
    if source != "akshare" or not HAS_AKSHARE:
        raise SourceUnavailableError(source, f"数据源 {source} 不可用")
    try:
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        df = _fetch_kline_internal(symbol, start_date, end_date, period)
        if df is None or df.empty:
            return []
        if validate:
            report = validate_kline_data(df)
            if not report.is_valid:
                logger.warning(f"K线数据验证失败: {report}")
        results = []
        for _, row in df.iterrows():
            results.append(StockKline(
                symbol=symbol,
                date=str(row.get("日期", "")),
                open=float(row.get("开盘", 0) or 0),
                close=float(row.get("收盘", 0) or 0),
                high=float(row.get("最高", 0) or 0),
                low=float(row.get("最低", 0) or 0),
                volume=int(row.get("成交量", 0) or 0),
                amount=float(row.get("成交额", 0) or 0),
                amplitude=float(row.get("振幅", 0) or 0),
                change_pct=float(row.get("涨跌幅", 0) or 0),
                change_amount=float(row.get("涨跌额", 0) or 0),
                turnover_rate=float(row.get("换手率", 0) or 0),
            ))
        return results
    except Exception as e:
        logger.error(f"K线数据获取失败: {e}")
        raise DataError(f"K线数据获取失败: {e}", data_type="kline")


# ============== 财务数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_financial_internal(symbol, report_type):
    return ak.stock_financial_report_sina(stock=symbol, symbol=report_type)


def fetch_financial(symbol, report_type="资产负债表", source="akshare"):
    if source != "akshare" or not HAS_AKSHARE:
        raise SourceUnavailableError(source, f"数据源 {source} 不可用")
    try:
        df = _fetch_financial_internal(symbol, report_type)
        results = []
        for _, row in df.iterrows():
            results.append(FinancialReport(
                symbol=symbol,
                report_date=str(row.get("报告期", "")),
                report_type=report_type,
                data={k: v for k, v in row.items() if k not in ["报告期"]},
            ))
        return results
    except Exception as e:
        logger.error(f"财务数据获取失败: {e}")
        raise DataError(f"财务数据获取失败: {e}", data_type="financial")


# ============== 分红数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_dividend_internal(symbol):
    return ak.stock_fhps_detail_em(symbol=symbol)


def fetch_dividend(symbol, source="akshare"):
    if source != "akshare" or not HAS_AKSHARE:
        raise SourceUnavailableError(source, f"数据源 {source} 不可用")
    try:
        df = _fetch_dividend_internal(symbol)
        results = []
        for _, row in df.iterrows():
            results.append(Dividend(
                symbol=symbol,
                report_date=str(row.get("报告期", "")),
                dividend_per_share=float(row.get("每股分红", 0) or 0),
                dividend_plan=str(row.get("分红方案", "")),
                ex_dividend_date=str(row.get("除权除息日", "")),
                record_date=str(row.get("股权登记日", "")),
            ))
        return results
    except Exception as e:
        logger.error(f"分红数据获取失败: {e}")
        raise DataError(f"分红数据获取失败: {e}", data_type="dividend")


# ============== 龙虎榜 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_lhb_internal(start_date, end_date):
    return ak.stock_lhb_detail_em(start_date=start_date, end_date=end_date)


def fetch_lhb(start_date=None, end_date=None, source="akshare"):
    if source != "akshare" or not HAS_AKSHARE:
        raise SourceUnavailableError(source, f"数据源 {source} 不可用")
    try:
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        df = _fetch_lhb_internal(start_date, end_date)
        results = []
        for _, row in df.iterrows():
            results.append(LHBRecord(
                trade_date=str(row.get("龙虎榜日期", "")),
                symbol=str(row.get("代码", "")),
                name=str(row.get("名称", "")),
                explanation=str(row.get("解释说明", "")),
                buy_amount=float(row.get("买入金额", 0) or 0),
                sell_amount=float(row.get("卖出金额", 0) or 0),
                net_buy=float(row.get("净买入", 0) or 0),
                buy_seat=str(row.get("买入营业部", "")),
                sell_seat=str(row.get("卖出营业部", "")),
            ))
        return results
    except Exception as e:
        logger.error(f"龙虎榜数据获取失败: {e}")
        raise DataError(f"龙虎榜数据获取失败: {e}", data_type="lhb")


# ============== 北向资金 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_northbound_internal():
    return ak.stock_hsgt_fund_flow_summary_em()


def fetch_northbound(source="akshare"):
    if source != "akshare" or not HAS_AKSHARE:
        raise SourceUnavailableError(source, f"数据源 {source} 不可用")
    try:
        df = _fetch_northbound_internal()
        results = []
        for _, row in df.iterrows():
            results.append(NorthboundFlow(
                date=str(row.get("日期", "")),
                sh_net_inflow=float(row.get("沪股通净流入", 0) or 0),
                sz_net_inflow=float(row.get("深股通净流入", 0) or 0),
                total_net_inflow=float(row.get("北向资金净流入", 0) or 0),
            ))
        return results
    except Exception as e:
        logger.error(f"北向资金数据获取失败: {e}")
        raise DataError(f"北向资金获取失败: {e}", data_type="northbound")


# ============== 股票基本信息 ==============

def fetch_stock_basic(source="akshare"):
    if source != "akshare" or not HAS_AKSHARE:
        raise SourceUnavailableError(source, f"数据源 {source} 不可用")
    try:
        df = ak.stock_zh_a_spot_em()
        results = []
        for _, row in df.iterrows():
            results.append(StockBasic(
                symbol=str(row.get("代码", "")),
                name=str(row.get("名称", "")),
                market="SH" if str(row.get("代码", "")).startswith("6") else "SZ",
                industry=str(row.get("行业", "")),
                list_date=str(row.get("上市日期", "")),
            ))
        return results
    except Exception as e:
        logger.error(f"股票基本信息获取失败: {e}")
        raise DataError(f"股票基本信息获取失败: {e}", data_type="basic")


# ============== 便捷函数 ==============

def fetch_all_stock_data(symbol=None, data_types=None, source="akshare"):
    """获取股票所有类型数据，返回 (results, quality_issues)"""
    if data_types is None:
        data_types = ["quote", "kline", "financial", "dividend", "lhb", "northbound", "basic"]
    results: Dict[str, Any] = {}
    quality_issues: List[Any] = []
    if "quote" in data_types:
        quote_results, quote_issues = fetch_realtime_quote(
            symbols=[symbol] if symbol else None, source=source
        )
        results["quote"] = quote_results
        quality_issues.extend(quote_issues)
    if "kline" in data_types and symbol:
        try:
            kline_results = fetch_kline(symbol=symbol, source=source)
            results["kline"] = kline_results
        except DataError as e:
            logger.error(f"K线批量获取失败: {e}")
            results["kline"] = []
    if "financial" in data_types and symbol:
        results["financial"] = fetch_financial(symbol=symbol, source=source)
    if "dividend" in data_types and symbol:
        results["dividend"] = fetch_dividend(symbol=symbol, source=source)
    if "lhb" in data_types:
        results["lhb"] = fetch_lhb(source=source)
    if "northbound" in data_types:
        results["northbound"] = fetch_northbound(source=source)
    if "basic" in data_types:
        results["basic"] = fetch_stock_basic(source=source)
    return results, quality_issues


__all__ = [
    "StockQuote", "StockKline", "FinancialReport", "Dividend",
    "LHBRecord", "NorthboundFlow", "StockBasic",
    "fetch_realtime_quote", "fetch_kline", "fetch_financial",
    "fetch_dividend", "fetch_lhb", "fetch_northbound",
    "fetch_stock_basic", "fetch_all_stock_data",
    "QualityReport", "DataQualityValidator",
]
