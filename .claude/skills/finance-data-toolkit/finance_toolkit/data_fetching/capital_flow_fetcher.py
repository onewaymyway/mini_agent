# -*- coding: utf-8 -*-
"""
资金流向数据抓取器
支持：个股资金流向、板块资金流向、历史资金流向
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


class CapitalFlowData:
    """资金流向数据模型"""
    def __init__(self, symbol: str, name: str, date: str,
                 main_inflow_net: float, main_inflow_ratio: float,
                 retail_inflow_net: float, mid_inflow_net: float,
                 large_inflow_net: float, super_large_inflow_net: float,
                 close_price: float, change_pct: float,
                 source: str = 'akshare'):
        self.symbol = symbol
        self.name = name
        self.date = date
        self.main_inflow_net = main_inflow_net
        self.main_inflow_ratio = main_inflow_ratio
        self.retail_inflow_net = retail_inflow_net
        self.mid_inflow_net = mid_inflow_net
        self.large_inflow_net = large_inflow_net
        self.super_large_inflow_net = super_large_inflow_net
        self.close_price = close_price
        self.change_pct = change_pct
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'name': self.name,
            'date': self.date,
            'main_inflow_net': self.main_inflow_net,
            'main_inflow_ratio': self.main_inflow_ratio,
            'retail_inflow_net': self.retail_inflow_net,
            'mid_inflow_net': self.mid_inflow_net,
            'large_inflow_net': self.large_inflow_net,
            'super_large_inflow_net': self.super_large_inflow_net,
            'close_price': self.close_price,
            'change_pct': self.change_pct,
            'source': self.source,
            'timestamp': self.timestamp,
        }


class SectorCapitalFlowData:
    """板块资金流向数据模型"""
    def __init__(self, sector_code: str, sector_name: str,
                 main_inflow_net: float, main_inflow_ratio: float,
                 change_pct: float, rank: int,
                 source: str = 'akshare'):
        self.sector_code = sector_code
        self.sector_name = sector_name
        self.main_inflow_net = main_inflow_net
        self.main_inflow_ratio = main_inflow_ratio
        self.change_pct = change_pct
        self.rank = rank
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'sector_code': self.sector_code,
            'sector_name': self.sector_name,
            'main_inflow_net': self.main_inflow_net,
            'main_inflow_ratio': self.main_inflow_ratio,
            'change_pct': self.change_pct,
            'rank': self.rank,
            'source': self.source,
            'timestamp': self.timestamp,
        }


# ============== 个股资金流向 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_individual_fund_flow(symbol: str):
    """内部函数：获取 AKShare 个股资金流向（带重试）"""
    return ak.stock_individual_fund_flow(stock=symbol)


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_individual_fund_flow_rank(indicator: str = '今日'):
    """内部函数：获取 AKShare 个股资金流向排行（带重试）"""
    return ak.stock_individual_fund_flow_rank(indicator=indicator)


def fetch_individual_capital_flow(
    symbol: str = None,
    data_type: str = 'stock',
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取个股资金流向数据

    Args:
        symbol: 股票代码（如 '600000'），不提供则获取排行
        data_type: 数据类型 (stock/sector)
        source: 数据源

    Returns:
        List[Dict]: 资金流向数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            if data_type == 'stock':
                if symbol:
                    # 个股资金流向历史
                    df = _fetch_akshare_individual_fund_flow(symbol)
                    for _, row in df.iterrows():
                        results.append(CapitalFlowData(
                            symbol=symbol,
                            name=row.get('名称', ''),
                            date=row.get('日期', ''),
                            main_inflow_net=float(row.get('主力净流入-净额', 0) or 0),
                            main_inflow_ratio=float(row.get('主力净流入-净占比', 0) or 0),
                            retail_inflow_net=float(row.get('散户净流入-净额', 0) or 0),
                            mid_inflow_net=float(row.get('中单净流入-净额', 0) or 0),
                            large_inflow_net=float(row.get('大单净流入-净额', 0) or 0),
                            super_large_inflow_net=float(row.get('超大单净流入-净额', 0) or 0),
                            close_price=float(row.get('收盘价', 0) or 0),
                            change_pct=float(row.get('涨跌幅', 0) or 0),
                            source='akshare'
                        ).to_dict())
                else:
                    # 个股资金流向排行
                    df = _fetch_akshare_individual_fund_flow_rank('今日')
                    for _, row in df.iterrows():
                        results.append({
                            'symbol': row.get('代码', ''),
                            'name': row.get('名称', ''),
                            'main_inflow_net': float(row.get('主力净流入-净额', 0) or 0),
                            'main_inflow_ratio': float(row.get('主力净流入-净占比', 0) or 0),
                            'rank': int(row.get('排名', 0) or 0),
                            'source': 'akshare',
                            'timestamp': datetime.utcnow().isoformat(),
                        })

            elif data_type == 'sector':
                # 行业板块资金流向
                df = ak.stock_sector_fund_flow_rank(industry_type='行业')
                for _, row in df.iterrows():
                    results.append(SectorCapitalFlowData(
                        sector_code=row.get('板块代码', ''),
                        sector_name=row.get('板块名称', ''),
                        main_inflow_net=float(row.get('主力净流入-净额', 0) or 0),
                        main_inflow_ratio=float(row.get('主力净流入-净占比', 0) or 0),
                        change_pct=float(row.get('涨跌幅', 0) or 0),
                        rank=int(row.get('排名', 0) or 0),
                        source='akshare'
                    ).to_dict())

                # 概念板块资金流向
                df_concept = ak.stock_sector_fund_flow_rank(industry_type='概念')
                for _, row in df_concept.iterrows():
                    results.append(SectorCapitalFlowData(
                        sector_code=row.get('板块代码', ''),
                        sector_name=row.get('板块名称', ''),
                        main_inflow_net=float(row.get('主力净流入-净额', 0) or 0),
                        main_inflow_ratio=float(row.get('主力净流入-净占比', 0) or 0),
                        change_pct=float(row.get('涨跌幅', 0) or 0),
                        rank=int(row.get('排名', 0) or 0),
                        source='akshare'
                    ).to_dict())

        except Exception as e:
            logger.error(f"资金流向数据获取失败: {e}")

    return results


# ============== 东方财富备选接口 ==============

def _fetch_eastmoney_capital_flow(symbol: str) -> List[Dict[str, Any]]:
    """从东方财富获取资金流向数据（备选）"""
    results = []
    if not HAS_HTTPX:
        return results

    try:
        # 东方财富资金流向 API
        code = symbol.replace('.', '').replace('SH', '').replace('SZ', '')
        market = '1' if symbol.endswith('SH') else '0'
        url = f"https://push2.eastmoney.com/api/qt/stock/fflow/dayList/get"
        params = {
            'secid': f"{market}.{code}",
            'fields1': 'f1,f2,f3,f7',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63',
            'ut': 'fa5fd1943c7b386f172d6893dbbd1d0c'
        }
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params=params)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    results.append({
                        'symbol': symbol,
                        'date': item.get('f57', ''),
                        'main_inflow_net': float(item.get('f58', 0) or 0) / 10000,
                        'main_inflow_ratio': float(item.get('f59', 0) or 0),
                        'retail_inflow_net': float(item.get('f60', 0) or 0) / 10000,
                        'source': 'eastmoney',
                        'timestamp': datetime.utcnow().isoformat(),
                    })
    except Exception as e:
        logger.error(f"东方财富资金流向获取失败: {e}")

    return results


# ============== 便捷函数 ==============

def fetch_capital_flow(
    symbol: str = None,
    data_type: str = 'stock',
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取资金流向数据（统一入口）

    Args:
        symbol: 股票代码
        data_type: 数据类型 (stock/sector)
        source: 数据源

    Returns:
        List[Dict]: 资金流向数据
    """
    results = fetch_individual_capital_flow(symbol, data_type, source)

    # 如果主数据源失败，尝试备选
    if not results and source == 'akshare' and HAS_HTTPX:
        logger.info("AKShare 资金流向无数据，尝试东方财富备选")
        results = _fetch_eastmoney_capital_flow(symbol or '')

    return results
