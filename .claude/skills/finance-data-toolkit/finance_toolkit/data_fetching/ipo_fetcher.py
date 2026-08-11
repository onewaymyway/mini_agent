"""
IPO数据抓取器
覆盖：A股新股申购、新股上市、历史IPO数据、港股IPO、美股IPO
数据源：东方财富、同花顺、新浪财经、雪球
"""

import requests
import json
import time
from typing import List, Dict, Optional
from datetime import datetime

from .http_client import HttpClient
from ..exceptions import DataFetchError


class IPOFetcher:
    """IPO数据抓取器"""
    
    def __init__(self, timeout: int = 30, proxy: Optional[str] = None):
        self.client = HttpClient(timeout=timeout, proxy=proxy)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://data.eastmoney.com/',
        })
    
    def fetch_upcoming_ipo(self, market: str = 'a') -> List[Dict]:
        """
        获取即将上市的新股
        
        Args:
            market: 'a' | 'hk' | 'us'
        """
        if market == 'a':
            return self._fetch_a_ipo()
        elif market == 'hk':
            return self._fetch_hk_ipo()
        return []
    
    def fetch_new_listings(self, days: int = 7) -> List[Dict]:
        """获取近期上市新股"""
        url = 'https://datacenter.eastmoney.com/api/data/v1/get'
        params = {
            'reportName': 'RPT_NEW_STOCK_LISTING',
            'columns': 'ALL',
            'filter': '(TRADE_DATE>="2024-01-01")',
            'pageNumber': 1,
            'pageSize': 50,
            'sortTypes': '-1',
            'sortColumns': 'TRADE_DATE',
            'source': 'WEB',
            'client': 'WEB',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=30)
            data = resp.json()
            
            if data.get('result') and data['result'].get('data'):
                return self._parse_listings(data['result']['data'])
            return []
        except Exception as e:
            raise DataFetchError('new_listings', str(e))
    
    def fetch_ipo_calendar(self, year: int = 2024) -> List[Dict]:
        """获取IPO日历"""
        url = 'https://datacenter.eastmoney.com/api/data/v1/get'
        params = {
            'reportName': 'RPT_IPO_APPLY',
            'columns': 'ALL',
            'filter': f'(APPLY_DATE>="{year}-01-01")',
            'pageNumber': 1,
            'pageSize': 100,
            'sortTypes': '-1',
            'sortColumns': 'APPLY_DATE',
            'source': 'WEB',
            'client': 'WEB',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=30)
            data = resp.json()
            
            if data.get('result') and data['result'].get('data'):
                return self._parse_ipo_calendar(data['result']['data'])
            return []
        except Exception as e:
            raise DataFetchError('ipo_calendar', str(e))
    
    def fetch_stock_subscribe(self, code: str) -> Dict:
        """获取新股申购详情"""
        url = 'https://datacenter.eastmoney.com/api/data/v1/get'
        params = {
            'reportName': 'RPT_NEW_STOCK_SUBSCRIBE',
            'columns': 'ALL',
            'filter': f'(SECURITY_CODE="{code}")',
            'pageNumber': 1,
            'pageSize': 10,
            'source': 'WEB',
            'client': 'WEB',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=30)
            data = resp.json()
            
            if data.get('result') and data['result'].get('data'):
                return self._parse_subscribe(data['result']['data'][0])
            return {}
        except Exception as e:
            raise DataFetchError(f'subscribe_{code}', str(e))
    
    def _fetch_a_ipo(self) -> List[Dict]:
        """获取A股即将上市新股"""
        url = 'https://datacenter.eastmoney.com/api/data/v1/get'
        params = {
            'reportName': 'RPT_NEW_STOCK_LISTING',
            'columns': 'ALL',
            'filter': '(TRADE_DATE>="2024-01-01")',
            'pageNumber': 1,
            'pageSize': 30,
            'sortTypes': '-1',
            'sortColumns': 'TRADE_DATE',
            'source': 'WEB',
            'client': 'WEB',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=30)
            data = resp.json()
            
            if data.get('result') and data['result'].get('data'):
                return self._parse_listings(data['result']['data'])
            return []
        except Exception as e:
            raise DataFetchError('a_ipo', str(e))
    
    def _fetch_hk_ipo(self) -> List[Dict]:
        """获取港股IPO"""
        url = 'https://api.hkapi.cn/api/IPOList'
        
        try:
            resp = self.session.get(url, timeout=30)
            data = resp.json()
            return self._parse_hk_ipo(data)
        except Exception as e:
            raise DataFetchError('hk_ipo', str(e))
    
    def _parse_listings(self, data: List) -> List[Dict]:
        """解析上市新股"""
        results = []
        for item in data:
            results.append({
                'code': item.get('SECURITY_CODE'),
                'name': item.get('SECURITY_NAME_ABBR'),
                'market': item.get('MARKET'),
                'trade_date': item.get('TRADE_DATE'),
                'issue_price': item.get('ISSUE_PRICE'),
                'issue_pe': item.get('ISSUE_PE'),
                'issue_size': item.get('ISSUE_SIZE'),
                'subscribe_start': item.get('SUBSCRIBE_START_DATE'),
                'subscribe_end': item.get('SUBSCRIBE_END_DATE'),
                'update_time': datetime.now().isoformat(),
            })
        return results
    
    def _parse_ipo_calendar(self, data: List) -> List[Dict]:
        """解析IPO日历"""
        results = []
        for item in data:
            results.append({
                'code': item.get('SECURITY_CODE'),
                'name': item.get('SECURITY_NAME_ABBR'),
                'apply_date': item.get('APPLY_DATE'),
                'issue_price': item.get('ISSUE_PRICE'),
                'status': item.get('STATUS'),
                'update_time': datetime.now().isoformat(),
            })
        return results
    
    def _parse_subscribe(self, data: Dict) -> Dict:
        """解析申购详情"""
        return {
            'code': data.get('SECURITY_CODE'),
            'name': data.get('SECURITY_NAME_ABBR'),
            'subscribe_price': data.get('ISSUE_PRICE'),
            'subscribe_units': data.get('MIN_UNIT'),
            'max_units': data.get('MAX_UNIT'),
            'update_time': datetime.now().isoformat(),
        }
    
    def _parse_hk_ipo(self, data: Dict) -> List[Dict]:
        """解析港股IPO"""
        return data.get('data', [])


def fetch_upcoming_listings() -> List[Dict]:
    """便捷函数：获取即将上市新股"""
    fetcher = IPOFetcher()
    return fetcher.fetch_upcoming_ipo('a')


def fetch_ipo_calendar() -> List[Dict]:
    """便捷函数：获取IPO日历"""
    fetcher = IPOFetcher()
    return fetcher.fetch_ipo_calendar()
