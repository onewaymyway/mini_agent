# -*- coding: utf-8 -*-
"""
凤凰财经数据抓取器
提供股票实时行情和财经新闻数据
"""

import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from ..core import FinanceData

logger = logging.getLogger(__name__)


def fetch_fenghuang_quote(symbols: List[str], source: str = 'fenghuang') -> List[FinanceData]:
    """获取凤凰财经股票实时行情
    
    Args:
        symbols: 股票代码列表，如 ['600000', '000001']
        source: 数据源标识
    
    Returns:
        List[FinanceData]: 股票行情数据列表
    """
    results = []
    
    if not HAS_HTTPX:
        logger.warning("httpx 未安装，无法获取凤凰财经数据")
        return results
    
    try:
        async def _fetch():
            async with httpx.AsyncClient(timeout=15) as client:
                # 凤凰财经使用腾讯行情接口
                # 上海(6xxxxx): sh, 深圳(0xxxxx): sz
                codes = []
                for s in symbols:
                    if s.startswith('6'):
                        codes.append(f"sh{s}")
                    elif s.startswith('0'):
                        codes.append(f"sz{s}")
                    else:
                        codes.append(s)
                url = f"https://qt.gtimg.cn/q={','.join(codes)}"
                
                resp = await client.get(url, timeout=15)
                resp.encoding = 'gbk'
                
                for line in resp.text.strip().split(';'):
                    if '=' not in line:
                        continue
                    
                    var_part, data_part = line.split('=', 1)
                    code = var_part.strip().replace('var hq_str_', '').replace('"', '')
                    data_str = data_part.strip().strip('"')
                    
                    if not data_str:
                        continue
                    
                    fields = data_str.split('~')
                    if len(fields) < 35:
                        continue
                    
                    try:
                        payload = {
                            'name': fields[1] if len(fields) > 1 else '',
                            'code': fields[2] if len(fields) > 2 else code,
                            'price': float(fields[3]) if fields[3] else 0.0,
                            'pre_close': float(fields[4]) if len(fields) > 4 and fields[4] else 0.0,
                            'open': float(fields[5]) if len(fields) > 5 and fields[5] else 0.0,
                            'high': float(fields[6]) if len(fields) > 6 and fields[6] else 0.0,
                            'low': float(fields[7]) if len(fields) > 7 and fields[7] else 0.0,
                            'volume': int(float(fields[8])) if len(fields) > 8 and fields[8] else 0,
                            'amount': float(fields[9]) if len(fields) > 9 and fields[9] else 0.0,
                            'change_pct': float(fields[30]) if len(fields) > 30 and fields[30] else 0.0,
                            'change_amt': float(fields[31]) if len(fields) > 31 and fields[31] else 0.0,
                        }
                        
                        results.append(FinanceData(
                            source='fenghuang',
                            data_type='quote',
                            symbol=code,
                            timestamp=datetime.utcnow().isoformat(),
                            payload=payload
                        ))
                    except (ValueError, IndexError) as e:
                        logger.warning(f"凤凰财经数据解析失败 {code}: {e}")
                        continue
                
                return results
        
        import asyncio
        results = asyncio.run(_fetch())
        
    except Exception as e:
        logger.error(f"凤凰财经行情获取失败: {e}")
    
    return results


def fetch_fenghuang_news(page: int = 1, page_size: int = 20) -> List[FinanceData]:
    """获取凤凰财经新闻列表
    
    Args:
        page: 页码
        page_size: 每页数量
    
    Returns:
        List[FinanceData]: 新闻数据列表
    """
    results = []
    
    if not HAS_HTTPX:
        logger.warning("httpx 未安装，无法获取凤凰财经新闻")
        return results
    
    try:
        async def _fetch():
            async with httpx.AsyncClient(timeout=15) as client:
                url = "https://api.fc.qq.com/lapi/newspage/getNewsList"
                params = {
                    'page': page,
                    'pagesize': page_size,
                    'channel': 'finance',
                    'type': 'hot'
                }
                
                resp = await client.get(url, params=params, timeout=15)
                data = resp.json()
                
                if data.get('code') == 0 and data.get('data'):
                    news_list = data['data'].get('list', [])
                    for item in news_list:
                        payload = {
                            'title': item.get('title', ''),
                            'url': item.get('url', ''),
                            'source': item.get('source', '凤凰财经'),
                            'time': item.get('ctime', ''),
                            'digest': item.get('digest', ''),
                        }
                        
                        results.append(FinanceData(
                            source='fenghuang',
                            data_type='news',
                            symbol='*',
                            timestamp=datetime.utcnow().isoformat(),
                            payload=payload
                        ))
                
                return results
        
        import asyncio
        results = asyncio.run(_fetch())
        
    except Exception as e:
        logger.error(f"凤凰财经新闻获取失败: {e}")
    
    return results


def fetch_fenghuang_hot_news() -> List[FinanceData]:
    """获取凤凰财经热门新闻"""
    return fetch_fenghuang_news(page=1, page_size=30)


class FenghuangFetcher:
    """凤凰财经数据获取器"""
    
    def get_quote(self, symbols: List[str]) -> List[FinanceData]:
        """获取股票行情"""
        return fetch_fenghuang_quote(symbols)
    
    def get_news(self, page: int = 1, page_size: int = 20) -> List[FinanceData]:
        """获取新闻"""
        return fetch_fenghuang_news(page, page_size)
    
    def get_hot_news(self) -> List[FinanceData]:
        """获取热门新闻"""
        return fetch_fenghuang_hot_news()


# 便捷实例
fenghuang_fetcher = FenghuangFetcher()


if __name__ == '__main__':
    logger.info("测试凤凰财经股票行情...")
    quotes = fetch_fenghuang_quote(['600000', '000001'])
    for q in quotes:
        logger.info(f"{q.symbol}: {q.payload.get('name')} - {q.payload.get('price')}")
    
    logger.info("\n测试凤凰财经新闻...")
    news = fetch_fenghuang_hot_news()
    for n in news[:5]:
        logger.info(f"{n.payload.get('title')[:30]}...")
