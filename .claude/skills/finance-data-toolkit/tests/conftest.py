"""
Pytest configuration and shared fixtures
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_httpx_response():
    """Create a mock httpx response"""
    def _make_response(json_data=None, text="", status_code=200):
        response = MagicMock()
        response.json.return_value = json_data or {}
        response.text = text
        response.status_code = status_code
        response.raise_for_status = MagicMock()
        if status_code >= 400:
            response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        return response
    return _make_response


@pytest.fixture
def mock_async_client(mock_httpx_response):
    """Create a mock async httpx client"""
    client = AsyncMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def sample_akshare_quote():
    """Sample AKShare realtime quote data"""
    return {
        '代码': '600000',
        '名称': '浦发银行',
        '最新价': 10.50,
        '涨跌幅': 2.5,
        '涨跌额': 0.25,
        '成交量': 1000000,
        '成交额': 10500000,
        '今开': 10.30,
        '最高': 10.60,
        '最低': 10.25,
        '昨收': 10.25,
        '换手率': 1.5,
        '市盈率-动态': 5.8,
        '市净率': 0.6,
        '总市值': 30000000000,
        '流通市值': 25000000000,
    }


@pytest.fixture
def sample_akshare_kline():
    """Sample AKShare K-line data"""
    import pandas as pd
    return pd.DataFrame({
        '日期': ['2024-01-01', '2024-01-02', '2024-01-03'],
        '开盘': [10.0, 10.2, 10.1],
        '收盘': [10.2, 10.1, 10.3],
        '最高': [10.3, 10.4, 10.5],
        '最低': [9.9, 10.0, 10.0],
        '成交量': [1000000, 1200000, 1100000],
        '成交额': [10200000, 12120000, 11330000],
        '振幅': [4.0, 3.9, 4.9],
        '涨跌幅': [2.0, -1.0, 2.0],
        '涨跌额': [0.2, -0.1, 0.2],
        '换手率': [1.0, 1.2, 1.1],
    })


@pytest.fixture
def sample_yahoo_quote():
    """Sample Yahoo Finance quote data"""
    return {
        'regularMarketPrice': 150.25,
        'regularMarketChange': 2.5,
        'regularMarketChangePercent': 1.69,
        'regularMarketVolume': 50000000,
        'regularMarketDayHigh': 151.0,
        'regularMarketDayLow': 149.0,
        'regularMarketOpen': 149.5,
        'regularMarketPreviousClose': 147.75,
        'marketCap': 2500000000000,
        'trailingPE': 28.5,
        'priceToBook': 15.2,
    }


@pytest.fixture
def sample_sina_news():
    """Sample Sina news list response"""
    return {
        'result': {
            'data': [
                {
                    'docid': 'sina_123456',
                    'title': '测试新闻标题',
                    'intro': '这是新闻摘要',
                    'url': 'https://finance.sina.com.cn/123456.shtml',
                    'source': '新浪财经',
                    'ctime': '2024-01-15 10:30:00',
                    'channel': 'stock',
                    'keywords': '股票,财经',
                }
            ]
        }
    }


@pytest.fixture
def sample_weibo_hot():
    """Sample Weibo hot search response"""
    return {
        'data': {
            'realtime': [
                {
                    'word': '测试热搜话题',
                    'num': 1000000,
                    'rank': 1,
                    'category': '热',
                },
                {
                    'word': '股市大涨',
                    'num': 800000,
                    'rank': 2,
                    'category': '沸',
                },
            ]
        }
    }


@pytest.fixture
def sample_xueqiu_discussion():
    """Sample Xueqiu discussion response"""
    return {
        'list': [
            {
                'data': json.dumps({
                    'id': 123456,
                    'title': '看好茅台后市',
                    'description': '茅台业绩超预期，机构纷纷买入',
                    'user': {'id': 789, 'screen_name': '测试用户'},
                    'created_at': 1705300000000,
                    'retweet_count': 100,
                    'reply_count': 50,
                    'like_count': 200,
                    'stocks': [{'code': '600519', 'name': '贵州茅台'}],
                })
            }
        ]
    }

