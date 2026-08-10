# -*- coding: utf-8 -*-
"""
北向资金数据抓取器
支持：沪深股通净流入、持仓明细
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


class NorthboundFlow:
    """北向资金流向数据模型"""
    def __init__(self, date: str,
                 sh_net_inflow: float,
                 sz_net_inflow: float,
                 total_net_inflow: float,
                 sh_hold_value: float = 0.0,
                 sz_hold_value: float = 0.0,
                 source: str = 'akshare'):
        self.date = date
        self.sh_net_inflow = sh_net_inflow
        self.sz_net_inflow = sz_net_inflow
        self.total_net_inflow = total_net_inflow
        self.sh_hold_value = sh_hold_value
        self.sz_hold_value = sz_hold_value
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date,
            'sh_net_inflow': self.sh_net_inflow,
            'sz_net_inflow': self.sz_net_inflow,
            'total_net_inflow': self.total_net_inflow,
            'sh_hold_value': self.sh_hold_value,
            'sz_hold_value': self.sz_hold_value,
            'source': self.source,
            'timestamp': self.timestamp,
        }


class NorthboundHoldStock:
    """北向资金持仓个股数据模型"""
    def __init__(self, stock_code: str, stock_name: str,
                 hold_shares: float, hold_value: float,
                 change_shares: float = 0.0,
                 change_pct: float = 0.0,
                 source: str = 'akshare'):
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.hold_shares = hold_shares
        self.hold_value = hold_value
        self.change_shares = change_shares
        self.change_pct = change_pct
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'hold_shares': self.hold_shares,
            'hold_value': self.hold_value,
            'change_shares': self.change_shares,
            'change_pct': self.change_pct,
            'source': self.source,
            'timestamp': self.timestamp,
        }


# ============== 历史流向数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_hsgt_hist():
    """内部函数：获取 AKShare 北向资金历史数据（带重试）"""
    return ak.stock_hsgt_hist_em()


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_hsgt_hold_stock():
    """内部函数：获取 AKShare 北向资金持仓股票（带重试）"""
    return ak.stock_hsgt_hold_stock_em()


def fetch_northbound_flow(
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取北向资金历史流向数据

    Args:
        source: 数据源

    Returns:
        List[Dict]: 北向资金流向数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            df = _fetch_akshare_hsgt_hist()
            for _, row in df.iterrows():
                sh_net = float(row.get('沪股通净流入', 0) or 0)
                sz_net = float(row.get('深股通净流入', 0) or 0)
                results.append(NorthboundFlow(
                    date=row.get('日期', ''),
                    sh_net_inflow=sh_net,
                    sz_net_inflow=sz_net,
                    total_net_inflow=sh_net + sz_net,
                    source='akshare'
                ).to_dict())
        except Exception as e:
            logger.error(f"北向资金历史数据获取失败: {e}")

    return results


def fetch_northbound_holdings(
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取北向资金持仓个股数据

    Args:
        source: 数据源

    Returns:
        List[Dict]: 北向资金持仓数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            df = _fetch_akshare_hsgt_hold_stock()
            for _, row in df.iterrows():
                results.append(NorthboundHoldStock(
                    stock_code=row.get('股票代码', ''),
                    stock_name=row.get('股票名称', ''),
                    hold_shares=float(row.get('持股数', 0) or 0),
                    hold_value=float(row.get('持股市值', 0) or 0),
                    change_shares=float(row.get('持股数变化', 0) or 0),
                    change_pct=float(row.get('持股数变化率', 0) or 0),
                    source='akshare'
                ).to_dict())
        except Exception as e:
            logger.error(f"北向资金持仓数据获取失败: {e}")

    return results


# ============== 东方财富备选接口 ==============

def _fetch_eastmoney_northbound() -> List[Dict[str, Any]]:
    """从东方财富获取北向资金数据（备选）"""
    results = []
    if not HAS_HTTPX:
        return results

    try:
        url = "https://push2.eastmoney.com/api/qt/kamt.clisget"
        params = {
            'fields1': 'f1,f2,f3,f7',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63',
            'ut': 'fa5fd1943c7b386f172d6893dbbd1d0c'
        }
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params=params)
            data = resp.json()
            if data.get('data') and data['data'].get('list'):
                for item in data['data']['list']:
                    results.append({
                        'date': item.get('f57', ''),
                        'sh_net_inflow': float(item.get('f58', 0) or 0) / 10000,
                        'sz_net_inflow': float(item.get('f59', 0) or 0) / 10000,
                        'total_net_inflow': float(item.get('f60', 0) or 0) / 10000,
                        'source': 'eastmoney',
                        'timestamp': datetime.utcnow().isoformat(),
                    })
    except Exception as e:
        logger.error(f"东方财富北向资金获取失败: {e}")

    return results


# ============== 便捷函数 ==============

def fetch_northbound_data(
    data_type: str = 'flow',
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取北向资金数据（统一入口）

    Args:
        data_type: 数据类型 (flow/holdings)
        source: 数据源

    Returns:
        List[Dict]: 北向资金数据
    """
    if data_type == 'flow':
        results = fetch_northbound_flow(source)
    elif data_type == 'holdings':
        results = fetch_northbound_holdings(source)
    else:
        logger.warning(f"未知的北向资金数据类型: {data_type}")
        results = []

    # 如果主数据源失败，尝试备选
    if not results and source == 'akshare' and data_type == 'flow' and HAS_HTTPX:
        logger.info("AKShare 北向资金无数据，尝试东方财富备选")
        results = _fetch_eastmoney_northbound()

    return results
