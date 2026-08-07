# -*- coding: utf-8 -*-
"""
基金数据抓取器实现
数据源: 东方财富、天天基金网 (免费、无需 token)
支持: 基金净值、历史净值、持仓、分类、基金经理等
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
class FundScraper(BaseScraper):
    """基金数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'fund'

    @property
    def supported_types(self) -> List[str]:
        return ['fund_nav', 'fund_holdings', 'fund_rank', 'fund_info', 'fund_history']

    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://fundgz.eastmoney.com/js/fundcode_search.js',
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

        if data_type == 'fund_nav':
            for symbol in symbols:
                async for data in self._fetch_nav(symbol, **kwargs):
                    yield data
        elif data_type == 'fund_holdings':
            for symbol in symbols:
                async for data in self._fetch_holdings(symbol, **kwargs):
                    yield data
        elif data_type == 'fund_rank':
            async for data in self._fetch_rank(**kwargs):
                yield data
        elif data_type == 'fund_info':
            for symbol in symbols:
                async for data in self._fetch_info(symbol):
                    yield data
        elif data_type == 'fund_history':
            for symbol in symbols:
                async for data in self._fetch_history(symbol, start, end):
                    yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_nav(self, symbol: str, **kwargs) -> AsyncIterator[FinanceData]:
        """获取基金净值 (单只基金)"""
        async with httpx.AsyncClient(timeout=30) as client:
            # 东方财富基金净值接口
            resp = await client.get(
                'https://fund.eastmoney.com/f10/F10Data.aspx',
                params={
                    'type': 'lsjz',
                    'code': symbol,
                    'page': '1',
                    'sdate': (kwargs.get('start') or datetime.now() - __import__('datetime').timedelta(days=30)).strftime('%Y-%m-%d'),
                    'edate': (kwargs.get('end') or datetime.now()).strftime('%Y-%m-%d'),
                    'per': '20'
                },
                timeout=30
            )

            if resp.status_code != 200:
                return

            # 解析 HTML 表格数据
            import re
            text = resp.text
            # 提取净值数据
            pattern = r'<td>(\d{4}-\d{2}-\d{2})</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)%</td>'
            matches = re.findall(pattern, text)

            if matches:
                records = []
                for m in matches:
                    records.append({
                        'date': m[0],
                        'nav': float(m[1]),
                        'acc_nav': float(m[2]),
                        'daily_return': float(m[3]),
                        'accum_return': float(m[4])
                    })
                yield FinanceData(
                    source='fund',
                    data_type='fund_nav',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )

    async def _fetch_holdings(self, symbol: str, **kwargs) -> AsyncIterator[FinanceData]:
        """获取基金持仓"""
        async with httpx.AsyncClient(timeout=30) as client:
            # 股票持仓
            resp = await client.get(
                'https://fundf10.eastmoney.com/FundArchivesDatas.aspx',
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
            # 解析持仓数据
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
                    source='fund',
                    data_type='fund_holdings',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'holdings': holdings}
                )

    async def _fetch_rank(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取基金排行榜"""
        async with httpx.AsyncClient(timeout=30) as client:
            rank_type = kwargs.get('rank_type', '1')  # 1=近1月, 2=近3月, 3=近6月, 4=近1年
            fund_type = kwargs.get('fund_type', 'gp')  # gp=股票型, hh=混合型, zq=债券型

            resp = await client.get(
                'https://fund.eastmoney.com/data/rankhandler.aspx',
                params={
                    'op': 'ph',
                    'dt': 'kf',
                    'ft': fund_type,
                    'rs': '',
                    'gs': '',
                    'sc': 'zjfz',
                    'st': 'desc',
                    'pi': '1',
                    'pn': '50',
                    'dx': '1',
                    'v': '0.123456789'
                },
                timeout=30
            )

            if resp.status_code != 200:
                return

            data = resp.json()
            if data.get('data'):
                funds = []
                for item in data['data'][:50]:
                    parts = item.split(',')
                    if len(parts) >= 12:
                        funds.append({
                            'code': parts[0],
                            'name': parts[1],
                            'nav': parts[2],
                            'acc_nav': parts[3],
                            'daily_return': parts[4],
                            'return_1m': parts[5],
                            'return_3m': parts[6],
                            'return_6m': parts[7],
                            'return_1y': parts[8],
                            'return_3y': parts[9],
                            'return_5y': parts[10],
                            'fund_type': parts[11]
                        })
                yield FinanceData(
                    source='fund',
                    data_type='fund_rank',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'funds': funds, 'count': len(funds)}
                )

    async def _fetch_info(self, symbol: str) -> AsyncIterator[FinanceData]:
        """获取基金基本信息"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'https://fund.eastmoney.com/{symbol}.html',
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text

            # 提取基本信息
            info = {}
            name_match = re.search(r'<h1[^>]*>([^<]+)</h1>', text)
            if name_match:
                info['name'] = name_match.group(1)

            # 基金类型
            type_match = re.search(r'基金类型[：:](\s*[^<]+)', text)
            if type_match:
                info['type'] = type_match.group(1).strip()

            # 基金规模
            size_match = re.search(r'基金规模[：:](\s*[^<]+)', text)
            if size_match:
                info['size'] = size_match.group(1).strip()

            # 成立日期
            date_match = re.search(r'成立日期[：:](\s*[^<]+)', text)
            if date_match:
                info['establish_date'] = date_match.group(1).strip()

            # 基金经理
            manager_match = re.search(r'基金经理[：:](\s*[^<]+)', text)
            if manager_match:
                info['manager'] = manager_match.group(1).strip()

            if info:
                yield FinanceData(
                    source='fund',
                    data_type='fund_info',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload=info
                )

    async def _fetch_history(self, symbol: str, start: Optional[datetime], end: Optional[datetime], **kwargs) -> AsyncIterator[FinanceData]:
        """获取基金历史净值"""
        async with httpx.AsyncClient(timeout=30) as client:
            beg = (start or datetime.now() - __import__('datetime').timedelta(days=365)).strftime('%Y-%m-%d')
            ed = (end or datetime.now()).strftime('%Y-%m-%d')

            resp = await client.get(
                'https://fund.eastmoney.com/f10/F10Data.aspx',
                params={
                    'type': 'lsjz',
                    'code': symbol,
                    'page': '1',
                    'sdate': beg,
                    'edate': ed,
                    'per': '100'
                },
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            pattern = r'<td>(\d{4}-\d{2}-\d{2})</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)%</td>'
            matches = re.findall(pattern, text)

            if matches:
                records = []
                for m in matches:
                    records.append({
                        'date': m[0],
                        'nav': float(m[1]),
                        'acc_nav': float(m[2]),
                        'daily_return': float(m[3]),
                        'accum_return': float(m[4])
                    })
                yield FinanceData(
                    source='fund',
                    data_type='fund_history',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records), 'start': beg, 'end': ed}
                )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'fund') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'fund':
        return FundScraper()
    raise ValueError(f"Unknown source: {source}")
