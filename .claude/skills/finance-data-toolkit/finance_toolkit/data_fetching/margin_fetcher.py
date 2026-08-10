# -*- coding: utf-8 -*-
"""
融资融券数据抓取器
支持：市场汇总、个股明细
数据源：AKShare (主)、东方财富 API (备)
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from ..resilience import retry_with_backoff

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

logger = logging.getLogger(__name__)


class MarginSummary:
    """融资融券汇总数据"""
    def __init__(self, date: str, exchange: str,
                 margin_balance: float, margin_buy: float,
                 short_balance: float, short_sell: float,
                 source: str = 'akshare'):
        self.date = date
        self.exchange = exchange
        self.margin_balance = margin_balance
        self.margin_buy = margin_buy
        self.short_balance = short_balance
        self.short_sell = short_sell
        self.total_balance = margin_balance + short_balance
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date,
            'exchange': self.exchange,
            'margin_balance': self.margin_balance,
            'margin_buy': self.margin_buy,
            'short_balance': self.short_balance,
            'short_sell': self.short_sell,
            'total_balance': self.total_balance,
            'source': self.source,
            'timestamp': self.timestamp,
        }


class MarginStock:
    """个股融资融券数据"""
    def __init__(self, stock_code: str, stock_name: str, date: str,
                 margin_balance: float, margin_change: float,
                 short_balance: float, short_change: float,
                 source: str = 'akshare'):
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.date = date
        self.margin_balance = margin_balance
        self.margin_change = margin_change
        self.short_balance = short_balance
        self.short_change = short_change
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'date': self.date,
            'margin_balance': self.margin_balance,
            'margin_change': self.margin_change,
            'short_balance': self.short_balance,
            'short_change': self.short_change,
            'source': self.source,
            'timestamp': self.timestamp,
        }


# ============== 市场汇总数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_margin_size_szse():
    """内部函数：获取深交所融资融券汇总（带重试）"""
    return ak.stock_margin_size_szse()


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_margin_size_sse():
    """内部函数：获取上交所融资融券汇总（带重试）"""
    return ak.stock_margin_size_sse()


def fetch_margin_summary(
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取融资融券市场汇总数据

    Args:
        source: 数据源

    Returns:
        List[Dict]: 融资融券汇总数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            # 深交所
            df_sz = _fetch_akshare_margin_size_szse()
            for _, row in df_sz.iterrows():
                results.append(MarginSummary(
                    date=row.get('日期', ''),
                    exchange='SZSE',
                    margin_balance=float(row.get('融资余额', 0) or 0),
                    margin_buy=float(row.get('融资买入额', 0) or 0),
                    short_balance=float(row.get('融券余额', 0) or 0),
                    short_sell=float(row.get('融券卖出量', 0) or 0),
                    source='akshare'
                ).to_dict())

            # 上交所
            df_sse = _fetch_akshare_margin_size_sse()
            for _, row in df_sse.iterrows():
                results.append(MarginSummary(
                    date=row.get('日期', ''),
                    exchange='SSE',
                    margin_balance=float(row.get('融资余额', 0) or 0),
                    margin_buy=float(row.get('融资买入额', 0) or 0),
                    short_balance=float(row.get('融券余额', 0) or 0),
                    short_sell=float(row.get('融券卖出量', 0) or 0),
                    source='akshare'
                ).to_dict())

        except Exception as e:
            logger.error(f"融资融券汇总数据获取失败: {e}")

    return results


# ============== 个股明细数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_margin_detail_szse():
    """内部函数：获取深交所融资融券明细（带重试）"""
    return ak.stock_margin_detail_szse()


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_margin_detail_sse():
    """内部函数：获取上交所融资融券明细（带重试）"""
    return ak.stock_margin_detail_sse()


def fetch_margin_stock(
    exchange: str = None,
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取个股融资融券明细数据

    Args:
        exchange: 交易所 (SZSE/SSE)，不提供则获取全部
        source: 数据源

    Returns:
        List[Dict]: 个股融资融券数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            # 深交所
            if exchange in [None, 'SZSE']:
                df_sz = _fetch_akshare_margin_detail_szse()
                for _, row in df_sz.iterrows():
                    results.append(MarginStock(
                        stock_code=row.get('代码', ''),
                        stock_name=row.get('名称', ''),
                        date=row.get('日期', ''),
                        margin_balance=float(row.get('融资余额', 0) or 0),
                        margin_change=float(row.get('融资余额变化', 0) or 0),
                        short_balance=float(row.get('融券余额', 0) or 0),
                        short_change=float(row.get('融券余额变化', 0) or 0),
                        source='akshare'
                    ).to_dict())

            # 上交所
            if exchange in [None, 'SSE']:
                df_sse = _fetch_akshare_margin_detail_sse()
                for _, row in df_sse.iterrows():
                    results.append(MarginStock(
                        stock_code=row.get('代码', ''),
                        stock_name=row.get('名称', ''),
                        date=row.get('日期', ''),
                        margin_balance=float(row.get('融资余额', 0) or 0),
                        margin_change=float(row.get('融资余额变化', 0) or 0),
                        short_balance=float(row.get('融券余额', 0) or 0),
                        short_change=float(row.get('融券余额变化', 0) or 0),
                        source='akshare'
                    ).to_dict())

        except Exception as e:
            logger.error(f"融资融券明细数据获取失败: {e}")

    return results


# ============== 东方财富备选接口 ==============

def _fetch_eastmoney_margin_summary() -> List[Dict[str, Any]]:
    """从东方财富获取融资融券汇总（备选）"""
    results = []
    if not HAS_HTTPX:
        return results

    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            'reportName': 'RPTA_WEB_MARGIN',
            'columns': 'ALL',
            'pageSize': '50',
            'sortColumns': 'TRADE_DATE',
            'sortTypes': '-1'
        }
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params=params)
            data = resp.json()
            if data.get('result') and data['result'].get('data'):
                for item in data['result']['data']:
                    results.append({
                        'date': item.get('TRADE_DATE', ''),
                        'exchange': item.get('MARKET', ''),
                        'margin_balance': float(item.get('RZYE', 0) or 0),
                        'margin_buy': float(item.get('RZMRE', 0) or 0),
                        'short_balance': float(item.get('RQYE', 0) or 0),
                        'short_sell': float(item.get('RQMCL', 0) or 0),
                        'source': 'eastmoney',
                        'timestamp': datetime.utcnow().isoformat(),
                    })
    except Exception as e:
        logger.error(f"东方财富融资融券获取失败: {e}")

    return results


# ============== 便捷函数 ==============

def fetch_margin_data(
    data_type: str = 'summary',
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取融资融券数据（统一入口）

    Args:
        data_type: 数据类型 (summary/stock)
        source: 数据源

    Returns:
        List[Dict]: 融资融券数据
    """
    if data_type == 'summary':
        results = fetch_margin_summary(source)
    elif data_type == 'stock':
        results = fetch_margin_stock(source=source)
    else:
        logger.warning(f"未知的融资融券数据类型: {data_type}")
        results = []

    # 如果主数据源失败，尝试备选
    if not results and source == 'akshare' and data_type == 'summary' and HAS_HTTPX:
        logger.info("AKShare 融资融券无数据，尝试东方财富备选")
        results = _fetch_eastmoney_margin_summary()

    return results
