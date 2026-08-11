# -*- coding: utf-8 -*-
"""
大宗交易数据抓取器
支持：大宗交易明细、板块排行、营业部排行
数据源：AKShare
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

logger = logging.getLogger(__name__)


# ============== 数据模型 ==============

class BlockTrade:
    """大宗交易数据"""
    def __init__(self, trade_date: str, stock_code: str, stock_name: str,
                 price: float, premium_discount: float,
                 volume: int, amount: float,
                 buyer: str, seller: str,
                 source: str = 'akshare'):
        self.trade_date = trade_date
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.price = price
        self.premium_discount = premium_discount
        self.volume = volume
        self.amount = amount
        self.buyer = buyer
        self.seller = seller
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'trade_date': self.trade_date,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'price': self.price,
            'premium_discount': self.premium_discount,
            'volume': self.volume,
            'amount': self.amount,
            'buyer': self.buyer,
            'seller': self.seller,
            'source': self.source,
            'timestamp': self.timestamp,
        }


class BlockTradeSeat:
    """营业部排行数据"""
    def __init__(self, rank: int, seat_name: str,
                 buy_count: int, sell_count: int,
                 buy_amount: float, sell_amount: float,
                 net_amount: float,
                 source: str = 'akshare'):
        self.rank = rank
        self.seat_name = seat_name
        self.buy_count = buy_count
        self.sell_count = sell_count
        self.buy_amount = buy_amount
        self.sell_amount = sell_amount
        self.net_amount = net_amount
        self.source = source
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'rank': self.rank,
            'seat_name': self.seat_name,
            'buy_count': self.buy_count,
            'sell_count': self.sell_count,
            'buy_amount': self.buy_amount,
            'sell_amount': self.sell_amount,
            'net_amount': self.net_amount,
            'source': self.source,
            'timestamp': self.timestamp,
        }


# ============== 大宗交易明细 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_block_trade_detail():
    """内部函数：获取大宗交易明细（带重试）"""
    return ak.stock_dzjy_mrmx()


def fetch_block_trade_detail() -> List[Dict[str, Any]]:
    """获取大宗交易明细数据

    Returns:
        List[Dict]: 大宗交易明细数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = _fetch_akshare_block_trade_detail()
            for _, row in df.iterrows():
                results.append(BlockTrade(
                    trade_date=str(row.get('交易日期', '')),
                    stock_code=str(row.get('代码', '')),
                    stock_name=str(row.get('名称', '')),
                    price=float(row.get('成交价', 0) or 0),
                    premium_discount=float(row.get('溢价率', 0) or 0),
                    volume=int(row.get('成交量', 0) or 0),
                    amount=float(row.get('成交额', 0) or 0),
                    buyer=str(row.get('买方营业部', '')),
                    seller=str(row.get('卖方营业部', '')),
                    source='akshare'
                ).to_dict())
        except Exception as e:
            logger.error(f"大宗交易明细获取失败: {e}")

    return results


# ============== 大宗交易板块 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_block_trade_sector():
    """内部函数：获取大宗交易板块（带重试）"""
    return ak.stock_dzjy_mrmk()


def fetch_block_trade_sector() -> List[Dict[str, Any]]:
    """获取大宗交易板块数据

    Returns:
        List[Dict]: 大宗交易板块数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = _fetch_akshare_block_trade_sector()
            for _, row in df.iterrows():
                results.append({
                    'trade_date': str(row.get('交易日期', '')),
                    'stock_code': str(row.get('代码', '')),
                    'stock_name': str(row.get('名称', '')),
                    'price': float(row.get('成交价', 0) or 0),
                    'premium_discount': float(row.get('溢价率', 0) or 0),
                    'volume': int(row.get('成交量', 0) or 0),
                    'amount': float(row.get('成交额', 0) or 0),
                    'source': 'akshare',
                    'timestamp': datetime.utcnow().isoformat(),
                })
        except Exception as e:
            logger.error(f"大宗交易板块获取失败: {e}")

    return results


# ============== 营业部排行 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_block_trade_seat():
    """内部函数：获取营业部排行（带重试）"""
    return ak.stock_dzjy_yybph()


def fetch_block_trade_seat() -> List[Dict[str, Any]]:
    """获取营业部排行数据

    Returns:
        List[Dict]: 营业部排行数据列表
    """
    results = []

    if HAS_AKSHARE:
        try:
            df = _fetch_akshare_block_trade_seat()
            for idx, row in df.iterrows():
                results.append(BlockTradeSeat(
                    rank=int(idx) + 1,
                    seat_name=str(row.get('营业部名称', '')),
                    buy_count=int(row.get('买入次数', 0) or 0),
                    sell_count=int(row.get('卖出次数', 0) or 0),
                    buy_amount=float(row.get('买入金额', 0) or 0),
                    sell_amount=float(row.get('卖出金额', 0) or 0),
                    net_amount=float(row.get('净额', 0) or 0),
                    source='akshare'
                ).to_dict())
        except Exception as e:
            logger.error(f"营业部排行获取失败: {e}")

    return results


# ============== 便捷函数 ==============

def fetch_block_trade_data(data_type: str = 'detail') -> List[Dict[str, Any]]:
    """获取大宗交易数据（统一入口）

    Args:
        data_type: 数据类型 (detail/sector/seat)

    Returns:
        List[Dict]: 大宗交易数据
    """
    if data_type == 'detail':
        return fetch_block_trade_detail()
    elif data_type == 'sector':
        return fetch_block_trade_sector()
    elif data_type == 'seat':
        return fetch_block_trade_seat()
    else:
        logger.warning(f"未知的大宗交易数据类型: {data_type}")
        return []


# ============== 便捷类 ==============

class BlockTradeFetcher:
    """大宗交易数据获取器"""

    def get_detail(self) -> List[Dict[str, Any]]:
        """获取大宗交易明细"""
        return fetch_block_trade_detail()

    def get_sector(self) -> List[Dict[str, Any]]:
        """获取大宗交易板块"""
        return fetch_block_trade_sector()

    def get_seat(self) -> List[Dict[str, Any]]:
        """获取营业部排行"""
        return fetch_block_trade_seat()

    def get_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取所有大宗交易数据"""
        return {
            'detail': self.get_detail(),
            'sector': self.get_sector(),
            'seat': self.get_seat(),
        }


# 便捷实例
block_trade_fetcher = BlockTradeFetcher()


if __name__ == '__main__':
    logger.info("测试大宗交易数据抓取...")

    logger.info("\n1. 大宗交易明细...")
    detail = fetch_block_trade_detail()
    for item in detail[:5]:
        logger.info(f"{item['stock_name']}: 成交价={item['price']}, 溢价率={item['premium_discount']}%")

    logger.info("\n2. 营业部排行...")
    seat = fetch_block_trade_seat()
    for item in seat[:5]:
        logger.info(f"{item['seat_name']}: 买入金额={item['buy_amount']}")
