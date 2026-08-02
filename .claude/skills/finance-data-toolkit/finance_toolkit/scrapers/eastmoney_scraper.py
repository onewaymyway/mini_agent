# -*- coding: utf-8 -*-
"""
东方财富抓取器实现
数据源：东方财富 (免费、无需 token)
支持：实时行情、历史 K 线、资金流向、股吧舆情等
"""

from datetime import datetime
from typing import List, Optional, AsyncIterator

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    httpx = None

from ..core import BaseScraper, FinanceData, register_scraper


@register_scraper
class EastmoneyScraper(BaseScraper):
    """东方财富数据抓取器"""
    
    @property
    def source_name(self) -> str:
        return 'eastmoney'
    
    @property
    def supported_types(self) -> List[str]:
        return ['quote', 'kline', 'moneyflow', 'guba']
    
    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://push2.eastmoney.com/api/qt/stock/get',
                    params={'secid': '1.600000', 'fields': 'f43'},
                    timeout=10
                )
                return resp.status_code == 200
        except Exception:
            return False
    
    async def fetch(self, 
        symbols: List[str],
        data_type: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> AsyncIterator[FinanceData]:
        """获取数据主入口"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")
        
        if data_type == 'quote':
            async for data in self._fetch_quotes(symbols):
                yield data
        elif data_type == 'kline':
            for symbol in symbols:
                async for data in self._fetch_kline(symbol, start, end, **kwargs):
                    yield data
        elif data_type == 'moneyflow':
            for symbol in symbols:
                async for data in self._fetch_moneyflow(symbol, start, end):
                    yield data
        elif data_type == 'guba':
            for symbol in symbols:
                async for data in self._fetch_guba(symbol, **kwargs):
                    yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")
    
    async def _fetch_quotes(self, symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取实时行情"""
        async with httpx.AsyncClient(timeout=30) as client:
            secids = []
            for symbol in symbols:
                code, market = symbol.split('.')
                prefix = '1' if market == 'SH' else '0'
                secids.append(f"{prefix}.{code}")
            
            params = {
                'secid': ','.join(secids),
                'fields': 'f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f57,f58,f60,f61,f170',
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                'fltt': '2'
            }
            
            resp = await client.get(
                'https://push2.eastmoney.com/api/qt/stock/get',
                params=params,
                timeout=30
            )
            
            if resp.status_code != 200:
                return
            
            data = resp.json()
            if not data.get('data') or not data['data'].get('diff'):
                return
            
            for secid, item in data['data']['diff'].items():
                if not item or item.get('f43') is None:
                    continue
                
                code, market = secid.split('.')
                symbol = f"{code}.{'SH' if market == '1' else 'SZ'}"
                
                yield FinanceData(
                    source='eastmoney',
                    data_type='quote',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={
                        'open': item.get('f46'),
                        'high': item.get('f44'),
                        'low': item.get('f45'),
                        'close': item.get('f43'),
                        'volume': item.get('f47'),
                        'amount': item.get('f48'),
                        'change_pct': item.get('f170'),
                        'turnover': item.get('f49'),
                        'pe_ttm': item.get('f51'),
                        'pb': item.get('f52'),
                        'total_mv': item.get('f60'),
                        'circ_mv': item.get('f61')
                    }
                )

    async def _fetch_kline(self, symbol: str, start: Optional[datetime], end: Optional[datetime], **kwargs) -> AsyncIterator[FinanceData]:
        """获取历史 K 线"""
        code, market = symbol.split('.')
        prefix = '1' if market == 'SH' else '0'
        secid = f"{prefix}.{code}"
        
        klt = kwargs.get('klt', '101')
        fqt = kwargs.get('fqt', '1')
        
        beg = start.strftime('%Y%m%d') if start else '20200101'
        end_str = end.strftime('%Y%m%d') if end else datetime.now().strftime('%Y%m%d')
        
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://push2his.eastmoney.com/api/qt/stock/kline/get',
                params={
                    'secid': secid,
                    'klt': klt,
                    'fqt': fqt,
                    'beg': beg,
                    'end': end_str,
                    'fields1': 'f1,f2,f3,f4,f5,f6',
                    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58',
                    'ut': 'fa5fd1943c7b386f172d6893dbfba10b'
                },
                timeout=30
            )
            
            if resp.status_code != 200:
                return
            
            data = resp.json()
            if not data.get('data') or not data['data'].get('klines'):
                return
            
            for kl in data['data']['klines']:
                parts = kl.split(',')
                if len(parts) < 8:
                    continue
                
                yield FinanceData(
                    source='eastmoney',
                    data_type='kline',
                    symbol=symbol,
                    timestamp=datetime.strptime(parts[0], '%Y-%m-%d'),
                    payload={
                        'open': float(parts[1]),
                        'close': float(parts[2]),
                        'high': float(parts[3]),
                        'low': float(parts[4]),
                        'volume': int(parts[5]),
                        'amount': float(parts[6]),
                        'amplitude': float(parts[7])
                    }
                )
    
    async def _fetch_moneyflow(self, symbol: str, start: Optional[datetime], end: Optional[datetime]) -> AsyncIterator[FinanceData]:
        """获取资金流向（简化版）"""
        code, market = symbol.split('.')
        
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://push2his.eastmoney.com/api/qt/stock/moneyflow/get',
                params={
                    'secid': f"{'1' if market == 'SH' else '0'}.{code}",
                    'ut': 'fa5fd1943c7b386f172d6893dbfba10b'
                },
                timeout=30
            )
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get('data'):
                    yield FinanceData(
                        source='eastmoney',
                        data_type='moneyflow',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload=data['data']
                    )
    
    async def _fetch_guba(self, symbol: str, **kwargs) -> AsyncIterator[FinanceData]:
        """获取股吧帖子（简化版）"""
        # TODO: 实现完整的股吧抓取
        pass
    
    async def close(self):
        """关闭资源"""
        pass
