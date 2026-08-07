# -*- coding: utf-8 -*-
"""
指数数据抓取器实现
数据源: 东方财富、同花顺 (免费、无需 token)
支持: 沪深指数、行业指数、风格指数、指数成分等
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
class IndexScraper(BaseScraper):
    """指数数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'index'

    @property
    def supported_types(self) -> List[str]:
        return ['index_quote', 'index_kline', 'index_constituents', 'index_info']

    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://quote.eastmoney.com/center/gridlist.html#hs_indices',
                    timeout=10
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch(
        self,
        symbols: List[str],
        data_type: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        **kwargs
    ) -> AsyncIterator[FinanceData]:
        """获取数据主入口"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        if data_type == 'index_quote':
            async for data in self._fetch_quotes(**kwargs):
                yield data
        elif data_type == 'index_kline':
            for symbol in symbols:
                async for data in self._fetch_kline(symbol, start, end, **kwargs):
                    yield data
        elif data_type == 'index_constituents':
            for symbol in symbols:
                async for data in self._fetch_constituents(symbol):
                    yield data
        elif data_type == 'index_info':
            for symbol in symbols:
                async for data in self._fetch_info(symbol):
                    yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_quotes(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取指数实时行情"""
        async with httpx.AsyncClient(timeout=30) as client:
            # 获取主要指数行情
            resp = await client.get(
                'https://push2.eastmoney.com/api/qt/clist/get',
                params={
                    'pn': '1',
                    'pz': '100',
                    'po': '1',
                    'np': '1',
                    'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                    'fltt': '2',
                    'invt': '2',
                    'fid': 'f3',
                    'fs': 'm:0+t:2,m:0+t:3,m:1+t:2,m:1+t:3',
                    'fields': 'f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18'
                },
                timeout=30
            )

            if resp.status_code != 200:
                return

            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                quotes = []
                for item in data['data']['diff'][:50]:
                    if item.get('f2') is not None:
                        quotes.append({
                            'name': item.get('f14', ''),
                            'code': item.get('f12', ''),
                            'price': item.get('f2'),
                            'change_pct': item.get('f3'),
                            'high': item.get('f4'),
                            'low': item.get('f5'),
                            'open': item.get('f6'),
                            'pre_close': item.get('f17'),
                            'volume': item.get('f7'),
                            'amount': item.get('f8')
                        })
                yield FinanceData(
                    source='index',
                    data_type='index_quote',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'quotes': quotes, 'count': len(quotes)}
                )

    async def _fetch_kline(self, symbol: str, start: Optional[datetime], end: Optional[datetime], **kwargs) -> AsyncIterator[FinanceData]:
        """获取指数K线"""
        async with httpx.AsyncClient(timeout=30) as client:
            beg = (start or datetime.now() - __import__('datetime').timedelta(days=365)).strftime('%Y-%m-%d')
            ed = (end or datetime.now()).strftime('%Y-%m-%d')
            
            # 判断市场
            prefix = '1' if symbol.startswith('000') else '0'
            secid = f"{prefix}.{symbol}"

            resp = await client.get(
                'https://push2his.eastmoney.com/api/qt/stock/kline/get',
                params={
                    'secid': secid,
                    'klt': '101',
                    'fqt': '1',
                    'beg': beg.replace('-', ''),
                    'end': ed.replace('-', ''),
                    'fields1': 'f1,f2,f3,f4,f5,f6',
                    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58',
                    'ut': 'fa5fd1943c7b386f172d6893dbfba10b'
                },
                timeout=30
            )

            if resp.status_code != 200:
                return

            data = resp.json()
            if data.get('data') and data['data'].get('klines'):
                records = []
                for kl in data['data']['klines']:
                    parts = kl.split(',')
                    if len(parts) >= 8:
                        records.append({
                            'date': parts[0],
                            'open': float(parts[1]),
                            'close': float(parts[2]),
                            'high': float(parts[3]),
                            'low': float(parts[4]),
                            'volume': int(parts[5]),
                            'amount': float(parts[6]),
                            'amplitude': float(parts[7])
                        })
                yield FinanceData(
                    source='index',
                    data_type='index_kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records), 'start': beg, 'end': ed}
                )

    async def _fetch_constituents(self, symbol: str) -> AsyncIterator[FinanceData]:
        """获取指数成分股"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/index/index.html',
                params={'code': symbol},
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            # 解析成分股数据
            pattern = r'<td><a[^>]*>([^<]+)</a></td><td>([^<]+)</td><td>([\d.]+)</td><td>([\d.]+)%</td>'
            matches = re.findall(pattern, text)

            if matches:
                constituents = []
                for m in matches[:100]:
                    constituents.append({
                        'name': m[0],
                        'code': m[1],
                        'weight': float(m[2]),
                        'change_pct': float(m[3])
                    })
                yield FinanceData(
                    source='index',
                    data_type='index_constituents',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'constituents': constituents, 'count': len(constituents)}
                )

    async def _fetch_info(self, symbol: str) -> AsyncIterator[FinanceData]:
        """获取指数基本信息"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'https://quote.eastmoney.com/concept/{symbol}.html',
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text

            info = {}
            name_match = re.search(r'<h1[^>]*>([^<]+)</h1>', text)
            if name_match:
                info['name'] = name_match.group(1)

            publisher_match = re.search(r'编制机构[：:](\s*[^<]+)', text)
            if publisher_match:
                info['publisher'] = publisher_match.group(1).strip()

            base_date_match = re.search(r'基期[：:](\s*[^<]+)', text)
            if base_date_match:
                info['base_date'] = base_date_match.group(1).strip()

            base_point_match = re.search(r'基点[：:](\s*[^<]+)', text)
            if base_point_match:
                info['base_point'] = base_point_match.group(1).strip()

            if info:
                yield FinanceData(
                    source='index',
                    data_type='index_info',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload=info
                )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'index') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'index':
        return IndexScraper()
    raise ValueError(f"Unknown source: {source}")
