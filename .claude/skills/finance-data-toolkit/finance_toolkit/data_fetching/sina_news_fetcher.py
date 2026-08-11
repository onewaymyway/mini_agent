# -*- coding: utf-8 -*-
"""
新浪财经新闻抓取器
提供财经新闻、股票新闻、宏观新闻等数据
"""

import json
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from ..core import FinanceData

logger = logging.getLogger(__name__)

# 新闻频道映射
NEWS_CHANNELS = {
    'stock': 'stock',
    'finance': 'finance',
    'macro': 'macro',
    'industry': 'industry',
    'company': 'company',
    'fund': 'fund',
}


def fetch_sina_news(channel: str = 'finance', page: int = 1, page_size: int = 20) -> List[FinanceData]:
    """获取新浪财经新闻列表
    
    Args:
        channel: 新闻频道 (stock/finance/macro/industry/company/fund)
        page: 页码
        page_size: 每页数量
    
    Returns:
        List[FinanceData]: 新闻数据列表
    """
    results = []
    
    if not HAS_HTTPX:
        logger.warning("httpx 未安装，无法获取新浪新闻")
        return results
    
    try:
        async def _fetch():
            async with httpx.AsyncClient(timeout=20) as client:
                # 新浪新闻API
                url = "https://feed.mix.sina.com.cn/api/roll/news"
                params = {
                    'pageid': '153',
                    'lid': NEWS_CHANNELS.get(channel, 'finance'),
                    'num': page_size,
                    'version': '8759',
                    'pagenum': page,
                    'reqtime': int(datetime.now().timestamp() * 1000),
                    'callback': 'callback'
                }
                
                resp = await client.get(url, params=params, timeout=20)
                
                # 解析JSONP响应
                text = resp.text
                if 'callback(' in text:
                    json_str = text.split('callback(', 1)[1].rstrip(');')
                else:
                    json_str = text
                
                data = json.loads(json_str)
                
                if data.get('result') and data['result'].get('data'):
                    news_list = data['result']['data']
                    for item in news_list:
                        # 提取股票代码
                        symbols = re.findall(r'(\d{6})', item.get('title', ''))
                        
                        payload = {
                            'title': item.get('title', ''),
                            'url': item.get('url', ''),
                            'source': item.get('source', '新浪财经'),
                            'ctime': item.get('ctime', ''),
                            'intro': item.get('intro', ''),
                            'symbols': list(set(symbols)),
                            'channel': channel,
                        }
                        
                        results.append(FinanceData(
                            source='sina_news',
                            data_type='news',
                            symbol='*',
                            timestamp=datetime.utcnow().isoformat(),
                            payload=payload
                        ))
                
                return results
        
        import asyncio
        results = asyncio.run(_fetch())
        
    except Exception as e:
        logger.error(f"新浪新闻获取失败: {e}")
    
    return results


def fetch_sina_stock_news(symbol: str, page: int = 1, page_size: int = 20) -> List[FinanceData]:
    """获取特定股票的新闻
    
    Args:
        symbol: 股票代码
        page: 页码
        page_size: 每页数量
    
    Returns:
        List[FinanceData]: 新闻数据列表
    """
    results = []
    
    if not HAS_HTTPX:
        return results
    
    try:
        async def _fetch():
            async with httpx.AsyncClient(timeout=20) as client:
                # 新浪股票新闻API
                code = symbol.split('.')[0]
                url = f"https://feed.mix.sina.com.cn/api/roll/news"
                params = {
                    'pageid': '153',
                    'lid': 'stock',
                    'num': page_size,
                    'version': '8759',
                    'pagenum': page,
                    'keyword': code,
                    'reqtime': int(datetime.now().timestamp() * 1000)
                }
                
                resp = await client.get(url, params=params, timeout=20)
                text = resp.text
                
                if 'callback(' in text:
                    json_str = text.split('callback(', 1)[1].rstrip(');')
                else:
                    json_str = text
                
                data = json.loads(json_str)
                
                if data.get('result') and data['result'].get('data'):
                    for item in data['result']['data']:
                        payload = {
                            'title': item.get('title', ''),
                            'url': item.get('url', ''),
                            'source': item.get('source', '新浪财经'),
                            'ctime': item.get('ctime', ''),
                            'intro': item.get('intro', ''),
                            'symbol': symbol,
                        }
                        
                        results.append(FinanceData(
                            source='sina_news',
                            data_type='stock_news',
                            symbol=symbol,
                            timestamp=datetime.utcnow().isoformat(),
                            payload=payload
                        ))
                
                return results
        
        import asyncio
        results = asyncio.run(_fetch())
        
    except Exception as e:
        logger.error(f"新浪股票新闻获取失败: {e}")
    
    return results


def fetch_sina_hot_news() -> List[FinanceData]:
    """获取新浪财经热门新闻"""
    return fetch_sina_news(channel='finance', page=1, page_size=30)


class SinaNewsFetcher:
    """新浪财经新闻获取器"""
    
    def get_news(self, channel: str = 'finance', page: int = 1, page_size: int = 20) -> List[FinanceData]:
        """获取新闻"""
        return fetch_sina_news(channel, page, page_size)
    
    def get_stock_news(self, symbol: str, page: int = 1, page_size: int = 20) -> List[FinanceData]:
        """获取股票新闻"""
        return fetch_sina_stock_news(symbol, page, page_size)
    
    def get_hot_news(self) -> List[FinanceData]:
        """获取热门新闻"""
        return fetch_sina_hot_news()


# 便捷实例
sina_news_fetcher = SinaNewsFetcher()


if __name__ == '__main__':
    logger.info("测试新浪财经新闻...")
    news = fetch_sina_hot_news()
    for n in news[:5]:
        logger.info(f"{n.payload.get('title')[:40]}...")
    
    logger.info("\n测试新浪股票新闻...")
    stock_news = fetch_sina_stock_news('600000.SH')
    for n in stock_news[:3]:
        logger.info(f"{n.payload.get('title')[:40]}...")
