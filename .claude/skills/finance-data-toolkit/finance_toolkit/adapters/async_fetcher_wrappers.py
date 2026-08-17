# -*- coding: utf-8 -*-
"""
多源适配器 - Async包装层

将同步fetcher包装为async，供MultiSourceAdapter使用。
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def tencent_quote_wrapper(query: str, data_type: str = 'quote', **kwargs) -> List[Dict]:
    """腾讯财经实时行情异步包装"""
    if data_type != 'quote':
        return []
    
    try:
        from ..data_fetching.tencent_fetcher import fetch_tencent_quote
        results = await asyncio.to_thread(fetch_tencent_quote, [query])
        return [r.payload for r in results if r.payload]
    except Exception as e:
        logger.warning(f"Tencent quote fetch failed: {e}")
        return []


async def tencent_kline_wrapper(query: str, data_type: str = 'kline', **kwargs) -> List[Dict]:
    """腾讯财经K线异步包装"""
    if data_type != 'kline':
        return []
    
    try:
        from ..data_fetching.tencent_fetcher import fetch_tencent_kline
        symbol = query.replace('.SH', '').replace('.SZ', '')
        period = kwargs.get('period', 'day')
        results = await asyncio.to_thread(fetch_tencent_kline, symbol, period=period)
        return results or []
    except Exception as e:
        logger.warning(f"Tencent kline fetch failed: {e}")
        return []


async def sina_quote_wrapper(query: str, data_type: str = 'quote', **kwargs) -> List[Dict]:
    """新浪财经实时行情异步包装"""
    if data_type != 'quote':
        return []
    
    try:
        from ..data_fetching.sina_kline_fetcher import fetch_sina_quote
        results = await asyncio.to_thread(fetch_sina_quote, [query])
        return [r.payload for r in results if r.payload]
    except Exception as e:
        logger.warning(f"Sina quote fetch failed: {e}")
        return []


async def eastmoney_quote_wrapper(query: str, data_type: str = 'quote', **kwargs) -> List[Dict]:
    """东方财富实时行情异步包装"""
    if data_type != 'quote':
        return []
    
    try:
        from ..data_fetching.realtime_fetcher import fetch_realtime_quotes
        results = await asyncio.to_thread(fetch_realtime_quotes, [query])
        return [r.payload for r in results if r.payload]
    except Exception as e:
        logger.warning(f"EastMoney quote fetch failed: {e}")
        return []


def create_async_wrappers() -> Dict[str, Any]:
    """创建所有async包装函数映射"""
    return {
        'tencent': {
            'quote': tencent_quote_wrapper,
            'kline': tencent_kline_wrapper,
        },
        'sina': {
            'quote': sina_quote_wrapper,
        },
        'eastmoney': {
            'quote': eastmoney_quote_wrapper,
        },
    }


if __name__ == '__main__':
    async def test():
        wrappers = create_async_wrappers()
        result = await wrappers['tencent']['quote']('600000.SH')
        print(f"Tencent result: {result[:1] if result else 'empty'}")
    
    asyncio.run(test())
