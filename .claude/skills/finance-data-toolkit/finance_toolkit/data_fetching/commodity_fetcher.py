"""
大宗商品数据抓取器
覆盖：贵金属（黄金、白银）、能源（原油、天然气）、农产品（大豆、玉米、小麦）、有色金属（铜、铝、锌）
数据源：上海期货交易所、大连商品交易所、郑州商品交易所、纽约商品交易所、伦敦金属交易所
"""

import requests
import json
import time
from typing import List, Dict, Optional
from datetime import datetime

from .http_client import HttpClient
from ..exceptions import DataFetchError


class CommodityFetcher:
    """大宗商品数据抓取器"""
    
    def __init__(self, timeout: int = 30, proxy: Optional[str] = None):
        self.client = HttpClient(timeout=timeout, proxy=proxy)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
        })
    
    def fetch_metals(self) -> List[Dict]:
        """
        获取有色金属价格
        覆盖：铜、铝、锌、铅、镍、锡
        """
        url = 'https://hq.sinajs.cn/list=sf_cu,sf_al,sf_zn,sf_pb,sf_ni,sf_sn'
        headers = {
            'Referer': 'https://finance.sina.com.cn/',
        }
        
        try:
            resp = self.session.get(url, headers=headers, timeout=15)
            return self._parse_metals(resp.text)
        except Exception as e:
            raise DataFetchError('metals', str(e))
    
    def fetch_precious_metals(self) -> List[Dict]:
        """
        获取贵金属价格
        覆盖：黄金、白银、铂金、钯金
        """
        url = 'https://hq.sinajs.cn/list=gl_gold,gl_silver,gl_platinum,gl_palladium'
        headers = {
            'Referer': 'https://finance.sina.com.cn/',
        }
        
        try:
            resp = self.session.get(url, headers=headers, timeout=15)
            return self._parse_precious(resp.text)
        except Exception as e:
            raise DataFetchError('precious_metals', str(e))
    
    def fetch_energy(self) -> List[Dict]:
        """
        获取能源价格
        覆盖：原油（WTI、布伦特）、天然气、燃料油
        """
        url = 'https://hq.sinajs.cn/list=cl,br,ng,ru'
        headers = {
            'Referer': 'https://finance.sina.com.cn/',
        }
        
        try:
            resp = self.session.get(url, headers=headers, timeout=15)
            return self._parse_energy(resp.text)
        except Exception as e:
            raise DataFetchError('energy', str(e))
    
    def fetch_agriculture(self) -> List[Dict]:
        """
        获取农产品价格
        覆盖：大豆、玉米、小麦、棉花、白糖、豆油、棕榈油
        """
        url = 'https://hq.sinajs.cn/list=soybean,corn,wheat,cotton,sugar,soyoil,palmoil'
        headers = {
            'Referer': 'https://finance.sina.com.cn/',
        }
        
        try:
            resp = self.session.get(url, headers=headers, timeout=15)
            return self._parse_agriculture(resp.text)
        except Exception as e:
            raise DataFetchError('agriculture', str(e))
    
    def fetch_futures_contract(self, exchange: str, symbol: str) -> Dict:
        """
        获取期货合约详情
        
        Args:
            exchange: 'shfe' | 'dce' | 'czce' | 'cffex' | 'ine'
            symbol: 合约代码
        """
        url = f'https://www.{exchange}.com.cn/data/dailydata/kx/{symbol}.json'
        
        try:
            resp = self.client.get(url, timeout=15)
            data = resp.json()
            return self._parse_futures_contract(data, exchange, symbol)
        except Exception as e:
            raise DataFetchError(f'futures_{symbol}', str(e))
    
    def fetch_futures_oi(self, exchange: str, symbol: str) -> List[Dict]:
        """
        获取期货持仓数据
        
        Args:
            exchange: 'shfe' | 'dce' | 'czce' | 'cffex'
            symbol: 品种代码
        """
        url = f'https://www.{exchange}.com.cn/data/dailydata/xq/{symbol}.json'
        
        try:
            resp = self.client.get(url, timeout=15)
            data = resp.json()
            return self._parse_oi_data(data, exchange, symbol)
        except Exception as e:
            raise DataFetchError(f'oi_{symbol}', str(e))
    
    def fetch_lme_prices(self) -> List[Dict]:
        """
        获取LME（伦敦金属交易所）价格
        """
        url = 'https://www.lme.com/ajax/GetLMEPrice'
        params = {
            'ProductCode': 'ALL',
            'DateFrom': '',
            'DateTo': '',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=30)
            return self._parse_lme(resp.json())
        except Exception as e:
            raise DataFetchError('lme', str(e))
    
    def fetch_cme_prices(self) -> List[Dict]:
        """
        获取CME（芝加哥商品交易所）价格
        """
        url = 'https://api.cmegroup.com/market-data/v1/prices/settlements'
        params = {
            'dateRange': '1',
            'group': 'ALL',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=30)
            return self._parse_cme(resp.json())
        except Exception as e:
            raise DataFetchError('cme', str(e))
    
    def _parse_metals(self, text: str) -> List[Dict]:
        """解析有色金属数据"""
        results = []
        lines = text.strip().split('\n')
        
        for line in lines:
            if '=' not in line:
                continue
            code = line.split('=')[0].replace('hq_str_', '').strip('"')
            data = line.split('=')[1].strip('"')
            if not data or data == '':
                continue
            
            parts = data.split(',')
            if len(parts) < 10:
                continue
            
            results.append({
                'code': code,
                'name': parts[0],
                'price': float(parts[1]) if parts[1] else 0,
                'open': float(parts[2]) if parts[2] else 0,
                'high': float(parts[3]) if parts[3] else 0,
                'low': float(parts[4]) if parts[4] else 0,
                'prev_close': float(parts[5]) if parts[5] else 0,
                'change': float(parts[6]) if parts[6] else 0,
                'change_pct': float(parts[7]) if parts[7] else 0,
                'volume': parts[8] if len(parts) > 8 else '',
                'time': parts[-1] if parts else '',
                'type': 'metal',
                'update_time': datetime.now().isoformat(),
            })
        
        return results
    
    def _parse_precious(self, text: str) -> List[Dict]:
        """解析贵金属数据"""
        results = []
        lines = text.strip().split('\n')
        
        for line in lines:
            if '=' not in line:
                continue
            code = line.split('=')[0].replace('hq_str_', '').strip('"')
            data = line.split('=')[1].strip('"')
            if not data:
                continue
            
            parts = data.split(',')
            results.append({
                'code': code,
                'name': parts[0] if parts else code,
                'price': float(parts[1]) if len(parts) > 1 and parts[1] else 0,
                'change_pct': float(parts[7]) if len(parts) > 7 and parts[7] else 0,
                'type': 'precious_metal',
                'update_time': datetime.now().isoformat(),
            })
        
        return results
    
    def _parse_energy(self, text: str) -> List[Dict]:
        """解析能源数据"""
        results = []
        lines = text.strip().split('\n')
        
        for line in lines:
            if '=' not in line:
                continue
            code = line.split('=')[0].replace('hq_str_', '').strip('"')
            data = line.split('=')[1].strip('"')
            if not data:
                continue
            
            parts = data.split(',')
            results.append({
                'code': code,
                'name': parts[0] if parts else code,
                'price': float(parts[1]) if len(parts) > 1 and parts[1] else 0,
                'change_pct': float(parts[7]) if len(parts) > 7 and parts[7] else 0,
                'type': 'energy',
                'update_time': datetime.now().isoformat(),
            })
        
        return results
    
    def _parse_agriculture(self, text: str) -> List[Dict]:
        """解析农产品数据"""
        results = []
        lines = text.strip().split('\n')
        
        for line in lines:
            if '=' not in line:
                continue
            code = line.split('=')[0].replace('hq_str_', '').strip('"')
            data = line.split('=')[1].strip('"')
            if not data:
                continue
            
            parts = data.split(',')
            results.append({
                'code': code,
                'name': parts[0] if parts else code,
                'price': float(parts[1]) if len(parts) > 1 and parts[1] else 0,
                'change_pct': float(parts[7]) if len(parts) > 7 and parts[7] else 0,
                'type': 'agriculture',
                'update_time': datetime.now().isoformat(),
            })
        
        return results
    
    def _parse_futures_contract(self, data: Dict, exchange: str, symbol: str) -> Dict:
        """解析期货合约数据"""
        return {
            'exchange': exchange,
            'symbol': symbol,
            'data': data,
            'update_time': datetime.now().isoformat(),
        }
    
    def _parse_oi_data(self, data: Dict, exchange: str, symbol: str) -> List[Dict]:
        """解析持仓数据"""
        return data.get('data', [])
    
    def _parse_lme(self, data: Dict) -> List[Dict]:
        """解析LME数据"""
        return data.get('data', [])
    
    def _parse_cme(self, data: Dict) -> List[Dict]:
        """解析CME数据"""
        return data.get('data', [])


def fetch_metals_prices() -> List[Dict]:
    """便捷函数：获取有色金属价格"""
    fetcher = CommodityFetcher()
    return fetcher.fetch_metals()


def fetch_precious_metals_prices() -> List[Dict]:
    """便捷函数：获取贵金属价格"""
    fetcher = CommodityFetcher()
    return fetcher.fetch_precious_metals()


def fetch_energy_prices() -> List[Dict]:
    """便捷函数：获取能源价格"""
    fetcher = CommodityFetcher()
    return fetcher.fetch_energy()


def fetch_agriculture_prices() -> List[Dict]:
    """便捷函数：获取农产品价格"""
    fetcher = CommodityFetcher()
    return fetcher.fetch_agriculture()
