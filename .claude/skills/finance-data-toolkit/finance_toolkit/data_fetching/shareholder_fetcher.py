# -*- coding: utf-8 -*-
"""
股东数据抓取器
支持：股东人数、机构持仓、十大流通股东
数据源：AKShare、东方财富
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

class ShareholderCount:
    """股东人数数据"""
    def __init__(self, stock_code: str, report_date: str,
                 shareholder_count: int, change_pct: float,
                 avg_holding: float, source: str = 'akshare'):
        self.stock_code = stock_code
        self.report_date = report_date
        self.shareholder_count = shareholder_count
        self.change_pct = change_pct
        self.avg_holding = avg_holding
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'stock_code': self.stock_code,
            'report_date': self.report_date,
            'shareholder_count': self.shareholder_count,
            'change_pct': self.change_pct,
            'avg_holding': self.avg_holding,
            'source': self.source,
            'timestamp': self.timestamp,
        }


class InstitutionHold:
    """机构持仓数据"""
    def __init__(self, stock_code: str, report_date: str,
                 institution_count: int, hold_ratio: float,
                 hold_amount: float, source: str = 'akshare'):
        self.stock_code = stock_code
        self.report_date = report_date
        self.institution_count = institution_count
        self.hold_ratio = hold_ratio
        self.hold_amount = hold_amount
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'stock_code': self.stock_code,
            'report_date': self.report_date,
            'institution_count': self.institution_count,
            'hold_ratio': self.hold_ratio,
            'hold_amount': self.hold_amount,
            'source': self.source,
            'timestamp': self.timestamp,
        }


class Top10Shareholder:
    """十大流通股东数据"""
    def __init__(self, stock_code: str, report_date: str,
                 rank: int, shareholder_name: str,
                 hold_shares: float, hold_ratio: float,
                 change: str, source: str = 'akshare'):
        self.stock_code = stock_code
        self.report_date = report_date
        self.rank = rank
        self.shareholder_name = shareholder_name
        self.hold_shares = hold_shares
        self.hold_ratio = hold_ratio
        self.change = change
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'stock_code': self.stock_code,
            'report_date': self.report_date,
            'rank': self.rank,
            'shareholder_name': self.shareholder_name,
            'hold_shares': self.hold_shares,
            'hold_ratio': self.hold_ratio,
            'change': self.change,
            'source': self.source,
            'timestamp': self.timestamp,
        }


# ============== 股东人数数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_shareholder_count():
    """内部函数：获取股东人数（带重试）"""
    return ak.stock_zh_a_gdhs()


def fetch_shareholder_count() -> List[Dict[str, Any]]:
    """获取股东人数数据

    Returns:
        List[Dict]: 股东人数数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = _fetch_akshare_shareholder_count()
            for _, row in df.iterrows():
                results.append(ShareholderCount(
                    stock_code=str(row.get('代码', '')),
                    report_date=str(row.get('报告期', '')),
                    shareholder_count=int(row.get('股东人数', 0) or 0),
                    change_pct=float(row.get('较上期变化', 0) or 0),
                    avg_holding=float(row.get('户均持股数', 0) or 0),
                    source='akshare'
                ).to_dict())
        except Exception as e:
            logger.error(f"股东人数获取失败: {e}")

    return results


# ============== 机构持仓数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_institution_hold():
    """内部函数：获取机构持仓（带重试）"""
    return ak.stock_zh_a_gdhs_detail_em()


def fetch_institution_hold() -> List[Dict[str, Any]]:
    """获取机构持仓数据

    Returns:
        List[Dict]: 机构持仓数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = _fetch_akshare_institution_hold()
            for _, row in df.iterrows():
                results.append(InstitutionHold(
                    stock_code=str(row.get('代码', '')),
                    report_date=str(row.get('报告期', '')),
                    institution_count=int(row.get('机构家数', 0) or 0),
                    hold_ratio=float(row.get('机构持股比例', 0) or 0),
                    hold_amount=float(row.get('机构持仓金额', 0) or 0),
                    source='akshare'
                ).to_dict())
        except Exception as e:
            logger.error(f"机构持仓获取失败: {e}")

    return results


# ============== 十大流通股东 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_top10_shareholder():
    """内部函数：获取十大流通股东（带重试）"""
    return ak.stock_zh_a_gdhs_detail_em()


def fetch_top10_shareholder() -> List[Dict[str, Any]]:
    """获取十大流通股东数据

    Returns:
        List[Dict]: 十大流通股东数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = _fetch_akshare_top10_shareholder()
            for _, row in df.iterrows():
                results.append(Top10Shareholder(
                    stock_code=str(row.get('代码', '')),
                    report_date=str(row.get('报告期', '')),
                    rank=int(row.get('排名', 0) or 0),
                    shareholder_name=str(row.get('股东名称', '')),
                    hold_shares=float(row.get('持股数', 0) or 0),
                    hold_ratio=float(row.get('持股比例', 0) or 0),
                    change=str(row.get('增减', '')),
                    source='akshare'
                ).to_dict())
        except Exception as e:
            logger.error(f"十大流通股东获取失败: {e}")

    return results


# ============== 个股股东数据 ==============

def fetch_stock_shareholder(symbol: str) -> Dict[str, Any]:
    """获取个股股东数据

    Args:
        symbol: 股票代码

    Returns:
        Dict: 包含股东人数、机构持仓、十大股东的综合数据
    """
    result = {
        'symbol': symbol,
        'timestamp': datetime.utcnow().isoformat(),
    }

    if HAS_AKSHARE:
        try:
            # 股东人数
            df = ak.stock_zh_a_gdhs(symbol=symbol)
            if df is not None and not df.empty:
                result['shareholder_count'] = df.to_dict('records')
        except Exception as e:
            logger.warning(f"{symbol} 股东人数获取失败: {e}")

        try:
            # 机构持仓
            df = ak.stock_zh_a_gdhs_detail_em(symbol=symbol)
            if df is not None and not df.empty:
                result['institution_hold'] = df.to_dict('records')
        except Exception as e:
            logger.warning(f"{symbol} 机构持仓获取失败: {e}")

    return result


# ============== 便捷函数 ==============

def fetch_shareholder_data(data_type: str = 'count') -> List[Dict[str, Any]]:
    """获取股东数据（统一入口）

    Args:
        data_type: 数据类型 (count/institution/top10)

    Returns:
        List[Dict]: 股东数据
    """
    if data_type == 'count':
        return fetch_shareholder_count()
    elif data_type == 'institution':
        return fetch_institution_hold()
    elif data_type == 'top10':
        return fetch_top10_shareholder()
    else:
        logger.warning(f"未知的股东数据类型: {data_type}")
        return []


# ============== 便捷类 ==============

class ShareholderFetcher:
    """股东数据获取器"""

    def get_shareholder_count(self) -> List[Dict[str, Any]]:
        """获取股东人数"""
        return fetch_shareholder_count()

    def get_institution_hold(self) -> List[Dict[str, Any]]:
        """获取机构持仓"""
        return fetch_institution_hold()

    def get_top10_shareholder(self) -> List[Dict[str, Any]]:
        """获取十大流通股东"""
        return fetch_top10_shareholder()

    def get_stock_data(self, symbol: str) -> Dict[str, Any]:
        """获取个股股东数据"""
        return fetch_stock_shareholder(symbol)

    def get_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取所有股东数据"""
        return {
            'shareholder_count': self.get_shareholder_count(),
            'institution_hold': self.get_institution_hold(),
            'top10_shareholder': self.get_top10_shareholder(),
        }


# 便捷实例
shareholder_fetcher = ShareholderFetcher()


if __name__ == '__main__':
    logger.info("测试股东数据抓取...")

    logger.info("\n1. 股东人数...")
    count = fetch_shareholder_count()
    for item in count[:5]:
        logger.info(f"{item['stock_code']}: 股东人数={item['shareholder_count']}")

    logger.info("\n2. 机构持仓...")
    inst = fetch_institution_hold()
    for item in inst[:5]:
        logger.info(f"{item['stock_code']}: 机构持股比例={item['hold_ratio']}%")

    logger.info("\n3. 十大流通股东...")
    top10 = fetch_top10_shareholder()
    for item in top10[:5]:
        logger.info(f"{item['stock_code']}: {item['shareholder_name']} 持股={item['hold_ratio']}%")
