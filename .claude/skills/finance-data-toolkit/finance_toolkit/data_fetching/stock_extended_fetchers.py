# -*- coding: utf-8 -*-
"""
股票扩展数据抓取模块
支持：大宗交易、股东数据、龙虎榜深度数据
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from ..resilience import retry_with_backoff

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

logger = logging.getLogger(__name__)


# ============== 大宗交易数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_block_trade(start_date: str, end_date: str):
    """内部函数：获取 AKShare 大宗交易明细（带重试）"""
    return ak.stock_dzjy_mrmx(start_date=start_date, end_date=end_date)


def fetch_block_trade(
    start_date: str = None,
    end_date: str = None,
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取大宗交易数据

    Args:
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)
        source: 数据源

    Returns:
        List[Dict]: 大宗交易数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            if end_date is None:
                end_date = datetime.now().strftime('%Y%m%d')
            if start_date is None:
                start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

            df = _fetch_akshare_block_trade(start_date, end_date)

            for _, row in df.iterrows():
                results.append({
                    'trade_date': row.get('成交日期', ''),
                    'stock_code': row.get('股票代码', ''),
                    'stock_name': row.get('股票名称', ''),
                    'price': float(row.get('成交价', 0) or 0),
                    'premium_discount': float(row.get('溢价率', 0) or 0),
                    'volume': int(row.get('成交量', 0) or 0),
                    'amount': float(row.get('成交额', 0) or 0),
                    'buyer_seat': row.get('买方营业部', ''),
                    'seller_seat': row.get('卖方营业部', ''),
                    'source': 'akshare',
                    'timestamp': datetime.utcnow().isoformat(),
                })
        except Exception as e:
            logger.error(f"大宗交易数据获取失败: {e}")

    return results


# ============== 股东数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_shareholder_count():
    """内部函数：获取 AKShare 股东人数（带重试）"""
    return ak.stock_zh_a_gdhs()


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_shareholder_detail(symbol: str):
    """内部函数：获取 AKShare 十大股东详情（带重试）"""
    return ak.stock_zh_a_gdhs_detail_em(symbol=symbol)


def fetch_shareholder_data(
    symbol: str = None,
    data_type: str = 'count',
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取股东数据

    Args:
        symbol: 股票代码（可选，如不提供则获取全市场）
        data_type: 数据类型 (count/institution/top10)
        source: 数据源

    Returns:
        List[Dict]: 股东数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            if data_type == 'count':
                # 股东人数
                df = _fetch_akshare_shareholder_count()
                for _, row in df.iterrows():
                    results.append({
                        'stock_code': row.get('代码', ''),
                        'stock_name': row.get('名称', ''),
                        'report_date': row.get('报告期', ''),
                        'shareholder_count': int(row.get('股东人数', 0) or 0),
                        'change_pct': float(row.get('较上期变化', 0) or 0),
                        'avg_holding': float(row.get('户均持股数量', 0) or 0),
                        'source': 'akshare',
                        'timestamp': datetime.utcnow().isoformat(),
                    })

            elif data_type == 'top10':
                if symbol:
                    # 十大股东详情
                    df = _fetch_akshare_shareholder_detail(symbol)
                    for _, row in df.iterrows():
                        results.append({
                            'stock_code': symbol,
                            'report_date': row.get('报告期', ''),
                            'rank': int(row.get('排名', 0) or 0),
                            'shareholder_name': row.get('股东名称', ''),
                            'hold_shares': float(row.get('持股数量', 0) or 0),
                            'hold_ratio': float(row.get('占流通股比例', 0) or 0),
                            'change': row.get('增减变动', ''),
                            'source': 'akshare',
                            'timestamp': datetime.utcnow().isoformat(),
                        })
                else:
                    logger.warning("top10 数据类型需要提供 symbol 参数")

            elif data_type == 'institution':
                # 机构持仓数据（从十大股东数据中提取）
                if symbol:
                    df = _fetch_akshare_shareholder_detail(symbol)
                    # 统计机构持仓
                    institution_count = 0
                    total_hold_ratio = 0.0
                    for _, row in df.iterrows():
                        name = row.get('股东名称', '')
                        if any(kw in name for kw in ['基金', '保险', '社保', 'QFII', '机构', '信托', '券商']):
                            institution_count += 1
                            total_hold_ratio += float(row.get('占流通股比例', 0) or 0)

                    if institution_count > 0:
                        results.append({
                            'stock_code': symbol,
                            'report_date': df.iloc[0].get('报告期', '') if not df.empty else '',
                            'institution_count': institution_count,
                            'hold_ratio': total_hold_ratio,
                            'source': 'akshare',
                            'timestamp': datetime.utcnow().isoformat(),
                        })
                else:
                    logger.warning("institution 数据类型需要提供 symbol 参数")

        except Exception as e:
            logger.error(f"股东数据获取失败: {e}")

    return results


# ============== 龙虎榜深度数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_lhb_seat():
    """内部函数：获取 AKShare 龙虎榜营业部排行（带重试）"""
    return ak.stock_lhb_yybph()


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_lhb_hot_money():
    """内部函数：获取 AKShare 龙虎榜活跃营业部（带重试）"""
    return ak.stock_lhb_hyyt_em()


def fetch_lhb_depth(
    symbol: str = None,
    start_date: str = None,
    end_date: str = None,
    data_type: str = 'detail',
    source: str = 'akshare'
) -> List[Dict[str, Any]]:
    """获取龙虎榜深度数据

    Args:
        symbol: 股票代码（可选）
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)
        data_type: 数据类型 (detail/seat/hot_money)
        source: 数据源

    Returns:
        List[Dict]: 龙虎榜深度数据列表
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            if data_type == 'detail':
                # 龙虎榜详情（已有实现，这里增强字段）
                if end_date is None:
                    end_date = datetime.now().strftime('%Y%m%d')
                if start_date is None:
                    start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

                df = ak.stock_lhb_detail_em(start_date=start_date, end_date=end_date)

                for _, row in df.iterrows():
                    results.append({
                        'trade_date': row.get('龙虎榜日期', ''),
                        'stock_code': row.get('代码', ''),
                        'stock_name': row.get('名称', ''),
                        'explain': row.get('解释说明', ''),
                        'buy_seat': row.get('买入金额', ''),
                        'sell_seat': row.get('卖出金额', ''),
                        'net_buy': row.get('净买入', ''),
                        'source': 'akshare',
                        'timestamp': datetime.utcnow().isoformat(),
                    })

            elif data_type == 'seat':
                # 营业部排行
                df = _fetch_akshare_lhb_seat()
                for _, row in df.iterrows():
                    results.append({
                        'seat_name': row.get('营业部名称', ''),
                        'buy_amount': float(row.get('买入金额', 0) or 0),
                        'sell_amount': float(row.get('卖出金额', 0) or 0),
                        'net_amount': float(row.get('净买入', 0) or 0),
                        'buy_count': int(row.get('买入次数', 0) or 0),
                        'sell_count': int(row.get('卖出次数', 0) or 0),
                        'source': 'akshare',
                        'timestamp': datetime.utcnow().isoformat(),
                    })

            elif data_type == 'hot_money':
                # 活跃营业部
                df = _fetch_akshare_lhb_hot_money()
                for _, row in df.iterrows():
                    results.append({
                        'seat_name': row.get('营业部名称', ''),
                        'total_buy': float(row.get('累计买入', 0) or 0),
                        'total_sell': float(row.get('累计卖出', 0) or 0),
                        'net_buy': float(row.get('净买入', 0) or 0),
                        'recent_stocks': row.get('近期操作个股', '').split(',') if row.get('近期操作个股') else [],
                        'win_rate': float(row.get('胜率', 0) or 0),
                        'source': 'akshare',
                        'timestamp': datetime.utcnow().isoformat(),
                    })

        except Exception as e:
            logger.error(f"龙虎榜深度数据获取失败: {e}")

    return results


# ============== 便捷函数 ==============

def fetch_all_stock_extended(
    symbol: str = None,
    data_types: List[str] = None,
    source: str = 'akshare'
) -> Dict[str, List[Dict]]:
    """获取所有股票扩展数据

    Args:
        symbol: 股票代码（可选）
        data_types: 数据类型列表
        source: 数据源

    Returns:
        Dict: 按数据类型分组的結果
    """
    if data_types is None:
        data_types = ['block_trade', 'shareholder_count', 'shareholder_top10', 'lhb_detail', 'lhb_seat', 'lhb_hot_money']

    results = {}

    if 'block_trade' in data_types:
        results['block_trade'] = fetch_block_trade(source=source)

    if 'shareholder_count' in data_types:
        results['shareholder_count'] = fetch_shareholder_data(data_type='count', source=source)

    if 'shareholder_top10' in data_types and symbol:
        results['shareholder_top10'] = fetch_shareholder_data(symbol=symbol, data_type='top10', source=source)

    if 'shareholder_institution' in data_types and symbol:
        results['shareholder_institution'] = fetch_shareholder_data(symbol=symbol, data_type='institution', source=source)

    if 'lhb_detail' in data_types:
        results['lhb_detail'] = fetch_lhb_depth(data_type='detail', source=source)

    if 'lhb_seat' in data_types:
        results['lhb_seat'] = fetch_lhb_depth(data_type='seat', source=source)

    if 'lhb_hot_money' in data_types:
        results['lhb_hot_money'] = fetch_lhb_depth(data_type='hot_money', source=source)

    return results
