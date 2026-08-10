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


class NorthboundFlowData:
    """北向资金数据模型"""
    def __init__(self, date: str, flow_type: str, sector: str,
                 net_buy: float, net_inflow: float,
                 source: str = 'akshare'):
        self.date = date
        self.flow_type = flow_type
        self.sector = sector
        self.net_buy = net_buy
        self.net_inflow = net_inflow
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date,
            'flow_type': self.flow_type,
            'sector': self.sector,
            'net_buy': self.net_buy,
            'net_inflow': self.net_inflow,
            'source': self.source,
            'timestamp': self.timestamp,
        }


# ============== 个股资金流向 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_individual_fund_flow(symbol: str, market: str = 'sh'):
    """内部函数：获取 AKShare 个股资金流向（带重试）"""
    return ak.stock_individual_fund_flow(stock=symbol, market=market)


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
                            name='',
                            date=str(row.get('日期', '')),
                            main_inflow_net=float(row.get('主力净流入-净额', 0) or 0),
                            main_inflow_ratio=float(row.get('主力净流入-净占比', 0) or 0),
                            retail_inflow_net=float(row.get('小单净流入-净额', 0) or 0),
                            mid_inflow_net=float(row.get('中单净流入-净额', 0) or 0),
                            large_inflow_net=float(row.get('大单净流入-净额', 0) or 0),
                            super_large_inflow_net=float(row.get('超大单净流入-净额', 0) or 0),
                            close_price=float(row.get('收盘价', 0) or 0),
                            change_pct=float(row.get('涨跌幅', 0) or 0),
                            source='akshare'
                        ).to_dict())
                else:
                    # 个股资金流向排行（代理环境下可能失败，返回空）
                    try:
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
                    except Exception as e:
                        logger.warning(f"个股资金流排行获取失败（代理限制）: {e}")

            elif data_type == 'sector':
                # 行业板块资金流向
                df = ak.stock_fund_flow_industry(symbol='即时')
                for _, row in df.iterrows():
                    results.append(SectorCapitalFlowData(
                        sector_code='',
                        sector_name=row.get('行业', ''),
                        main_inflow_net=float(row.get('净额', 0) or 0),
                        main_inflow_ratio=0.0,
                        change_pct=float(row.get('行业-涨跌幅', 0) or 0),
                        rank=int(row.get('序号', 0) or 0),
                        source='akshare'
                    ).to_dict())

                # 概念板块资金流向
                df_concept = ak.stock_fund_flow_concept(symbol='即时')
                for _, row in df_concept.iterrows():
                    results.append(SectorCapitalFlowData(
                        sector_code='',
                        sector_name=row.get('行业', ''),
                        main_inflow_net=float(row.get('净额', 0) or 0),
                        main_inflow_ratio=0.0,
                        change_pct=float(row.get('行业-涨跌幅', 0) or 0),
                        rank=int(row.get('序号', 0) or 0),
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


# ============== 北向资金 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_northbound_summary():
    """内部函数：获取北向资金汇总（带重试）"""
    return ak.stock_hsgt_fund_flow_summary_em()


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_northbound_hist(symbol: str = '沪股通'):
    """内部函数：获取北向资金历史（带重试）"""
    return ak.stock_hsgt_hist_em(symbol=symbol)


def fetch_northbound_flow(
    data_type: str = 'summary',
    symbol: str = None
) -> List[Dict[str, Any]]:
    """获取北向资金数据

    Args:
        data_type: 数据类型 (summary/hist)
        symbol: 北向类型 (沪股通/深股通/港股通(沪)/港股通(深))

    Returns:
        List[Dict]: 北向资金数据列表
    """
    results = []

    if not HAS_AKSHARE:
        return results

    try:
        if data_type == 'summary':
            df = _fetch_akshare_northbound_summary()
            for _, row in df.iterrows():
                results.append(NorthboundFlowData(
                    date=str(row.get('交易日', '')),
                    flow_type=str(row.get('类型', '')),
                    sector=str(row.get('板块', '')),
                    net_buy=float(row.get('成交净买额', 0) or 0),
                    net_inflow=float(row.get('资金净流入', 0) or 0),
                    source='akshare'
                ).to_dict())

        elif data_type == 'hist':
            target_symbol = symbol or '沪股通'
            df = _fetch_akshare_northbound_hist(target_symbol)
            for _, row in df.iterrows():
                results.append({
                    'date': str(row.get('日期', '')),
                    'net_buy': float(row.get('当日成交净买额', 0) or 0),
                    'buy_amount': float(row.get('买入成交额', 0) or 0),
                    'sell_amount': float(row.get('卖出成交额', 0) or 0),
                    'cumulative_net': float(row.get('历史累计净买额', 0) or 0),
                    'leading_stock': str(row.get('领涨股', '')),
                    'leading_stock_change': float(row.get('领涨股-涨跌幅', 0) or 0),
                    'source': 'akshare',
                    'timestamp': datetime.utcnow().isoformat(),
                })

    except Exception as e:
        logger.error(f"北向资金数据获取失败: {e}")

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
