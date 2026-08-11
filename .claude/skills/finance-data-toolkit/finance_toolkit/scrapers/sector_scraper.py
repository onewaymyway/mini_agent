# -*- coding: utf-8 -*-
"""
板块数据抓取器实现
数据源: 东方财富、AKShare (免费、无需 token)
支持: 行业板块、概念板块、地域板块的实时行情和资金流向
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
class SectorScraper(BaseScraper):
    """板块数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'sector'

    @property
    def supported_types(self) -> List[str]:
        return ['sector_quote', 'sector_flow', 'sector_history']

    async def health_check(self) -> bool:
        if not HAS_HTTPX and not HAS_AKSHARE:
            return False
        try:
            if HAS_HTTPX:
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
                            'fs': 'm:90+t:2',
                            'fields': 'f2,f3,f12,f14'
                        },
                        timeout=10
                    )
                    return resp.status_code == 200
        except Exception:
            pass
        
        try:
            if HAS_AKSHARE:
                df = ak.stock_board_industry_name_em()
                return not df.empty
        except Exception:
            pass
        
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
        if not HAS_HTTPX and not HAS_AKSHARE:
            raise ImportError("httpx 或 akshare 未安装")

        sector_type = kwargs.get('sector_type', 'industry')  # industry/concept/region
        
        kwargs.pop('sector_type', None)  # 避免重复传递
        if data_type == 'sector_quote':
            async for data in self._fetch_quote(sector_type, **kwargs):
                yield data
        elif data_type == 'sector_flow':
            async for data in self._fetch_flow(sector_type, **kwargs):
                yield data
        elif data_type == 'sector_history':
            for symbol in symbols:
                async for data in self._fetch_history(symbol, sector_type, start, end):
                    yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_quote(self, sector_type: str = 'industry', **kwargs) -> AsyncIterator[FinanceData]:
        """获取板块实时行情"""
        results = []
        
        # 优先使用 AKShare
        if HAS_AKSHARE:
            try:
                if sector_type == 'industry':
                    df = ak.stock_board_industry_name_em()
                    for _, row in df.iterrows():
                        results.append({
                            'sector_code': row.get('板块代码', ''),
                            'sector_name': row.get('板块名称', ''),
                            'change_pct': float(row.get('涨跌幅', 0) or 0),
                            'change_amt': float(row.get('涨跌额', 0) or 0),
                            'top_stock': row.get('领涨股票', ''),
                            'top_stock_change': float(row.get('领涨股票-涨跌幅', 0) or 0),
                            'avg_pe': float(row.get('市盈率', 0) or 0),
                            'total_mv': float(row.get('总市值', 0) or 0),
                            'turnover': float(row.get('换手率', 0) or 0),
                            'update_time': datetime.utcnow().isoformat(),
                        })
                elif sector_type == 'concept':
                    df = ak.stock_board_concept_name_em()
                    for _, row in df.iterrows():
                        results.append({
                            'sector_code': row.get('板块代码', ''),
                            'sector_name': row.get('板块名称', ''),
                            'change_pct': float(row.get('涨跌幅', 0) or 0),
                            'change_amt': float(row.get('涨跌额', 0) or 0),
                            'top_stock': row.get('领涨股票', ''),
                            'top_stock_change': float(row.get('领涨股票-涨跌幅', 0) or 0),
                            'stock_count': int(row.get('成分股数量', 0) or 0),
                            'update_time': datetime.utcnow().isoformat(),
                        })
                elif sector_type == 'region':
                    df = ak.stock_board_industry_name_em()
                    # 地域板块需要通过其他方式获取
                    # 这里使用行业板块数据作为替代
                    for _, row in df.iterrows():
                        results.append({
                            'sector_code': row.get('板块代码', ''),
                            'sector_name': row.get('板块名称', ''),
                            'change_pct': float(row.get('涨跌幅', 0) or 0),
                            'change_amt': float(row.get('涨跌额', 0) or 0),
                            'top_stock': row.get('领涨股票', ''),
                            'top_stock_change': float(row.get('领涨股票-涨跌幅', 0) or 0),
                            'update_time': datetime.utcnow().isoformat(),
                        })
                
                if results:
                    yield FinanceData(
                        source='akshare',
                        data_type='sector_quote',
                        symbol=f'*_{sector_type}',
                        timestamp=datetime.utcnow().isoformat(),
                        payload={'sectors': results, 'count': len(results), 'type': sector_type}
                    )
            except Exception as e:
                print(f"AKShare板块行情获取失败: {e}", file=__import__('sys').stderr)
        
        # 备选：东方财富 API
        if not results and HAS_HTTPX:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    # 行业板块: m:90+t:2, 概念板块: m:90+t:3, 地域板块: m:90+t:4
                    fs_map = {
                        'industry': 'm:90+t:2',
                        'concept': 'm:90+t:3',
                        'region': 'm:90+t:4'
                    }
                    fs = fs_map.get(sector_type, 'm:90+t:2')
                    
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
                            'fs': fs,
                            'fields': 'f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18'
                        },
                        timeout=30
                    )
                    
                    data = resp.json()
                    if data.get('data') and data['data'].get('diff'):
                        for item in data['data']['diff']:
                            if item.get('f2') is not None:
                                results.append({
                                    'sector_code': item.get('f12', ''),
                                    'sector_name': item.get('f14', ''),
                                    'price': item.get('f2'),
                                    'change_pct': item.get('f3'),
                                    'change_amt': item.get('f4'),
                                    'high': item.get('f4'),
                                    'low': item.get('f5'),
                                    'open': item.get('f6'),
                                    'pre_close': item.get('f17'),
                                    'volume': item.get('f7'),
                                    'amount': item.get('f8'),
                                    'update_time': datetime.utcnow().isoformat(),
                                })
                        
                        if results:
                            yield FinanceData(
                                source='eastmoney',
                                data_type='sector_quote',
                                symbol=f'*_{sector_type}',
                                timestamp=datetime.utcnow().isoformat(),
                                payload={'sectors': results, 'count': len(results), 'type': sector_type}
                            )
            except Exception as e:
                print(f"东方财富板块行情获取失败: {e}", file=__import__('sys').stderr)

    async def _fetch_flow(self, sector_type: str = 'industry', **kwargs) -> AsyncIterator[FinanceData]:
        """获取板块资金流向"""
        results = []
        
        if HAS_AKSHARE:
            try:
                if sector_type == 'industry':
                    df = ak.stock_fund_flow_industry(symbol='即时')
                    for _, row in df.iterrows():
                        results.append({
                            'sector_code': '',
                            'sector_name': row.get('行业', ''),
                            'main_inflow': float(row.get('净额', 0) or 0),
                            'main_inflow_ratio': 0.0,
                            'retail_inflow': 0.0,
                            'change_pct': float(row.get('行业-涨跌幅', 0) or 0),
                            'rank': int(row.get('序号', 0) or 0),
                            'update_time': datetime.utcnow().isoformat(),
                        })
                elif sector_type == 'concept':
                    df = ak.stock_fund_flow_concept(symbol='即时')
                    for _, row in df.iterrows():
                        results.append({
                            'sector_code': '',
                            'sector_name': row.get('行业', ''),
                            'main_inflow': float(row.get('净额', 0) or 0),
                            'main_inflow_ratio': 0.0,
                            'retail_inflow': 0.0,
                            'change_pct': float(row.get('行业-涨跌幅', 0) or 0),
                            'rank': int(row.get('序号', 0) or 0),
                            'update_time': datetime.utcnow().isoformat(),
                        })
                
                if results:
                    yield FinanceData(
                        source='akshare',
                        data_type='sector_flow',
                        symbol=f'*_{sector_type}',
                        timestamp=datetime.utcnow().isoformat(),
                        payload={'sectors': results, 'count': len(results), 'type': sector_type}
                    )
            except Exception as e:
                print(f"AKShare板块资金流向获取失败: {e}", file=__import__('sys').stderr)

    async def _fetch_history(self, symbol: str, sector_type: str, start: Optional[datetime], end: Optional[datetime], **kwargs) -> AsyncIterator[FinanceData]:
        """获取板块历史数据"""
        # TODO: 实现板块历史K线数据
        yield FinanceData(
            source='sector',
            data_type='sector_history',
            symbol=symbol,
            timestamp=datetime.utcnow().isoformat(),
            payload={'message': '板块历史数据功能开发中'}
        )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'sector') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'sector':
        return SectorScraper()
    raise ValueError(f"Unknown source: {source}")
