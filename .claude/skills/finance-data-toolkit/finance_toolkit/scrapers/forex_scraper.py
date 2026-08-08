# -*- coding: utf-8 -*-
"""
外汇数据抓取器实现
数据源: 新浪财经、AKShare、东方财富 (免费、无需 token)
支持: 实时汇率、历史汇率、人民币中间价、主要货币对等
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


# 新浪外汇代码映射
SINA_FOREX_CODES = {
    'USDCNY': 'hf_USDCNY',
    'EURCNY': 'hf_EURCNY',
    'GBPUSD': 'hf_GBPCNY',
    'USDJPY': 'hf_USDJPY',
    'AUDUSD': 'hf_AUDUSD',
    'USDCAD': 'hf_USDCAD',
    'USDCHF': 'hf_USDCHF',
    'NZDUSD': 'hf_NZDUSD',
    'USDHKD': 'hf_USDHKD',
    'USDSGD': 'hf_USDSGD',
}


class ForexScraper(BaseScraper):
    """外汇数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'forex'

    @property
    def supported_types(self) -> List[str]:
        return ['forex_quote', 'forex_kline', 'forex_cny', 'forex_cross']

    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://hq.sinajs.cn/list=hf_USDCNY',
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
        if data_type == 'forex_quote':
            async for data in self._fetch_quotes(symbols, **kwargs):
                yield data
        elif data_type == 'forex_kline':
            for symbol in symbols:
                async for data in self._fetch_kline(symbol, start, end, **kwargs):
                    yield data
        elif data_type == 'forex_cny':
            async for data in self._fetch_cny_rates(**kwargs):
                yield data
        elif data_type == 'forex_cross':
            async for data in self._fetch_cross_rates(**kwargs):
                yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_quotes(self, symbols: List[str], **kwargs) -> AsyncIterator[FinanceData]:
        """获取外汇实时行情 (新浪)"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        async with httpx.AsyncClient(timeout=30) as client:
            # 批量请求新浪外汇接口
            codes = []
            for sym in symbols:
                code = SINA_FOREX_CODES.get(sym.upper(), sym.upper())
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
                    if len(fields) >= 10:
                        try:
                            quotes.append({
                                'code': code,
                                'name': fields[0],
                                'open': float(fields[1]) if fields[1] else 0,
                                'pre_close': float(fields[2]) if fields[2] else 0,
                                'price': float(fields[3]) if fields[3] else 0,
                                'high': float(fields[4]) if fields[4] else 0,
                                'low': float(fields[5]) if fields[5] else 0,
                                'volume': int(fields[6]) if fields[6] else 0,
                                'date': fields[7] if len(fields) > 7 else '',
                                'time': fields[8] if len(fields) > 8 else '',
                            })
                        except (ValueError, IndexError):
                            continue

                yield FinanceData(
                    source='forex',
                    data_type='forex_quote',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'quotes': quotes, 'count': len(quotes)}
                )
            except Exception as e:
                yield FinanceData(
                    source='forex',
                    data_type='forex_quote',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_kline(self, symbol: str, start: Optional[datetime], end: Optional[datetime], **kwargs) -> AsyncIterator[FinanceData]:
        """获取外汇历史K线 (新浪)"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        async with httpx.AsyncClient(timeout=30) as client:
            code = SINA_FOREX_CODES.get(symbol.upper(), symbol.upper())
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
                    source='forex',
                    data_type='forex_kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )
            except Exception as e:
                yield FinanceData(
                    source='forex',
                    data_type='forex_kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_cny_rates(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取人民币中间价 (AKShare)"""
        if not HAS_AKSHARE:
            raise ImportError("akshare 未安装，请运行：pip install akshare")

        try:
            df = ak.currency_boc_safe()
            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())

            yield FinanceData(
                source='forex',
                data_type='forex_cny',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'records': records, 'count': len(records)}
            )
        except Exception as e:
            yield FinanceData(
                source='forex',
                data_type='forex_cny',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'error': str(e)}
            )

    async def _fetch_cross_rates(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取外汇实时行情 (AKShare)"""
        if not HAS_AKSHARE:
            raise ImportError("akshare 未安装，请运行：pip install akshare")

        try:
            df = ak.currency_foreign_cnh_spot()
            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())

            yield FinanceData(
                source='forex',
                data_type='forex_cross',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'records': records, 'count': len(records)}
            )
        except Exception as e:
            yield FinanceData(
                source='forex',
                data_type='forex_cross',
                symbol='*',
                timestamp=datetime.utcnow(),
                payload={'error': str(e)}
            )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'forex') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'forex':
        return ForexScraper()
    raise ValueError(f"Unknown source: {source}")
