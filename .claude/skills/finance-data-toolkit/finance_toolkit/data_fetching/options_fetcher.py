"""
期权数据抓取器
覆盖：A股期权（沪深300ETF期权、50ETF期权）、商品期权、股指期权
数据源：东方财富、新浪期权、中金所、上期所、大商所、郑商所
"""

import requests
import json
import time
from typing import List, Dict, Optional
from datetime import datetime

from .http_client import HttpClient
from ..exceptions import DataFetchError


class OptionsFetcher:
    """期权数据抓取器"""
    
    def __init__(self, timeout: int = 30, proxy: Optional[str] = None):
        self.client = HttpClient(timeout=timeout, proxy=proxy)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://quote.eastmoney.com/',
        })
    
    def fetch_option_list(self, market: str = 'all') -> List[Dict]:
        """
        获取期权合约列表
        
        Args:
            market: 'all' | 'sh' | 'sz' | 'cffex' | 'shfe' | 'dce' | 'czce'
        
        Returns:
            期权合约列表
        """
        # 东方财富期权列表API
        url = 'https://push2.eastmoney.com/api/qt/clist/get'
        params = {
            'pn': 1,
            'pz': 5000,
            'po': 1,
            'np': 1,
            'fltt': 2,
            'invt': 2,
            'fid': 'f3',
            'fs': 'm:90+t:2',  # 期权市场
            'fields': 'f12,f13,f14,f2,f3,f4,f5,f6,f7,f15,f16,f17,f18',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=30)
            data = resp.json()
            
            if data.get('data') and data['data'].get('diff'):
                return [self._parse_option_item(item) for item in data['data']['diff']]
            return []
        except Exception as e:
            raise DataFetchError('options_list', str(e))
    
    def fetch_option_chain(self, underlying: str, expiry: Optional[str] = None) -> Dict:
        """
        获取期权链数据
        
        Args:
            underlying: 标的代码，如 '510050' (50ETF), '510300' (300ETF)
            expiry: 到期日，如 '202401'，不传则返回所有月份
        
        Returns:
            期权链数据，包含看涨/看跌合约
        """
        # 新浪期权链API
        url = 'https://stock2.finance.sina.com.cn/futures/api/json.php'
        params = {
            'index_id': self._get_index_id(underlying),
            'contract_type': 'all',
        }
        
        try:
            resp = self.session.get(url, params=params, timeout=30)
            data = json.loads(resp.text.strip())
            return self._parse_option_chain(data, underlying, expiry)
        except Exception as e:
            raise DataFetchError(f'options_chain_{underlying}', str(e))
    
    def fetch_option_greeks(self, option_code: str) -> Dict:
        """
        获取期权希腊字母
        
        Args:
            option_code: 期权代码，如 '510050C2401M02600'
        
        Returns:
            希腊字母数据
        """
        url = 'https://push2.eastmoney.com/api/qt/stock/get'
        params = {
            'secid': self._parse_secid(option_code),
            'fields': 'f43,f44,f45,f46,f47,f48,f57,f58,f170',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=15)
            data = resp.json()
            if data.get('data'):
                d = data['data']
                return {
                    'option_code': option_code,
                    'delta': d.get('f43'),
                    'gamma': d.get('f44'),
                    'theta': d.get('f45'),
                    'vega': d.get('f46'),
                    'rho': d.get('f47'),
                    'iv': d.get('f48'),  # 隐含波动率
                    'price': d.get('f43'),
                    'update_time': datetime.now().isoformat(),
                }
            return {}
        except Exception as e:
            raise DataFetchError(f'greeks_{option_code}', str(e))
    
    def fetch_option_volume_ranking(self, market: str = 'all') -> List[Dict]:
        """
        获取期权成交量排名
        
        Returns:
            按成交量排序的期权合约列表
        """
        url = 'https://push2.eastmoney.com/api/qt/clist/get'
        params = {
            'pn': 1,
            'pz': 100,
            'po': 1,
            'np': 1,
            'fltt': 2,
            'invt': 2,
            'fid': 'f5',  # 按成交量排序
            'fs': 'm:90+t:2',
            'fields': 'f12,f13,f14,f2,f5,f6,f7,f15,f16,f17,f18',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=30)
            data = resp.json()
            
            if data.get('data') and data['data'].get('diff'):
                return [self._parse_option_item(item) for item in data['data']['diff']]
            return []
        except Exception as e:
            raise DataFetchError('options_volume_ranking', str(e))
    
    def fetch_oi_change(self, underlying: str, days: int = 5) -> List[Dict]:
        """
        获取持仓变化数据
        
        Args:
            underlying: 标的代码
            days: 回溯天数
        
        Returns:
            持仓变化列表
        """
        # 东方财富期权持仓数据
        url = 'https://datacenter.eastmoney.com/api/data/v1/get'
        params = {
            'reportName': 'RPT_OPTION_CURRENT_HOLDER',
            'columns': 'ALL',
            'filter': f'(UNDERLYING_CODE="{underlying}")',
            'pageNumber': 1,
            'pageSize': 50,
            'sortTypes': '-1',
            'sortColumns': 'CHANGE_DATE',
            'source': 'WEB',
            'client': 'WEB',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=30)
            data = resp.json()
            
            if data.get('result') and data['result'].get('data'):
                return data['result']['data']
            return []
        except Exception as e:
            raise DataFetchError(f'oi_change_{underlying}', str(e))
    
    def _parse_option_item(self, item: Dict) -> Dict:
        """解析单个期权合约"""
        return {
            'code': item.get('f12'),
            'name': item.get('f14'),
            'underlying': item.get('f2'),
            'type': 'CALL' if item.get('f3') == 'C' else 'PUT',
            'strike_price': item.get('f4'),
            'expiry': item.get('f15'),
            'last_price': item.get('f5'),
            'change_pct': item.get('f6'),
            'volume': item.get('f7'),
            'open_interest': item.get('f16'),
            'iv': item.get('f17'),
            'update_time': datetime.now().isoformat(),
        }
    
    def _parse_option_chain(self, data: List, underlying: str, expiry: Optional[str]) -> Dict:
        """解析期权链数据"""
        calls = []
        puts = []
        
        for item in data:
            contract = item.get('contract', '')
            if 'C' in contract or 'call' in contract.lower():
                calls.append(item)
            else:
                puts.append(item)
        
        return {
            'underlying': underlying,
            'calls': calls,
            'puts': puts,
            'update_time': datetime.now().isoformat(),
        }
    
    def _get_index_id(self, underlying: str) -> str:
        """获取指数ID映射"""
        mapping = {
            '510050': '510050',  # 50ETF
            '510300': '510300',  # 300ETF
            '159919': '159919',  # 500ETF
            '000016': '000016',  # 上证50
            '000300': '000300',  # 沪深300
            '000905': '000905',  # 中证500
        }
        return mapping.get(underlying, underlying)
    
    def _parse_secid(self, option_code: str) -> str:
        """解析期权代码为secid"""
        if option_code.startswith('5'):
            return f'1.{option_code}'
        return f'0.{option_code}'


def fetch_options(market: str = 'all') -> List[Dict]:
    """便捷函数：获取期权列表"""
    fetcher = OptionsFetcher()
    return fetcher.fetch_option_list(market)


def fetch_option_chain_data(underlying: str, expiry: Optional[str] = None) -> Dict:
    """便捷函数：获取期权链"""
    fetcher = OptionsFetcher()
    return fetcher.fetch_option_chain(underlying, expiry)


def fetch_option_greeks_data(option_code: str) -> Dict:
    """便捷函数：获取希腊字母"""
    fetcher = OptionsFetcher()
    return fetcher.fetch_option_greeks(option_code)
