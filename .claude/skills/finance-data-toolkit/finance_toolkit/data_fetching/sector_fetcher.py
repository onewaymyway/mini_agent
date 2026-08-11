# -*- coding: utf-8 -*-
"""
板块数据抓取器
支持：行业板块、概念板块、地域板块的行情和资金流向
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


# ============== 数据模型 ==============

class SectorQuote:
    """板块行情数据"""
    def __init__(self, sector_code: str, sector_name: str,
                 change_pct: float, leading_stock: str,
                 leading_stock_change: float, avg_pe: float,
                 avg_pb: float, total_mv: float,
                 turnover_rate: float, hot_rank: int,
                 source: str = 'akshare'):
        self.sector_code = sector_code
        self.sector_name = sector_name
        self.change_pct = change_pct
        self.leading_stock = leading_stock
        self.leading_stock_change = leading_stock_change
        self.avg_pe = avg_pe
        self.avg_pb = avg_pb
        self.total_mv = total_mv
        self.turnover_rate = turnover_rate
        self.hot_rank = hot_rank
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'sector_code': self.sector_code,
            'sector_name': self.sector_name,
            'change_pct': self.change_pct,
            'leading_stock': self.leading_stock,
            'leading_stock_change': self.leading_stock_change,
            'avg_pe': self.avg_pe,
            'avg_pb': self.avg_pb,
            'total_mv': self.total_mv,
            'turnover_rate': self.turnover_rate,
            'hot_rank': self.hot_rank,
            'source': self.source,
            'timestamp': self.timestamp,
        }


class SectorCapitalFlow:
    """板块资金流向数据"""
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


# ============== 行业板块数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_industry_name():
    """内部函数：获取行业板块名称列表（带重试）"""
    return ak.stock_board_industry_name_em()


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_industry_change():
    """内部函数：获取行业板块行情（带重试）"""
    return ak.stock_board_industry_change_em()


def fetch_industry_quote(source: str = 'akshare') -> List[Dict[str, Any]]:
    """获取行业板块行情数据

    Args:
        source: 数据源

    Returns:
        List[Dict]: 行业板块行情数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            df = _fetch_akshare_industry_change()
            for _, row in df.iterrows():
                results.append(SectorQuote(
                    sector_code=str(row.get('代码', '')),
                    sector_name=str(row.get('名称', '')),
                    change_pct=float(row.get('涨跌幅', 0) or 0),
                    leading_stock=str(row.get('领涨股票', '')),
                    leading_stock_change=float(row.get('领涨股票-涨跌幅', 0) or 0),
                    avg_pe=float(row.get('平均市盈率', 0) or 0),
                    avg_pb=float(row.get('平均市净率', 0) or 0),
                    total_mv=float(row.get('总市值', 0) or 0),
                    turnover_rate=float(row.get('换手率', 0) or 0),
                    hot_rank=int(row.get('排名', 0) or 0),
                    source='akshare'
                ).to_dict())
        except Exception as e:
            logger.error(f"行业板块行情获取失败: {e}")

    return results


def fetch_industry_list() -> List[Dict[str, Any]]:
    """获取行业板块列表

    Returns:
        List[Dict]: 行业板块列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = _fetch_akshare_industry_name()
            for _, row in df.iterrows():
                results.append({
                    'sector_code': str(row.get('板块代码', '')),
                    'sector_name': str(row.get('板块名称', '')),
                    'source': 'akshare',
                    'timestamp': datetime.utcnow().isoformat(),
                })
        except Exception as e:
            logger.error(f"行业板块列表获取失败: {e}")

    return results


# ============== 概念板块数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_concept_name():
    """内部函数：获取概念板块名称列表（带重试）"""
    return ak.stock_board_concept_name_em()


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_concept_change():
    """内部函数：获取概念板块行情（带重试）"""
    return ak.stock_board_concept_change_em()


def fetch_concept_quote(source: str = 'akshare') -> List[Dict[str, Any]]:
    """获取概念板块行情数据

    Args:
        source: 数据源

    Returns:
        List[Dict]: 概念板块行情数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            df = _fetch_akshare_concept_change()
            for _, row in df.iterrows():
                results.append(SectorQuote(
                    sector_code=str(row.get('代码', '')),
                    sector_name=str(row.get('名称', '')),
                    change_pct=float(row.get('涨跌幅', 0) or 0),
                    leading_stock=str(row.get('领涨股票', '')),
                    leading_stock_change=float(row.get('领涨股票-涨跌幅', 0) or 0),
                    avg_pe=float(row.get('平均市盈率', 0) or 0),
                    avg_pb=float(row.get('平均市净率', 0) or 0),
                    total_mv=float(row.get('总市值', 0) or 0),
                    turnover_rate=float(row.get('换手率', 0) or 0),
                    hot_rank=int(row.get('排名', 0) or 0),
                    source='akshare'
                ).to_dict())
        except Exception as e:
            logger.error(f"概念板块行情获取失败: {e}")

    return results


def fetch_concept_list() -> List[Dict[str, Any]]:
    """获取概念板块列表

    Returns:
        List[Dict]: 概念板块列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = _fetch_akshare_concept_name()
            for _, row in df.iterrows():
                results.append({
                    'sector_code': str(row.get('板块代码', '')),
                    'sector_name': str(row.get('板块名称', '')),
                    'source': 'akshare',
                    'timestamp': datetime.utcnow().isoformat(),
                })
        except Exception as e:
            logger.error(f"概念板块列表获取失败: {e}")

    return results


# ============== 地域板块数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_region_name():
    """内部函数：获取地域板块名称列表（带重试）"""
    return ak.stock_board_region_name_em()


def fetch_region_quote(source: str = 'akshare') -> List[Dict[str, Any]]:
    """获取地域板块行情数据

    Args:
        source: 数据源

    Returns:
        List[Dict]: 地域板块行情数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = ak.stock_board_region_change_em()
            for _, row in df.iterrows():
                results.append({
                    'sector_code': str(row.get('代码', '')),
                    'sector_name': str(row.get('名称', '')),
                    'change_pct': float(row.get('涨跌幅', 0) or 0),
                    'stock_count': int(row.get('股票数量', 0) or 0),
                    'source': 'akshare',
                    'timestamp': datetime.utcnow().isoformat(),
                })
        except Exception as e:
            logger.error(f"地域板块行情获取失败: {e}")

    return results


def fetch_region_list() -> List[Dict[str, Any]]:
    """获取地域板块列表

    Returns:
        List[Dict]: 地域板块列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = _fetch_akshare_region_name()
            for _, row in df.iterrows():
                results.append({
                    'sector_code': str(row.get('板块代码', '')),
                    'sector_name': str(row.get('板块名称', '')),
                    'source': 'akshare',
                    'timestamp': datetime.utcnow().isoformat(),
                })
        except Exception as e:
            logger.error(f"地域板块列表获取失败: {e}")

    return results


# ============== 板块资金流向 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_sector_fund_flow_industry():
    """内部函数：获取行业板块资金流向（带重试）"""
    return ak.stock_fund_flow_industry(symbol='即时')


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_sector_fund_flow_concept():
    """内部函数：获取概念板块资金流向（带重试）"""
    return ak.stock_fund_flow_concept(symbol='即时')


def fetch_sector_capital_flow(data_type: str = 'industry') -> List[Dict[str, Any]]:
    """获取板块资金流向数据

    Args:
        data_type: 数据类型 (industry/concept)

    Returns:
        List[Dict]: 板块资金流向数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            if data_type == 'industry':
                df = _fetch_akshare_sector_fund_flow_industry()
                for _, row in df.iterrows():
                    results.append(SectorCapitalFlow(
                        sector_code='',
                        sector_name=str(row.get('行业', '')),
                        main_inflow_net=float(row.get('净额', 0) or 0),
                        main_inflow_ratio=0.0,
                        change_pct=float(row.get('行业-涨跌幅', 0) or 0),
                        rank=int(row.get('序号', 0) or 0),
                        source='akshare'
                    ).to_dict())
            elif data_type == 'concept':
                df = _fetch_akshare_sector_fund_flow_concept()
                for _, row in df.iterrows():
                    results.append(SectorCapitalFlow(
                        sector_code='',
                        sector_name=str(row.get('行业', '')),
                        main_inflow_net=float(row.get('净额', 0) or 0),
                        main_inflow_ratio=0.0,
                        change_pct=float(row.get('行业-涨跌幅', 0) or 0),
                        rank=int(row.get('序号', 0) or 0),
                        source='akshare'
                    ).to_dict())
        except Exception as e:
            logger.error(f"板块资金流向获取失败: {e}")

    return results


# ============== 东方财富备选接口 ==============

def _fetch_eastmoney_sector_quote(fs: str) -> List[Dict[str, Any]]:
    """从东方财富获取板块行情（备选）

    Args:
        fs: 板块类型标识，如 'm:90+t:2' (行业) 或 'm:90+t:3' (概念)

    Returns:
        List[Dict]: 板块行情数据列表
    """
    results = []
    if not HAS_HTTPX:
        return results

    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            'pn': 1,
            'pz': 500,
            'po': 1,
            'np': 1,
            'fltt': 2,
            'invt': 2,
            'fs': fs,
            'fields': 'f12,f14,f2,f3,f4,f15,f16,f17,f18'
        }
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params=params)
            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                for item in data['data']['diff']:
                    results.append({
                        'sector_code': item.get('f12', ''),
                        'sector_name': item.get('f14', ''),
                        'change_pct': float(item.get('f3', 0) or 0),
                        'price': float(item.get('f2', 0) or 0),
                        'volume': int(item.get('f5', 0) or 0),
                        'amount': float(item.get('f6', 0) or 0),
                        'source': 'eastmoney',
                        'timestamp': datetime.utcnow().isoformat(),
                    })
    except Exception as e:
        logger.error(f"东方财富板块行情获取失败: {e}")

    return results


# ============== 便捷函数 ==============

def fetch_sector_quote(data_type: str = 'industry', source: str = 'akshare') -> List[Dict[str, Any]]:
    """获取板块行情数据（统一入口）

    Args:
        data_type: 数据类型 (industry/concept/region)
        source: 数据源

    Returns:
        List[Dict]: 板块行情数据
    """
    if data_type == 'industry':
        results = fetch_industry_quote(source)
    elif data_type == 'concept':
        results = fetch_concept_quote(source)
    elif data_type == 'region':
        results = fetch_region_quote(source)
    else:
        logger.warning(f"未知的板块数据类型: {data_type}")
        results = []

    # 如果主数据源失败，尝试备选
    if not results and source == 'akshare' and data_type in ['industry', 'concept'] and HAS_HTTPX:
        fs_map = {'industry': 'm:90+t:2', 'concept': 'm:90+t:3'}
        logger.info("AKShare 板块无数据，尝试东方财富备选")
        results = _fetch_eastmoney_sector_quote(fs_map.get(data_type, 'm:90+t:2'))

    return results


def fetch_sector_list(data_type: str = 'industry') -> List[Dict[str, Any]]:
    """获取板块列表（统一入口）

    Args:
        data_type: 数据类型 (industry/concept/region)

    Returns:
        List[Dict]: 板块列表
    """
    if data_type == 'industry':
        return fetch_industry_list()
    elif data_type == 'concept':
        return fetch_concept_list()
    elif data_type == 'region':
        return fetch_region_list()
    else:
        logger.warning(f"未知的板块数据类型: {data_type}")
        return []


def fetch_sector_flow(data_type: str = 'industry') -> List[Dict[str, Any]]:
    """获取板块资金流向（统一入口）

    Args:
        data_type: 数据类型 (industry/concept)

    Returns:
        List[Dict]: 板块资金流向数据
    """
    return fetch_sector_capital_flow(data_type)


# ============== 便捷类 ==============

class SectorFetcher:
    """板块数据获取器"""

    def get_industry_quote(self) -> List[Dict[str, Any]]:
        """获取行业板块行情"""
        return fetch_industry_quote()

    def get_concept_quote(self) -> List[Dict[str, Any]]:
        """获取概念板块行情"""
        return fetch_concept_quote()

    def get_region_quote(self) -> List[Dict[str, Any]]:
        """获取地域板块行情"""
        return fetch_region_quote()

    def get_industry_list(self) -> List[Dict[str, Any]]:
        """获取行业板块列表"""
        return fetch_industry_list()

    def get_concept_list(self) -> List[Dict[str, Any]]:
        """获取概念板块列表"""
        return fetch_concept_list()

    def get_region_list(self) -> List[Dict[str, Any]]:
        """获取地域板块列表"""
        return fetch_region_list()

    def get_industry_flow(self) -> List[Dict[str, Any]]:
        """获取行业板块资金流向"""
        return fetch_sector_capital_flow('industry')

    def get_concept_flow(self) -> List[Dict[str, Any]]:
        """获取概念板块资金流向"""
        return fetch_sector_capital_flow('concept')

    def get_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取所有板块数据"""
        return {
            'industry_quote': self.get_industry_quote(),
            'concept_quote': self.get_concept_quote(),
            'region_quote': self.get_region_quote(),
            'industry_flow': self.get_industry_flow(),
            'concept_flow': self.get_concept_flow(),
        }


# 便捷实例
sector_fetcher = SectorFetcher()


if __name__ == '__main__':
    logger.info("测试板块数据抓取...")

    logger.info("\n1. 行业板块行情...")
    industry = fetch_industry_quote()
    for item in industry[:5]:
        logger.info(f"{item['sector_name']}: 涨跌幅={item['change_pct']}%")

    logger.info("\n2. 概念板块行情...")
    concept = fetch_concept_quote()
    for item in concept[:5]:
        logger.info(f"{item['sector_name']}: 涨跌幅={item['change_pct']}%")

    logger.info("\n3. 行业板块资金流向...")
    flow = fetch_sector_capital_flow('industry')
    for item in flow[:5]:
        logger.info(f"{item['sector_name']}: 主力净流入={item['main_inflow_net']}")
