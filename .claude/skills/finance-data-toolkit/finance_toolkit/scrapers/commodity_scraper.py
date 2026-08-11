# -*- coding: utf-8 -*-
"""
大宗商品数据抓取器实现
数据源: 新浪财经、东方财富、AKShare (免费、无需 token)
支持: 贵金属、能源、有色金属、农产品、期货合约、持仓数据等
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


# 新浪财经商品代码映射
SINA_COMMODITY_CODES = {
    # 贵金属
    'GL_GOLD': 'gl_gold',
    'GL_SILVER': 'gl_silver',
    'GL_PLATINUM': 'gl_platinum',
    'GL_PALLADIUM': 'gl_palladium',
    # 能源
    'CL_CRUDE': 'cl',
    'BR_CRUDE': 'br',
    'NG_GAS': 'ng',
    'RU_OIL': 'ru',
    # 有色金属
    'SF_CU': 'sf_cu',
    'SF_AL': 'sf_al',
    'SF_ZN': 'sf_zn',
    'SF_PB': 'sf_pb',
    'SF_NI': 'sf_ni',
    'SF_SN': 'sf_sn',
    # 农产品
    'SOYBEAN': 'soybean',
    'CORN': 'corn',
    'WHEAT': 'wheat',
    'COTTON': 'cotton',
    'SUGAR': 'sugar',
    'SOYOIL': 'soyoil',
    'PALMOIL': 'palmoil',
}

# 商品分类
COMMODITY_CATEGORIES = {
    'precious_metal': ['GL_GOLD', 'GL_SILVER', 'GL_PLATINUM', 'GL_PALLADIUM'],
    'energy': ['CL_CRUDE', 'BR_CRUDE', 'NG_GAS', 'RU_OIL'],
    'base_metal': ['SF_CU', 'SF_AL', 'SF_ZN', 'SF_PB', 'SF_NI', 'SF_SN'],
    'agriculture': ['SOYBEAN', 'CORN', 'WHEAT', 'COTTON', 'SUGAR', 'SOYOIL', 'PALMOIL'],
}


@register_scraper
class CommodityScraper(BaseScraper):
    """大宗商品数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'commodity'

    @property
    def supported_types(self) -> List[str]:
        return [
            'commodity_quote', 'commodity_kline',
            'precious_metal', 'energy', 'base_metal', 'agriculture',
            'futures_contract', 'futures_oi',
        ]

    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://hq.sinajs.cn/list=gl_gold',
                    headers={'Referer': 'https://finance.sina.com.cn/'},
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
        if data_type == 'commodity_quote':
            async for data in self._fetch_quotes(symbols, **kwargs):
                yield data
        elif data_type == 'commodity_kline':
            for symbol in symbols:
                async for data in self._fetch_kline(symbol, start, end, **kwargs):
                    yield data
        elif data_type == 'precious_metal':
            async for data in self._fetch_precious_metals(**kwargs):
                yield data
        elif data_type == 'energy':
            async for data in self._fetch_energy(**kwargs):
                yield data
        elif data_type == 'base_metal':
            async for data in self._fetch_base_metals(**kwargs):
                yield data
        elif data_type == 'agriculture':
            async for data in self._fetch_agriculture(**kwargs):
                yield data
        elif data_type == 'futures_contract':
            for symbol in symbols:
                async for data in self._fetch_futures_contract(symbol, **kwargs):
                    yield data
        elif data_type == 'futures_oi':
            for symbol in symbols:
                async for data in self._fetch_futures_oi(symbol, **kwargs):
                    yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_quotes(self, symbols: List[str], **kwargs) -> AsyncIterator[FinanceData]:
        """获取商品实时行情 (新浪)"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        async with httpx.AsyncClient(timeout=30) as client:
            codes = []
            for sym in symbols:
                code = SINA_COMMODITY_CODES.get(sym.upper(), sym.upper())
                codes.append(code)

            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            headers = {
                'Referer': 'https://finance.sina.com.cn/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            try:
                resp = await client.get(url, headers=headers, timeout=30)
                resp.encoding = 'gbk'

                if resp.status_code != 200:
                    yield FinanceData(
                        source='commodity',
                        data_type='commodity_quote',
                        symbol='*',
                        timestamp=datetime.utcnow(),
                        payload={'error': f'HTTP {resp.status_code}'}
                    )
                    return

                quotes = []
                lines = resp.text.strip().split(';')
                for line in lines:
                    if not line or '=' not in line:
                        continue
                    var_part, data_part = line.split('=', 1)
                    code = var_part.strip().replace('var hq_str_', '').replace('"', '')
                    if not data_part or data_part.strip() == '""':
                        continue
                    data_str = data_part.strip().strip('"')
                    fields = data_str.split(',')
                    if len(fields) >= 8:
                        try:
                            quotes.append({
                                'code': code,
                                'name': fields[0] if fields[0] else code,
                                'price': float(fields[1]) if fields[1] else 0,
                                'open': float(fields[2]) if len(fields) > 2 and fields[2] else 0,
                                'high': float(fields[3]) if len(fields) > 3 and fields[3] else 0,
                                'low': float(fields[4]) if len(fields) > 4 and fields[4] else 0,
                                'pre_close': float(fields[5]) if len(fields) > 5 and fields[5] else 0,
                                'change': float(fields[6]) if len(fields) > 6 and fields[6] else 0,
                                'change_pct': float(fields[7]) if len(fields) > 7 and fields[7] else 0,
                                'volume': fields[8] if len(fields) > 8 else '',
                                'time': fields[-1] if fields else '',
                            })
                        except (ValueError, IndexError):
                            continue

                yield FinanceData(
                    source='commodity',
                    data_type='commodity_quote',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'quotes': quotes, 'count': len(quotes)}
                )
            except Exception as e:
                yield FinanceData(
                    source='commodity',
                    data_type='commodity_quote',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_kline(self, symbol: str, start: Optional[datetime], end: Optional[datetime], **kwargs) -> AsyncIterator[FinanceData]:
        """获取商品历史K线 (新浪)"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        async with httpx.AsyncClient(timeout=30) as client:
            code = SINA_COMMODITY_CODES.get(symbol.upper(), symbol.upper())
            url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"
            params = {'symbol': code, 'scale': '240', 'datalen': '100'}

            try:
                resp = await client.get(url, params=params, timeout=30)
                text = resp.text

                idx = text.find('var=(')
                if idx < 0:
                    idx = text.find('=(')
                if idx < 0:
                    return

                end_idx = text.rfind(');')
                if end_idx < 0:
                    end_idx = text.rfind(')')
                json_str = text[idx + 5:end_idx] if idx >= 0 else text[idx + 2:end_idx]

                import json
                data = json.loads(json_str)
                if not data:
                    return

                records = []
                for row in data:
                    records.append({
                        'date': row.get('day', ''),
                        'open': float(row.get('open', 0)),
                        'close': float(row.get('close', 0)),
                        'high': float(row.get('high', 0)),
                        'low': float(row.get('low', 0)),
                        'volume': int(row.get('volume', 0)),
                    })

                yield FinanceData(
                    source='commodity',
                    data_type='commodity_kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )
            except Exception as e:
                yield FinanceData(
                    source='commodity',
                    data_type='commodity_kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_precious_metals(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取贵金属价格 (新浪 + AKShare)"""
        # 优先使用 AKShare
        if HAS_AKSHARE:
            try:
                df = ak.gold_bullion_spot()
                records = []
                for _, row in df.iterrows():
                    records.append(row.to_dict())
                yield FinanceData(
                    source='commodity',
                    data_type='precious_metal',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records), 'source': 'akshare'}
                )
                return
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"AKShare 贵金属获取失败: {e}")

        # 降级到新浪
        if not HAS_HTTPX:
            yield FinanceData(
                source='commodity',
                data_type='precious_metal',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'error': 'httpx 未安装'}
            )
            return

        async with httpx.AsyncClient(timeout=30) as client:
            codes = ['gl_gold', 'gl_silver', 'gl_platinum', 'gl_palladium']
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            headers = {
                'Referer': 'https://finance.sina.com.cn/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            try:
                resp = await client.get(url, headers=headers, timeout=30)
                resp.encoding = 'gbk'

                quotes = []
                lines = resp.text.strip().split(';')
                for line in lines:
                    if not line or '=' not in line:
                        continue
                    var_part, data_part = line.split('=', 1)
                    code = var_part.strip().replace('var hq_str_', '').replace('"', '')
                    if not data_part or data_part.strip() == '""':
                        continue
                    data_str = data_part.strip().strip('"')
                    fields = data_str.split(',')
                    if len(fields) >= 8:
                        try:
                            quotes.append({
                                'code': code,
                                'name': fields[0] if fields[0] else code,
                                'price': float(fields[1]) if fields[1] else 0,
                                'change_pct': float(fields[7]) if len(fields) > 7 and fields[7] else 0,
                                'category': 'precious_metal',
                            })
                        except (ValueError, IndexError):
                            continue

                yield FinanceData(
                    source='commodity',
                    data_type='precious_metal',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'quotes': quotes, 'count': len(quotes), 'source': 'sina'}
                )
            except Exception as e:
                yield FinanceData(
                    source='commodity',
                    data_type='precious_metal',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_energy(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取能源价格 (新浪 + AKShare)"""
        if HAS_AKSHARE:
            try:
                df = ak.futures_main_sina()
                energy_symbols = ['cl', 'br', 'ng', 'ru']
                records = []
                for _, row in df.iterrows():
                    if row.get('symbol', '') in energy_symbols:
                        records.append(row.to_dict())
                if records:
                    yield FinanceData(
                        source='commodity',
                        data_type='energy',
                        symbol='*',
                        timestamp=datetime.utcnow(),
                        payload={'records': records, 'count': len(records), 'source': 'akshare'}
                    )
                    return
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"AKShare 能源获取失败: {e}")

        if not HAS_HTTPX:
            yield FinanceData(
                source='commodity',
                data_type='energy',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'error': 'httpx 未安装'}
            )
            return

        async with httpx.AsyncClient(timeout=30) as client:
            codes = ['cl', 'br', 'ng', 'ru']
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            headers = {
                'Referer': 'https://finance.sina.com.cn/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            try:
                resp = await client.get(url, headers=headers, timeout=30)
                resp.encoding = 'gbk'

                quotes = []
                lines = resp.text.strip().split(';')
                for line in lines:
                    if not line or '=' not in line:
                        continue
                    var_part, data_part = line.split('=', 1)
                    code = var_part.strip().replace('var hq_str_', '').replace('"', '')
                    if not data_part or data_part.strip() == '""':
                        continue
                    data_str = data_part.strip().strip('"')
                    fields = data_str.split(',')
                    if len(fields) >= 8:
                        try:
                            quotes.append({
                                'code': code,
                                'name': fields[0] if fields[0] else code,
                                'price': float(fields[1]) if fields[1] else 0,
                                'change_pct': float(fields[7]) if len(fields) > 7 and fields[7] else 0,
                                'category': 'energy',
                            })
                        except (ValueError, IndexError):
                            continue

                yield FinanceData(
                    source='commodity',
                    data_type='energy',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'quotes': quotes, 'count': len(quotes), 'source': 'sina'}
                )
            except Exception as e:
                yield FinanceData(
                    source='commodity',
                    data_type='energy',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_base_metals(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取有色金属价格 (新浪 + AKShare)"""
        if HAS_AKSHARE:
            try:
                df = ak.futures_main_sina()
                metal_symbols = ['cu', 'al', 'zn', 'pb', 'ni', 'sn']
                records = []
                for _, row in df.iterrows():
                    if row.get('symbol', '') in metal_symbols:
                        records.append(row.to_dict())
                if records:
                    yield FinanceData(
                        source='commodity',
                        data_type='base_metal',
                        symbol='*',
                        timestamp=datetime.utcnow(),
                        payload={'records': records, 'count': len(records), 'source': 'akshare'}
                    )
                    return
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"AKShare 有色金属获取失败: {e}")

        if not HAS_HTTPX:
            yield FinanceData(
                source='commodity',
                data_type='base_metal',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'error': 'httpx 未安装'}
            )
            return

        async with httpx.AsyncClient(timeout=30) as client:
            codes = ['sf_cu', 'sf_al', 'sf_zn', 'sf_pb', 'sf_ni', 'sf_sn']
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            headers = {
                'Referer': 'https://finance.sina.com.cn/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            try:
                resp = await client.get(url, headers=headers, timeout=30)
                resp.encoding = 'gbk'

                quotes = []
                lines = resp.text.strip().split(';')
                for line in lines:
                    if not line or '=' not in line:
                        continue
                    var_part, data_part = line.split('=', 1)
                    code = var_part.strip().replace('var hq_str_', '').replace('"', '')
                    if not data_part or data_part.strip() == '""':
                        continue
                    data_str = data_part.strip().strip('"')
                    fields = data_str.split(',')
                    if len(fields) >= 8:
                        try:
                            quotes.append({
                                'code': code,
                                'name': fields[0] if fields[0] else code,
                                'price': float(fields[1]) if fields[1] else 0,
                                'change_pct': float(fields[7]) if len(fields) > 7 and fields[7] else 0,
                                'category': 'base_metal',
                            })
                        except (ValueError, IndexError):
                            continue

                yield FinanceData(
                    source='commodity',
                    data_type='base_metal',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'quotes': quotes, 'count': len(quotes), 'source': 'sina'}
                )
            except Exception as e:
                yield FinanceData(
                    source='commodity',
                    data_type='base_metal',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_agriculture(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取农产品价格 (新浪 + AKShare)"""
        if HAS_AKSHARE:
            try:
                df = ak.futures_main_sina()
                ag_symbols = ['a', 'b', 'c', 'cs', 'l', 'm', 'p', 'rb', 'si', 'sm', 'v', 'y']
                records = []
                for _, row in df.iterrows():
                    if row.get('symbol', '') in ag_symbols:
                        records.append(row.to_dict())
                if records:
                    yield FinanceData(
                        source='commodity',
                        data_type='agriculture',
                        symbol='*',
                        timestamp=datetime.utcnow(),
                        payload={'records': records, 'count': len(records), 'source': 'akshare'}
                    )
                    return
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"AKShare 农产品获取失败: {e}")

        if not HAS_HTTPX:
            yield FinanceData(
                source='commodity',
                data_type='agriculture',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'error': 'httpx 未安装'}
            )
            return

        async with httpx.AsyncClient(timeout=30) as client:
            codes = ['soybean', 'corn', 'wheat', 'cotton', 'sugar', 'soyoil', 'palmoil']
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            headers = {
                'Referer': 'https://finance.sina.com.cn/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            try:
                resp = await client.get(url, headers=headers, timeout=30)
                resp.encoding = 'gbk'

                quotes = []
                lines = resp.text.strip().split(';')
                for line in lines:
                    if not line or '=' not in line:
                        continue
                    var_part, data_part = line.split('=', 1)
                    code = var_part.strip().replace('var hq_str_', '').replace('"', '')
                    if not data_part or data_part.strip() == '""':
                        continue
                    data_str = data_part.strip().strip('"')
                    fields = data_str.split(',')
                    if len(fields) >= 8:
                        try:
                            quotes.append({
                                'code': code,
                                'name': fields[0] if fields[0] else code,
                                'price': float(fields[1]) if fields[1] else 0,
                                'change_pct': float(fields[7]) if len(fields) > 7 and fields[7] else 0,
                                'category': 'agriculture',
                            })
                        except (ValueError, IndexError):
                            continue

                yield FinanceData(
                    source='commodity',
                    data_type='agriculture',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'quotes': quotes, 'count': len(quotes), 'source': 'sina'}
                )
            except Exception as e:
                yield FinanceData(
                    source='commodity',
                    data_type='agriculture',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_futures_contract(self, symbol: str, **kwargs) -> AsyncIterator[FinanceData]:
        """获取期货合约详情"""
        if not HAS_HTTPX:
            yield FinanceData(
                source='commodity',
                data_type='futures_contract',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'error': 'httpx 未安装'}
            )
            return

        async with httpx.AsyncClient(timeout=30) as client:
            # 从东方财富获取期货合约信息
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
                    'fs': 'm:113+t:2',
                    'fields': 'f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18'
                },
                timeout=30
            )

            if resp.status_code != 200:
                yield FinanceData(
                    source='commodity',
                    data_type='futures_contract',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'error': f'HTTP {resp.status_code}'}
                )
                return

            data = resp.json()
            if data.get('data') and data['data'].get('diff'):
                contracts = []
                for item in data['data']['diff'][:20]:
                    if item.get('f2') is not None:
                        contracts.append({
                            'name': item.get('f14', ''),
                            'code': item.get('f12', ''),
                            'price': item.get('f2'),
                            'change_pct': item.get('f3'),
                            'high': item.get('f4'),
                            'low': item.get('f5'),
                            'open': item.get('f6'),
                            'pre_close': item.get('f17'),
                            'volume': item.get('f7'),
                            'hold': item.get('f8'),
                        })
                yield FinanceData(
                    source='commodity',
                    data_type='futures_contract',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'contracts': contracts, 'count': len(contracts)}
                )
            else:
                yield FinanceData(
                    source='commodity',
                    data_type='futures_contract',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'contracts': [], 'count': 0}
                )

    async def _fetch_futures_oi(self, symbol: str, **kwargs) -> AsyncIterator[FinanceData]:
        """获取期货持仓数据 (AKShare)"""
        if not HAS_AKSHARE:
            yield FinanceData(
                source='commodity',
                data_type='futures_oi',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'error': 'akshare 未安装'}
            )
            return

        try:
            df = ak.futures_display_main_sina()
            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())

            yield FinanceData(
                source='commodity',
                data_type='futures_oi',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'records': records, 'count': len(records)}
            )
        except Exception as e:
            yield FinanceData(
                source='commodity',
                data_type='futures_oi',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'error': str(e)}
            )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'commodity') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'commodity':
        return CommodityScraper()
    raise ValueError(f"Unknown source: {source}")
