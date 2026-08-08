# -*- coding: utf-8 -*-
"""
ETF/期权数据抓取器实现
数据源: AKShare、东方财富 (免费、无需 token)
支持: ETF实时行情、ETF历史K线、期权数据等
"""

from datetime import datetime
from typing import List, Optional, AsyncIterator

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    httpx = None

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

from ..core import BaseScraper, FinanceData, register_scraper


@register_scraper
class ETFScraper(BaseScraper):
    """ETF/期权数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'etf'

    @property
    def supported_types(self) -> List[str]:
        return ['etf_quote', 'etf_kline', 'etf_holdings', 'option_quote', 'option_chain']

    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://push2.eastmoney.com/api/qt/clist/get',
                    params={
                        'pn': '1',
                        'pz': '10',
                        'po': '1',
                        'np': '1',
                        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                        'fltt': '2',
                        'invt': '2',
                        'fid': 'f3',
                        'fs': 'm:0+t:14',
                        'fields': 'f12,f14,f2,f3'
                    },
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
        if data_type == 'etf_quote':
            async for data in self._fetch_quotes(symbols, **kwargs):
                yield data
        elif data_type == 'etf_kline':
            for symbol in symbols:
                async for data in self._fetch_kline(symbol, start, end, **kwargs):
                    yield data
        elif data_type == 'etf_holdings':
            for symbol in symbols:
                async for data in self._fetch_holdings(symbol, **kwargs):
                    yield data
        elif data_type == 'option_quote':
            async for data in self._fetch_option_quotes(**kwargs):
                yield data
        elif data_type == 'option_chain':
            for symbol in symbols:
                async for data in self._fetch_option_chain(symbol, **kwargs):
                    yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_quotes(self, symbols: List[str], **kwargs) -> AsyncIterator[FinanceData]:
        """获取ETF实时行情"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        async with httpx.AsyncClient(timeout=30) as client:
            # 获取ETF列表行情
            resp = await client.get(
                'https://push2.eastmoney.com/api/qt/clist/get',
                params={
                    'pn': '1',
                    'pz': '500',
                    'po': '1',
                    'np': '1',
                    'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                    'fltt': '2',
                    'invt': '2',
                    'fid': 'f3',
                    'fs': 'm:0+t:14,m:0+t:23,m:1+t:2,m:1+t:23',
                    'fields': 'f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18'
                },
                timeout=30
            )

            if resp.status_code != 200:
                return

            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                quotes = []
                for item in data['data']['diff']:
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
                            'amount': item.get('f8'),
                        })

                # 如果指定了symbols，过滤
                if symbols:
                    quotes = [q for q in quotes if q['code'] in symbols or q['name'] in symbols]

                yield FinanceData(
                    source='etf',
                    data_type='etf_quote',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'quotes': quotes, 'count': len(quotes)}
                )

    async def _fetch_kline(self, symbol: str, start: Optional[datetime], end: Optional[datetime], **kwargs) -> AsyncIterator[FinanceData]:
        """获取ETF历史K线"""
        if not HAS_AKSHARE:
            raise ImportError("akshare 未安装，请运行：pip install akshare")

        try:
            code = symbol.split('.')[0]
            beg = (start or datetime.now() - __import__('datetime').timedelta(days=365)).strftime('%Y%m%d')
            ed = (end or datetime.now()).strftime('%Y%m%d')

            df = ak.fund_etf_hist_sina(symbol=code, period='daily', start_date=beg, end_date=ed, adjust='qfq')

            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())

            yield FinanceData(
                source='etf',
                data_type='etf_kline',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'records': records, 'count': len(records), 'start': beg, 'end': ed}
            )
        except Exception as e:
            yield FinanceData(
                source='etf',
                data_type='etf_kline',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'error': str(e)}
            )

    async def _fetch_holdings(self, symbol: str, **kwargs) -> AsyncIterator[FinanceData]:
        """获取ETF持仓"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    f'https://fundf10.eastmoney.com/FundArchivesDatas.aspx',
                    params={
                        'type': 'jjcc',
                        'code': symbol,
                        'topline': '10',
                        'year': 'all',
                        'month': 'all'
                    },
                    timeout=30
                )

                if resp.status_code != 200:
                    return

                import re
                text = resp.text
                pattern = r'<td><a[^>]*>([^<]+)</a></td><td>([^<]+)</td><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td>'
                matches = re.findall(pattern, text)

                if matches:
                    holdings = []
                    for m in matches[:10]:
                        holdings.append({
                            'stock_name': m[0],
                            'stock_code': m[1],
                            'shares': m[2],
                            'market_value': m[3],
                            'weight': m[4]
                        })
                    yield FinanceData(
                        source='etf',
                        data_type='etf_holdings',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload={'holdings': holdings}
                    )
            except Exception as e:
                yield FinanceData(
                    source='etf',
                    data_type='etf_holdings',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_option_quotes(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取期权实时行情"""
        if not HAS_AKSHARE:
            raise ImportError("akshare 未安装，请运行：pip install akshare")

        try:
            # 获取期权实时行情
            df = ak.option_zh_daily()
            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())

            yield FinanceData(
                source='etf',
                data_type='option_quote',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'records': records, 'count': len(records)}
            )
        except Exception as e:
            yield FinanceData(
                source='etf',
                data_type='option_quote',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'error': str(e)}
            )

    async def _fetch_option_chain(self, symbol: str, **kwargs) -> AsyncIterator[FinanceData]:
        """获取期权链数据"""
        if not HAS_AKSHARE:
            raise ImportError("akshare 未安装，请运行：pip install akshare")

        try:
            # 获取期权链
            df = ak.option_zh_hs_daily(symbol=symbol)
            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())

            yield FinanceData(
                source='etf',
                data_type='option_chain',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'records': records, 'count': len(records)}
            )
        except Exception as e:
            yield FinanceData(
                source='etf',
                data_type='option_chain',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'error': str(e)}
            )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'etf') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'etf':
        return ETFScraper()
    raise ValueError(f"Unknown source: {source}")
