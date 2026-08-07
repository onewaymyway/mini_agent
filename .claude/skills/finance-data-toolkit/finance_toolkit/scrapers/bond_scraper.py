# -*- coding: utf-8 -*-
"""
债券数据抓取器实现
数据源: 东方财富、中国债券信息网 (免费、无需 token)
支持: 国债收益率、企业债、可转债、债券行情等
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
class BondScraper(BaseScraper):
    """债券数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'bond'

    @property
    def supported_types(self) -> List[str]:
        return ['bond_yield', 'bond_quote', 'convertible', 'bond_info']

    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://data.eastmoney.com/cjsj/gszs.html',
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

        if data_type == 'bond_yield':
            async for data in self._fetch_yield_rates(**kwargs):
                yield data
        elif data_type == 'bond_quote':
            for symbol in symbols:
                async for data in self._fetch_quote(symbol):
                    yield data
        elif data_type == 'convertible':
            for symbol in symbols:
                async for data in self._fetch_convertible(symbol):
                    yield data
        elif data_type == 'bond_info':
            for symbol in symbols:
                async for data in self._fetch_info(symbol):
                    yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_yield_rates(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取国债收益率曲线"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/cjsj/gszs.html',
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            # 解析收益率数据
            pattern = r'<td>(\d{4}-\d{2}-\d{2})</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td>'
            matches = re.findall(pattern, text)

            if matches:
                records = []
                for m in matches[:30]:
                    records.append({
                        'date': m[0],
                        '1y': float(m[1]),
                        '2y': float(m[2]),
                        '3y': float(m[3]),
                        '5y': float(m[4]),
                        '10y': float(m[5])
                    })
                yield FinanceData(
                    source='bond',
                    data_type='bond_yield',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )

    async def _fetch_quote(self, symbol: str) -> AsyncIterator[FinanceData]:
        """获取债券行情"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/bond/',
                params={'code': symbol},
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            # 解析债券行情数据
            pattern = r'<td>([^<]+)</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)%</td>'
            matches = re.findall(pattern, text)

            if matches:
                quotes = []
                for m in matches[:20]:
                    quotes.append({
                        'name': m[0],
                        'code': m[1],
                        'price': float(m[2]),
                        'yield_rate': float(m[3]),
                        'change': float(m[4]),
                        'change_pct': float(m[5])
                    })
                yield FinanceData(
                    source='bond',
                    data_type='bond_quote',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'quotes': quotes, 'count': len(quotes)}
                )

    async def _fetch_convertible(self, symbol: str) -> AsyncIterator[FinanceData]:
        """获取可转债数据"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/bond/kzz.html',
                params={'code': symbol},
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            # 解析可转债数据
            pattern = r'<td>([^<]+)</td><td>([^<]+)</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)%</td><td>([\d.]+)</td>'
            matches = re.findall(pattern, text)

            if matches:
                convertibles = []
                for m in matches[:20]:
                    convertibles.append({
                        'name': m[0],
                        'code': m[1],
                        'price': float(m[2]),
                        'stock_price': float(m[3]),
                        'change_pct': float(m[4]),
                        'premium': float(m[5])
                    })
                yield FinanceData(
                    source='bond',
                    data_type='convertible',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'convertibles': convertibles, 'count': len(convertibles)}
                )

    async def _fetch_info(self, symbol: str) -> AsyncIterator[FinanceData]:
        """获取债券基本信息"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f'https://data.eastmoney.com/bond/{symbol}.html',
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

            type_match = re.search(r'债券类型[：:](\s*[^<]+)', text)
            if type_match:
                info['type'] = type_match.group(1).strip()

            rating_match = re.search(r'信用评级[：:](\s*[^<]+)', text)
            if rating_match:
                info['rating'] = rating_match.group(1).strip()

            maturity_match = re.search(r'到期日期[：:](\s*[^<]+)', text)
            if maturity_match:
                info['maturity_date'] = maturity_match.group(1).strip()

            if info:
                yield FinanceData(
                    source='bond',
                    data_type='bond_info',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload=info
                )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'bond') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'bond':
        return BondScraper()
    raise ValueError(f"Unknown source: {source}")
