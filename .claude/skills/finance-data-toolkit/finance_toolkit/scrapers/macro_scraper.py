# -*- coding: utf-8 -*-
"""
宏观经济数据抓取器实现
数据源: 东方财富、国家统计局 (免费、无需 token)
支持: GDP、CPI、PMI、利率、汇率等宏观经济指标
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
class MacroScraper(BaseScraper):
    """宏观经济数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'macro'

    @property
    def supported_types(self) -> List[str]:
        return ['gdp', 'cpi', 'pmi', 'interest_rate', 'exchange_rate', 'money_supply']

    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://data.eastmoney.com/cjsj/',
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

        if data_type == 'gdp':
            async for data in self._fetch_gdp(**kwargs):
                yield data
        elif data_type == 'cpi':
            async for data in self._fetch_cpi(**kwargs):
                yield data
        elif data_type == 'pmi':
            async for data in self._fetch_pmi(**kwargs):
                yield data
        elif data_type == 'interest_rate':
            async for data in self._fetch_interest_rate(**kwargs):
                yield data
        elif data_type == 'exchange_rate':
            async for data in self._fetch_exchange_rate(**kwargs):
                yield data
        elif data_type == 'money_supply':
            async for data in self._fetch_money_supply(**kwargs):
                yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_gdp(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取GDP数据"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/cjsj/gdp.html',
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            pattern = r'<td>(\d{4}Q\d)</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td>'
            matches = re.findall(pattern, text)

            if matches:
                records = []
                for m in matches[:20]:
                    records.append({
                        'quarter': m[0],
                        'gdp': float(m[1]),
                        'growth_rate': float(m[2]),
                        'per_capita': float(m[3])
                    })
                yield FinanceData(
                    source='macro',
                    data_type='gdp',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )

    async def _fetch_cpi(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取CPI数据"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/cjsj/cpi.html',
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            pattern = r'<td>(\d{4}-\d{2})</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td>'
            matches = re.findall(pattern, text)

            if matches:
                records = []
                for m in matches[:24]:
                    records.append({
                        'date': m[0],
                        'cpi': float(m[1]),
                        'yoy': float(m[2]),
                        'food': float(m[3])
                    })
                yield FinanceData(
                    source='macro',
                    data_type='cpi',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )

    async def _fetch_pmi(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取PMI数据"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/cjsj/pmi.html',
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            pattern = r'<td>(\d{4}-\d{2})</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td>'
            matches = re.findall(pattern, text)

            if matches:
                records = []
                for m in matches[:24]:
                    records.append({
                        'date': m[0],
                        'manufacturing_pmi': float(m[1]),
                        'non_manufacturing_pmi': float(m[2]),
                        'new_order_pmi': float(m[3])
                    })
                yield FinanceData(
                    source='macro',
                    data_type='pmi',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )

    async def _fetch_interest_rate(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取利率数据"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/cjsj/lilv.html',
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            pattern = r'<td>(\d{4}-\d{2}-\d{2})</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td>'
            matches = re.findall(pattern, text)

            if matches:
                records = []
                for m in matches[:12]:
                    records.append({
                        'date': m[0],
                        'deposit_rate': float(m[1]),
                        'loan_rate': float(m[2]),
                        'mlf_rate': float(m[3])
                    })
                yield FinanceData(
                    source='macro',
                    data_type='interest_rate',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )

    async def _fetch_exchange_rate(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取汇率数据"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/cjsj/huilv.html',
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            pattern = r'<td>(\d{4}-\d{2}-\d{2})</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td>'
            matches = re.findall(pattern, text)

            if matches:
                records = []
                for m in matches[:30]:
                    records.append({
                        'date': m[0],
                        'usd_cny': float(m[1]),
                        'eur_cny': float(m[2]),
                        'jpy_cny': float(m[3])
                    })
                yield FinanceData(
                    source='macro',
                    data_type='exchange_rate',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )

    async def _fetch_money_supply(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取货币供应量数据"""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                'https://data.eastmoney.com/cjsj/hb.html',
                timeout=30
            )

            if resp.status_code != 200:
                return

            import re
            text = resp.text
            pattern = r'<td>(\d{4}-\d{2})</td><td>([\d.]+)</td><td>([\d.]+)</td><td>([\d.]+)</td>'
            matches = re.findall(pattern, text)

            if matches:
                records = []
                for m in matches[:12]:
                    records.append({
                        'date': m[0],
                        'm0': float(m[1]),
                        'm1': float(m[2]),
                        'm2': float(m[3])
                    })
                yield FinanceData(
                    source='macro',
                    data_type='money_supply',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'macro') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'macro':
        return MacroScraper()
    raise ValueError(f"Unknown source: {source}")
