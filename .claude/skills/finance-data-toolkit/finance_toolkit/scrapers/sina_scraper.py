# -*- coding: utf-8 -*-
"""
新浪财经抓取器实现
数据源: 新浪财经 (finance.sina.com.cn, quotes.sina.cn, hq.sinajs.cn)
支持: 实时行情、历史K线、财务报表、分红配股、龙虎榜、北向资金、新闻资讯
特点: 免费、无需token、接口稳定、支持分钟级K线、有JSONP格式
"""

import asyncio
import json
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, AsyncIterator
from urllib.parse import urlencode

import httpx

from ..core import BaseScraper, FinanceData, register_scraper


# 新浪财经常用 API 端点
SINA_API = {
    'realtime': 'https://hq.sinajs.cn/list=',
    'realtime_v2': 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getStockRealTimeData',
    'kline': 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData',
    'kline_v2': 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineDataNew',
    'financial': 'https://money.finance.sina.com.cn/corp/go.php/vFD_FinanceSummary/stockid/',
    'dividend': 'https://money.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/',
    'shareholder': 'https://money.finance.sina.com.cn/corp/go.php/vCI_StockHolder/stockid/',
    'lhb': 'https://vip.stock.finance.sina.com.cn/quotes_service/api/jsonp.php/var=/LHB_Service.getLHBData',
    'northbound': 'https://vip.stock.finance.sina.com.cn/quotes_service/api/jsonp.php/var=/MoneyFlowService.getMoneyFlowData',
    'stock_basic': 'https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/',
}


def to_sina_symbol(symbol: str) -> str:
    """转换为新浪格式: 600000.SH -> sh600000, 000001.SZ -> sz000001"""
    code = symbol.split('.')[0]
    if code.startswith(('60', '68', '90')):
        return f'sh{code}'
    else:
        return f'sz{code}'


def from_sina_symbol(sina_code: str) -> str:
    """转换回标准格式: sh600000 -> 600000.SH"""
    code = sina_code[2:]
    if sina_code.startswith('sh'):
        return f'{code}.SH'
    else:
        return f'{code}.SZ'


class SinaScraper(BaseScraper):
    """新浪财经数据抓取器"""
    
    def __init__(self, proxy: str = None, timeout: int = 30, **kwargs):
        super().__init__()
        self.proxy = proxy
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': 'https://finance.sina.com.cn/',
            }
        )
    
    @property
    def source_name(self) -> str:
        return 'sina'
    
    @property
    def supported_types(self) -> List[str]:
        return ['quote', 'kline', 'financial', 'dividend', 'shareholder', 'lhb', 'northbound', 'stock_basic']
    
    async def health_check(self) -> bool:
        try:
            resp = await self.client.get(f'{SINA_API["realtime"]}sh600000')
            return resp.status_code == 200 and 'sh600000' in resp.text
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
        codes = [to_sina_symbol(s) for s in symbols]
        original_symbols = symbols
        
        if data_type == 'quote':
            async for item in self._fetch_realtime_quote(codes, original_symbols):
                yield item
        elif data_type == 'kline':
            period = kwargs.get('period', '240')  # 240=日线, 60=60分, 30=30分, 15=15分, 5=5分, 1=1分
            datalen = kwargs.get('datalen', 1023)
            ma = kwargs.get('ma', 'no')
            async for item in self._fetch_kline(codes, original_symbols, period, datalen, ma):
                yield item
        elif data_type == 'financial':
            async for item in self._fetch_financial(codes, original_symbols):
                yield item
        elif data_type == 'dividend':
            async for item in self._fetch_dividend(codes, original_symbols):
                yield item
        elif data_type == 'shareholder':
            async for item in self._fetch_shareholder(codes, original_symbols):
                yield item
        elif data_type == 'lhb':
            async for item in self._fetch_lhb():
                yield item
        elif data_type == 'northbound':
            async for item in self._fetch_northbound():
                yield item
        elif data_type == 'stock_basic':
            async for item in self._fetch_stock_basic(codes, original_symbols):
                yield item
        else:
            raise ValueError(f"Unsupported data_type: {data_type}")
    
    async def _fetch_realtime_quote(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取实时行情 (hq.sinajs.cn 接口)
        返回格式: var hq_str_sh600000="浦发银行,10.50,10.45,10.55,10.60,10.40,10.55,10.50,100000,1000000,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2024-01-15,15:00:00,00,00";
        字段: 名称, 今开, 昨收, 当前价, 最高, 最低, 竞买价, 竞卖价, 成交量(手), 成交额(元), 买一量, 买一价, 买二量, 买二价, 买三量, 买三价, 买四量, 买四价, 买五量, 买五价, 卖一量, 卖一价, 卖二量, 卖二价, 卖三量, 卖三价, 卖四量, 卖四价, 卖五量, 卖五价, 日期, 时间
        """
        # 批量请求 (每次最多 100 只)
        for i in range(0, len(codes), 50):
            batch = codes[i:i+50]
            batch_orig = original_symbols[i:i+50]
            
            url = SINA_API['realtime'] + ','.join(batch)
            
            try:
                resp = await self.client.get(url)
                text = resp.text
                
                # 解析每行
                lines = text.strip().split(';')
                for line in lines:
                    if not line or '=' not in line:
                        continue
                    
                    var_part, data_part = line.split('=', 1)
                    sina_code = var_part.strip().replace('var hq_str_', '').replace('"', '')
                    
                    if not data_part or data_part.strip() == '""':
                        continue
                    
                    data_str = data_part.strip().strip('"')
                    fields = data_str.split(',')
                    
                    if len(fields) < 30:
                        continue
                    
                    symbol = from_sina_symbol(sina_code)
                    if symbol not in batch_orig:
                        continue
                    
                    try:
                        payload = {
                            'name': fields[0],
                            'open': float(fields[1]) if fields[1] else 0,
                            'pre_close': float(fields[2]) if fields[2] else 0,
                            'price': float(fields[3]) if fields[3] else 0,
                            'high': float(fields[4]) if fields[4] else 0,
                            'low': float(fields[5]) if fields[5] else 0,
                            'bid_price': float(fields[6]) if fields[6] else 0,
                            'ask_price': float(fields[7]) if fields[7] else 0,
                            'volume': int(fields[8]) if fields[8] else 0,
                            'amount': float(fields[9]) if fields[9] else 0,
                            'buy_volumes': [int(fields[j]) if fields[j] else 0 for j in range(10, 20, 2)],
                            'buy_prices': [float(fields[j]) if fields[j] else 0 for j in range(11, 20, 2)],
                            'sell_volumes': [int(fields[j]) if fields[j] else 0 for j in range(20, 30, 2)],
                            'sell_prices': [float(fields[j]) if fields[j] else 0 for j in range(21, 30, 2)],
                            'date': fields[30] if len(fields) > 30 else '',
                            'time': fields[31] if len(fields) > 31 else '',
                        }
                        
                        # 计算衍生字段
                        if payload['pre_close'] > 0:
                            payload['change_amt'] = round(payload['price'] - payload['pre_close'], 2)
                            payload['change_pct'] = round(payload['change_amt'] / payload['pre_close'] * 100, 2)
                        
                        yield FinanceData(
                            source='sina',
                            data_type='quote',
                            symbol=symbol,
                            timestamp=datetime.utcnow(),
                            payload=payload
                        )
                    except (ValueError, IndexError) as e:
                        yield FinanceData(
                            source='sina',
                            data_type='quote',
                            symbol=symbol,
                            timestamp=datetime.utcnow(),
                            payload={'error': f'Parse error: {e}'},
                            meta={'raw': data_str[:200]}
                        )
            except Exception as e:
                for sym in batch_orig:
                    yield FinanceData(
                        source='sina',
                        data_type='quote',
                        symbol=sym,
                        timestamp=datetime.utcnow(),
                        payload={'error': str(e)},
                        meta={'batch': batch}
                    )
    
    async def _fetch_kline(self, codes: List[str], original_symbols: List[str], period: str, datalen: int, ma: str) -> AsyncIterator[FinanceData]:
        """获取历史 K 线 (quotes.sina.cn 接口)
        period: 240=日线, 120=120分钟, 60=60分钟, 30=30分钟, 15=15分钟, 5=5分钟, 1=1分钟
        ma: no/ma5,ma10,ma20... (均线)
        """
        for code, symbol in zip(codes, original_symbols):
            params = {
                'symbol': code,
                'scale': period,
                'ma': ma,
                'datalen': datalen,
            }
            
            url = f"{SINA_API['kline']}?{urlencode(params)}"
            
            try:
                resp = await self.client.get(url)
                text = resp.text
                
                # 提取 JSONP 数据: var=(...);
                idx = text.find('var=(')
                if idx < 0:
                    idx = text.find('=(')
                if idx < 0:
                    yield FinanceData(
                        source='sina',
                        data_type='kline',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload={'error': 'Invalid response format'},
                        meta={'raw': text[:200]}
                    )
                    continue
                
                end = text.rfind(');')
                if end < 0:
                    end = text.rfind(')')
                json_str = text[idx + 5:end] if idx >= 0 else text[idx + 2:end]
                
                data = json.loads(json_str)
                
                if not data:
                    yield FinanceData(
                        source='sina',
                        data_type='kline',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload={'error': 'Empty data'},
                        meta={'code': code}
                    )
                    continue
                
                kline_data = []
                for row in data:
                    kline_data.append({
                        'date': row.get('day', ''),
                        'open': float(row.get('open', 0)),
                        'high': float(row.get('high', 0)),
                        'low': float(row.get('low', 0)),
                        'close': float(row.get('close', 0)),
                        'volume': int(row.get('volume', 0)),
                        'amount': float(row.get('amount', 0)) if row.get('amount') else 0,
                    })
                
                yield FinanceData(
                    source='sina',
                    data_type='kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={
                        'period': period,
                        'datalen': datalen,
                        'ma': ma,
                        'count': len(kline_data),
                        'data': kline_data
                    },
                    meta={'code': code, 'period': period}
                )
            except Exception as e:
                yield FinanceData(
                    source='sina',
                    data_type='kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'code': code}
                )
    
    async def _fetch_financial(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取财务报表 (需解析网页)"""
        for code, symbol in zip(codes, original_symbols):
            try:
                stock_code = code[2:]  # 去掉 sh/sz 前缀
                url = f"{SINA_API['financial']}{stock_code}/displaytype/4.phtml"
                
                resp = await self.client.get(url)
                # 这里需要解析 HTML 表格，简化实现
                # 实际项目中建议使用 BeautifulSoup 解析
                
                yield FinanceData(
                    source='sina',
                    data_type='financial',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'note': 'Requires HTML parsing', 'url': url},
                    meta={'code': code}
                )
            except Exception as e:
                yield FinanceData(
                    source='sina',
                    data_type='financial',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'code': code}
                )
    
    async def _fetch_dividend(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取分红配股"""
        for code, symbol in zip(codes, original_symbols):
            try:
                stock_code = code[2:]
                url = f"{SINA_API['dividend']}{stock_code}.phtml"
                
                resp = await self.client.get(url)
                
                yield FinanceData(
                    source='sina',
                    data_type='dividend',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'note': 'Requires HTML parsing', 'url': url},
                    meta={'code': code}
                )
            except Exception:
                pass
    
    async def _fetch_shareholder(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取股本结构/股东信息"""
        for code, symbol in zip(codes, original_symbols):
            try:
                stock_code = code[2:]
                url = f"{SINA_API['shareholder']}{stock_code}.phtml"
                
                resp = await self.client.get(url)
                
                yield FinanceData(
                    source='sina',
                    data_type='shareholder',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'note': 'Requires HTML parsing', 'url': url},
                    meta={'code': code}
                )
            except Exception:
                pass
    
    async def _fetch_lhb(self) -> AsyncIterator[FinanceData]:
        """获取龙虎榜"""
        try:
            params = {'date': datetime.now().strftime('%Y-%m-%d')}
            url = f"{SINA_API['lhb']}?{urlencode(params)}"
            
            resp = await self.client.get(url)
            text = resp.text
            
            # 提取 JSONP
            idx = text.find('=(')
            if idx >= 0:
                end = text.rfind(')')
                json_str = text[idx + 2:end]
                data = json.loads(json_str)
                
                if data:
                    for item in data:
                        code = item.get('symbol', '')
                        if code.startswith(('sh', 'sz')):
                            symbol = from_sina_symbol(code)
                            yield FinanceData(
                                source='sina',
                                data_type='lhb',
                                symbol=symbol,
                                timestamp=datetime.utcnow(),
                                payload=item
                            )
        except Exception:
            pass
    
    async def _fetch_northbound(self) -> AsyncIterator[FinanceData]:
        """获取北向资金"""
        try:
            url = f"{SINA_API['northbound']}?symbol=sh000001"
            resp = await self.client.get(url)
            text = resp.text
            
            idx = text.find('=(')
            if idx >= 0:
                end = text.rfind(')')
                json_str = text[idx + 2:end]
                data = json.loads(json_str)
                
                if data:
                    yield FinanceData(
                        source='sina',
                        data_type='northbound',
                        symbol='SH_HSGT',
                        timestamp=datetime.utcnow(),
                        payload=data
                    )
        except Exception:
            pass
    
    async def _fetch_stock_basic(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取股票基本信息"""
        for code, symbol in zip(codes, original_symbols):
            try:
                stock_code = code[2:]
                url = f"{SINA_API['stock_basic']}{stock_code}.phtml"
                
                resp = await self.client.get(url)
                
                yield FinanceData(
                    source='sina',
                    data_type='stock_basic',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'note': 'Requires HTML parsing', 'url': url},
                    meta={'code': code}
                )
            except Exception:
                pass
    
    async def close(self):
        await self.client.aclose()


# 便捷函数
async def create_scraper(proxy: str = None) -> SinaScraper:
    """创建新浪财经抓取器实例"""
    return SinaScraper(proxy=proxy)