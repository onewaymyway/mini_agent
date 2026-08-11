# -*- coding: utf-8 -*-
"""
财经新闻数据抓取器
支持：财经新闻、股票新闻、热点新闻
数据源：凤凰财经、新浪财经、东方财富
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from ..resilience import retry_with_backoff

logger = logging.getLogger(__name__)


# ============== 数据模型 ==============

class NewsItem:
    """新闻数据"""
    def __init__(self, title: str, url: str, source: str,
                 publish_time: str, content: str = '',
                 tags: List[str] = None,
                 sentiment: str = 'neutral'):
        self.title = title
        self.url = url
        self.source = source
        self.publish_time = publish_time
        self.content = content
        self.tags = tags or []
        self.sentiment = sentiment
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'title': self.title,
            'url': self.url,
            'source': self.source,
            'publish_time': self.publish_time,
            'content': self.content,
            'tags': self.tags,
            'sentiment': self.sentiment,
            'timestamp': self.timestamp,
        }


# ============== 凤凰财经新闻 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_fenghuang_news(page: int = 1, page_size: int = 20):
    """内部函数：获取凤凰财经新闻（带重试）"""
    if not HAS_HTTPX:
        raise ImportError("httpx 未安装")
    
    url = "https://api.fc.qq.com/lapi/newspage/getNewsList"
    params = {
        'page': page,
        'pagesize': page_size,
        'channel': 'finance',
        'type': 'hot'
    }
    
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params=params)
        data = resp.json()
        return data


def fetch_fenghuang_news(page: int = 1, page_size: int = 20) -> List[Dict[str, Any]]:
    """获取凤凰财经新闻

    Args:
        page: 页码
        page_size: 每页数量

    Returns:
        List[Dict]: 新闻数据列表
    """
    results = []

    if not HAS_HTTPX:
        logger.warning("httpx 未安装，无法获取凤凰财经新闻")
        return results

    try:
        data = _fetch_fenghuang_news(page, page_size)
        
        if data.get('code') == 0 and data.get('data'):
            news_list = data['data'].get('list', [])
            for item in news_list:
                results.append(NewsItem(
                    title=item.get('title', ''),
                    url=item.get('url', ''),
                    source='fenghuang',
                    publish_time=item.get('ctime', ''),
                    content=item.get('digest', ''),
                    tags=[],
                    sentiment='neutral'
                ).to_dict())
    except Exception as e:
        logger.error(f"凤凰财经新闻获取失败: {e}")

    return results


def fetch_fenghuang_hot_news() -> List[Dict[str, Any]]:
    """获取凤凰财经热门新闻"""
    return fetch_fenghuang_news(page=1, page_size=30)


# ============== 新浪财经新闻 ==============

def fetch_sina_news(category: str = 'finance', page: int = 1) -> List[Dict[str, Any]]:
    """获取新浪财经新闻

    Args:
        category: 新闻分类 (finance/stock/money)
        page: 页码

    Returns:
        List[Dict]: 新闻数据列表
    """
    results = []

    if not HAS_HTTPX:
        logger.warning("httpx 未安装，无法获取新浪新闻")
        return results

    try:
        url = f"https://feed.mix.sina.com.cn/api/roll/get"
        params = {
            'pageid': '153',
            'lid': '2506',
            'num': '20',
            'versionNumber': '1.2.8',
            'page': page,
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.sina.com.cn/',
        }
        
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params=params, headers=headers)
            data = resp.json()
            
            if data.get('result') and data['result'].get('data'):
                for item in data['result']['data']:
                    results.append(NewsItem(
                        title=item.get('title', ''),
                        url=item.get('url', ''),
                        source='sina',
                        publish_time=item.get('ctime', ''),
                        content=item.get('intro', ''),
                        tags=[],
                        sentiment='neutral'
                    ).to_dict())
    except Exception as e:
        logger.error(f"新浪新闻获取失败: {e}")

    return results


# ============== 东方财富新闻 ==============

def fetch_eastmoney_news(channel: str = 'finance', page: int = 1) -> List[Dict[str, Any]]:
    """获取东方财富新闻

    Args:
        channel: 频道 (finance/stock/money)
        page: 页码

    Returns:
        List[Dict]: 新闻数据列表
    """
    results = []

    if not HAS_HTTPX:
        logger.warning("httpx 未安装，无法获取东财新闻")
        return results

    try:
        url = "https://np-listapi.eastmoney.com/comm/wap/getListInfo"
        params = {
            'cb': 'jQuery',
            'type': '0',
            'client': 'web',
            'channel': channel,
            'page_index': page,
            'page_size': 20,
        }
        
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params=params)
            # 去除 jQuery 回调包装
            text = resp.text.strip()
            if text.startswith('jQuery'):
                text = text[text.index('(') + 1:text.rindex(')')]
            data = eval(text)  # 注意：仅用于解析已知格式的回调数据
            
            if data.get('result') and data['result'].get('list'):
                for item in data['result']['list']:
                    results.append(NewsItem(
                        title=item.get('title', ''),
                        url=item.get('url', ''),
                        source='eastmoney',
                        publish_time=item.get('showtime', ''),
                        content=item.get('digest', ''),
                        tags=[],
                        sentiment='neutral'
                    ).to_dict())
    except Exception as e:
        logger.error(f"东方财富新闻获取失败: {e}")

    return results


# ============== 股票新闻 ==============

def fetch_stock_news(symbol: str, days: int = 7) -> List[Dict[str, Any]]:
    """获取个股相关新闻

    Args:
        symbol: 股票代码
        days: 最近天数

    Returns:
        List[Dict]: 新闻数据列表
    """
    results = []

    if not HAS_HTTPX:
        return results

    try:
        # 东方财富个股新闻
        url = "https://newsapi.eastmoney.com/kuaixun/v1/getList"
        params = {
            'symbol': symbol,
            'pageSize': 20,
            'pageNo': 1,
        }
        
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params=params)
            data = resp.json()
            
            if data.get('data') and data['data'].get('list'):
                for item in data['data']['list']:
                    results.append(NewsItem(
                        title=item.get('title', ''),
                        url=item.get('url', ''),
                        source='eastmoney',
                        publish_time=item.get('datetime', ''),
                        content=item.get('digest', ''),
                        tags=[symbol],
                        sentiment='neutral'
                    ).to_dict())
    except Exception as e:
        logger.error(f"{symbol} 新闻获取失败: {e}")

    return results


# ============== 便捷函数 ==============

def fetch_news(source: str = 'fenghuang', **kwargs) -> List[Dict[str, Any]]:
    """获取新闻数据（统一入口）

    Args:
        source: 数据源 (fenghuang/sina/eastmoney)
        **kwargs: 其他参数

    Returns:
        List[Dict]: 新闻数据列表
    """
    if source == 'fenghuang':
        return fetch_fenghuang_news(**kwargs)
    elif source == 'sina':
        return fetch_sina_news(**kwargs)
    elif source == 'eastmoney':
        return fetch_eastmoney_news(**kwargs)
    else:
        logger.warning(f"未知的新闻数据源: {source}")
        return []


# ============== 便捷类 ==============

class NewsFetcher:
    """新闻数据获取器"""

    def get_fenghuang_news(self, page: int = 1, page_size: int = 20) -> List[Dict[str, Any]]:
        """获取凤凰财经新闻"""
        return fetch_fenghuang_news(page, page_size)

    def get_fenghuang_hot_news(self) -> List[Dict[str, Any]]:
        """获取凤凰财经热门新闻"""
        return fetch_fenghuang_hot_news()

    def get_sina_news(self, category: str = 'finance', page: int = 1) -> List[Dict[str, Any]]:
        """获取新浪新闻"""
        return fetch_sina_news(category, page)

    def get_eastmoney_news(self, channel: str = 'finance', page: int = 1) -> List[Dict[str, Any]]:
        """获取东方财富新闻"""
        return fetch_eastmoney_news(channel, page)

    def get_stock_news(self, symbol: str, days: int = 7) -> List[Dict[str, Any]]:
        """获取个股新闻"""
        return fetch_stock_news(symbol, days)

    def get_all_news(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取所有新闻"""
        return {
            'fenghuang': self.get_fenghuang_hot_news(),
            'sina': self.get_sina_news(),
            'eastmoney': self.get_eastmoney_news(),
        }


# 便捷实例
news_fetcher = NewsFetcher()


if __name__ == '__main__':
    logger.info("测试新闻数据抓取...")

    logger.info("\n1. 凤凰财经新闻...")
    news = fetch_fenghuang_hot_news()
    for item in news[:5]:
        logger.info(f"{item['title'][:30]}...")

    logger.info("\n2. 新浪新闻...")
    sina_news = fetch_sina_news()
    for item in sina_news[:5]:
        logger.info(f"{item['title'][:30]}...")
