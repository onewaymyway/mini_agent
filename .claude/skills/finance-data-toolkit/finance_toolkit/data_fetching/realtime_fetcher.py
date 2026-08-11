"""
实时行情数据抓取器
覆盖：A股实时行情、港股实时行情、美股实时行情、指数实时行情
数据源：新浪财经、东方财富、腾讯财经
"""

import requests
import json
import time
from typing import List, Dict, Optional
from datetime import datetime

from .http_client import HttpClient
from ..exceptions import DataFetchError


class RealtimeFetcher:
    """实时行情数据抓取器"""
    
    def __init__(self, timeout: int = 15, proxy: Optional[str] = None):
        self.client = HttpClient(timeout=timeout, proxy=proxy)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Referer': 'https://finance.sina.com.cn/',
        })
    
    def fetch_a_stock_quote(self, codes: List[str]) -> List[Dict]:
        """
        获取A股实时行情
        
        Args:
            codes: 股票代码列表，如 ['sh600000', 'sz000001']
        """
        if not codes:
            return []
        
        # 新浪实时行情API
        symbol_str = ','.join(codes)
        url = f'https://hq.sinajs.cn/list={symbol_str}'
        
        try:
            resp = self.session.get(url, timeout=10)
            return self._parse_sina_quote(resp.text)
        except Exception as e:
            raise DataFetchError(f'a_quote_{codes[0] if codes else "unknown"}', str(e))
    
    def fetch_hk_stock_quote(self, codes: List[str]) -> List[Dict]:
        """获取港股实时行情"""
        if not codes:
            return []
        
        # 港股代码格式：hk00700
        hk_codes = [f'hk{c[1:]}' if c.isdigit() else c for c in codes]
        symbol_str = ','.join(hk_codes)
        url = f'https://hq.sinajs.cn/list={symbol_str}'
        
        try:
            resp = self.session.get(url, timeout=10)
            return self._parse_hk_quote(resp.text)
        except Exception as e:
            raise DataFetchError(f'hk_quote_{codes[0] if codes else "unknown"}', str(e))
    
    def fetch_us_stock_quote(self, codes: List[str]) -> List[Dict]:
        """获取美股实时行情"""
        if not codes:
            return []
        
        symbol_str = ','.join(codes)
        url = f'https://hq.sinajs.cn/list={symbol_str}'
        
        try:
            resp = self.session.get(url, timeout=10)
            return self._parse_us_quote(resp.text)
        except Exception as e:
            raise DataFetchError(f'us_quote_{codes[0] if codes else "unknown"}', str(e))
    
    def fetch_index_quote(self, codes: List[str]) -> List[Dict]:
        """获取指数实时行情"""
        if not codes:
            return []
        
        symbol_str = ','.join(codes)
        url = f'https://hq.sinajs.cn/list={symbol_str}'
        
        try:
            resp = self.session.get(url, timeout=10)
            return self._parse_index_quote(resp.text)
        except Exception as e:
            raise DataFetchError(f'index_quote_{codes[0] if codes else "unknown"}', str(e))
    
    def fetch_market_summary(self) -> Dict:
        """获取市场概况"""
        # 上证指数、深证成指、创业板指
        codes = ['sh000001', 'sz399001', 'sz399006', 'sh000300', 'sh000016']
        
        try:
            quotes = self.fetch_a_stock_quote(codes)
            return self._build_market_summary(quotes)
        except Exception as e:
            raise DataFetchError('market_summary', str(e))
    
    def fetch_top_gainers(self, market: str = 'a', limit: int = 20) -> List[Dict]:
        """获取涨幅榜"""
        url = 'https://push2.eastmoney.com/api/qt/clist/get'
        params = {
            'pn': 1,
            'pz': limit,
            'po': 1,
            'np': 1,
            'fltt': 2,
            'invt': 2,
            'fid': 'f3',  # 按涨跌幅排序
            'fs': 'm:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23',
            'fields': 'f12,f14,f2,f3,f4,f5,f6,f7,f15,f16,f17,f18',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=15)
            data = resp.json()
            
            if data.get('data') and data['data'].get('diff'):
                return [self._parse_quote_item(item) for item in data['data']['diff']]
            return []
        except Exception as e:
            raise DataFetchError(f'top_gainers_{market}', str(e))
    
    def fetch_top_losers(self, market: str = 'a', limit: int = 20) -> List[Dict]:
        """获取跌幅榜"""
        url = 'https://push2.eastmoney.com/api/qt/clist/get'
        params = {
            'pn': 1,
            'pz': limit,
            'po': 0,  # 降序
            'np': 1,
            'fltt': 2,
            'invt': 2,
            'fid': 'f3',
            'fs': 'm:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23',
            'fields': 'f12,f14,f2,f3,f4,f5,f6,f7,f15,f16,f17,f18',
        }
        
        try:
            resp = self.client.get(url, params=params, timeout=15)
            data = resp.json()
            
            if data.get('data') and data['data'].get('diff'):
                return [self._parse_quote_item(item) for item in data['data']['diff']]
            return []
        except Exception as e:
            raise DataFetchError(f'top_losers_{market}', str(e))
    
    def _parse_sina_quote(self, text: str) -> List[Dict]:
        """解析新浪行情数据"""
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
            if len(parts) < 32:
                continue
            
            try:
                results.append({
                    'code': code,
                    'name': parts[0],
                    'price': float(parts[3]) if parts[3] else 0,
                    'open': float(parts[5]) if parts[5] else 0,
                    'high': float(parts[33]) if len(parts) > 33 and parts[33] else 0,
                    'low': float(parts[34]) if len(parts) > 34 and parts[34] else 0,
                    'prev_close': float(parts[4]) if parts[4] else 0,
                    'volume': float(parts[6]) if parts[6] else 0,
                    'amount': float(parts[7]) if parts[7] else 0,
                    'change': float(parts[31]) if len(parts) > 31 and parts[31] else 0,
                    'change_pct': float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                    'time': parts[30] if len(parts) > 30 else '',
                    'type': 'a_stock',
                    'update_time': datetime.now().isoformat(),
                })
            except (ValueError, IndexError):
                continue
        
        return results
    
    def _parse_hk_quote(self, text: str) -> List[Dict]:
        """解析港股行情数据"""
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
            if len(parts) < 20:
                continue
            
            try:
                results.append({
                    'code': code,
                    'name': parts[0],
                    'price': float(parts[2]) if parts[2] else 0,
                    'prev_close': float(parts[3]) if parts[3] else 0,
                    'change_pct': float(parts[31]) if len(parts) > 31 and parts[31] else 0,
                    'type': 'hk_stock',
                    'update_time': datetime.now().isoformat(),
                })
            except (ValueError, IndexError):
                continue
        
        return results
    
    def _parse_us_quote(self, text: str) -> List[Dict]:
        """解析美股行情数据"""
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
            if len(parts) < 10:
                continue
            
            try:
                results.append({
                    'code': code,
                    'name': parts[0],
                    'price': float(parts[1]) if parts[1] else 0,
                    'change': float(parts[2]) if parts[2] else 0,
                    'change_pct': float(parts[3]) if parts[3] else 0,
                    'type': 'us_stock',
                    'update_time': datetime.now().isoformat(),
                })
            except (ValueError, IndexError):
                continue
        
        return results
    
    def _parse_index_quote(self, text: str) -> List[Dict]:
        """解析指数行情数据"""
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
            if len(parts) < 10:
                continue
            
            try:
                results.append({
                    'code': code,
                    'name': parts[0],
                    'price': float(parts[3]) if parts[3] else 0,
                    'change': float(parts[31]) if len(parts) > 31 and parts[31] else 0,
                    'change_pct': float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                    'volume': float(parts[6]) if parts[6] else 0,
                    'type': 'index',
                    'update_time': datetime.now().isoformat(),
                })
            except (ValueError, IndexError):
                continue
        
        return results
    
    def _parse_quote_item(self, item: Dict) -> Dict:
        """解析行情项"""
        return {
            'code': item.get('f12'),
            'name': item.get('f14'),
            'price': item.get('f2'),
            'change_pct': item.get('f3'),
            'volume': item.get('f5'),
            'amount': item.get('f6'),
            'update_time': datetime.now().isoformat(),
        }
    
    def _build_market_summary(self, quotes: List[Dict]) -> Dict:
        """构建市场概况"""
        summary = {
            'sh000001': None,
            'sz399001': None,
            'sz399006': None,
            'sh000300': None,
            'sh000016': None,
            'update_time': datetime.now().isoformat(),
        }
        
        for q in quotes:
            code = q.get('code', '')
            if code in summary:
                summary[code] = q
        
        return summary


def fetch_realtime_quote(codes: List[str]) -> List[Dict]:
    """便捷函数：获取实时行情"""
    fetcher = RealtimeFetcher()
    return fetcher.fetch_a_stock_quote(codes)


def fetch_market_summary() -> Dict:
    """便捷函数：获取市场概况"""
    fetcher = RealtimeFetcher()
    return fetcher.fetch_market_summary()


def fetch_top_gainers(limit: int = 20) -> List[Dict]:
    """便捷函数：获取涨幅榜"""
    fetcher = RealtimeFetcher()
    return fetcher.fetch_top_gainers(limit=limit)
