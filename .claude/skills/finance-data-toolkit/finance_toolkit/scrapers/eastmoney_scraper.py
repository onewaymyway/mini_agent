# -*- coding: utf-8 -*-
"""
东方财富网抓取器实现
数据源: 东方财富网 (quote.eastmoney.com, push2.eastmoney.com)
支持: 实时行情、历史K线、财务报表、分红配股、龙虎榜、北向资金、板块资金流、个股研报
特点: 免费、字段全、有反爬、需配合 browser-cdp 或 requests+签名
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


# 东方财富常用 API 端点
EM_API = {
    'realtime': 'https://push2.eastmoney.com/api/qt/stock/get',
    'kline': 'https://push2his.eastmoney.com/api/qt/stock/kline/get',
    'capital_flow': 'https://push2.eastmoney.com/api/qt/stock/fflow/kline/get',
    'financial': 'https://emweb.securities.eastmoney.com/PC_HSF10/FinancialAnalysis/PageAjax',
    'dividend': 'https://emweb.securities.eastmoney.com/PC_HSF10/ProfitForecast/PageAjax',
    'shareholder': 'https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax',
    'lhb': 'https://push2.eastmoney.com/api/qt/clist/get',
    'northbound': 'https://push2.eastmoney.com/api/qt/kamt/get',
    'block_flow': 'https://push2.eastmoney.com/api/qt/clist/get',
    'research': 'https://reportapi.eastmoney.com/report/list',
}


def to_em_symbol(symbol: str) -> str:
    """转换为东方财富格式: 600000.SH -> 1.600000, 000001.SZ -> 0.000001"""
    code = symbol.split('.')[0]
    if code.startswith(('60', '68', '90')):
        return f'1.{code}'
    else:
        return f'0.{code}'


def from_em_symbol(em_code: str) -> str:
    """转换回标准格式: 1.600000 -> 600000.SH"""
    prefix, code = em_code.split('.')
    if prefix == '1':
        return f'{code}.SH'
    else:
        return f'{code}.SZ'


class EastmoneySigner:
    """东方财富签名生成器 (简化版，实际需逆向 JS)"""
    
    @staticmethod
    def generate_sign(params: dict) -> str:
        """生成签名 - 实际项目中需从网页 JS 逆向获取算法"""
        # 简化实现：东方财富部分接口不需要签名，或使用固定参数
        # 完整实现需分析网页中的 sign 生成逻辑
        return ''


@register_scraper
class EastmoneyScraper(BaseScraper):
    """东方财富数据抓取器"""
    
    def __init__(self, proxy: str = None, timeout: int = 30, use_cdp: bool = False, cdp_port: int = 9222, **kwargs):
        super().__init__()
        self.proxy = proxy
        self.timeout = timeout
        self.use_cdp = use_cdp
        self.cdp_port = cdp_port
        self.client = httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy,
            trust_env=False,  # 禁用系统代理，避免 127.0.0.1:10808 干扰
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': 'https://quote.eastmoney.com/',
                'Origin': 'https://quote.eastmoney.com',
            }
        )
    
    @property
    def source_name(self) -> str:
        return 'eastmoney'
    
    @property
    def supported_types(self) -> List[str]:
        return ['quote', 'kline', 'financial', 'dividend', 'shareholder', 'lhb', 'northbound', 'capital_flow', 'block_flow', 'research']
    
    async def health_check(self) -> bool:
        try:
            resp = await self.client.get(EM_API['realtime'], params={'secid': '1.600000', 'fields': 'f43'})
            return resp.status_code == 200 and resp.json().get('rc') == 0
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
        codes = [to_em_symbol(s) for s in symbols]
        original_symbols = symbols
        
        if data_type == 'quote':
            async for item in self._fetch_realtime_quote(codes, original_symbols):
                yield item
        elif data_type == 'kline':
            period = kwargs.get('period', '101')  # 101=日线, 102=周线, 103=月线, 1=1分钟, 5=5分钟等
            adj = kwargs.get('adj', '1')  # 1=前复权, 2=后复权, 0=不复权
            start_str = start.strftime('%Y%m%d') if start else '20240101'
            end_str = end.strftime('%Y%m%d') if end else datetime.now().strftime('%Y%m%d')
            async for item in self._fetch_kline(codes, original_symbols, period, start_str, end_str, adj):
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
        elif data_type == 'capital_flow':
            async for item in self._fetch_capital_flow(codes, original_symbols):
                yield item
        elif data_type == 'block_flow':
            async for item in self._fetch_block_flow():
                yield item
        elif data_type == 'research':
            async for item in self._fetch_research(codes, original_symbols):
                yield item
        else:
            raise ValueError(f"Unsupported data_type: {data_type}")
    
    async def _fetch_realtime_quote(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取实时行情
        优先使用 push2 实时接口，失败时尝试新浪，最后降级到 K 线数据
        """
        # 1. 尝试东方财富 push2 实时接口
        try:
            async for item in self._fetch_realtime_push2(codes, original_symbols):
                yield item
            return  # 成功获取，退出
        except Exception:
            pass
        
        # 2. 尝试新浪财经接口 (备用)
        try:
            async for item in self._fetch_realtime_from_sina(codes, original_symbols):
                yield item
            return  # 成功获取，退出
        except Exception:
            pass
        
        # 3. 降级：从 K 线 API 获取最新一根数据
        async for item in self._fetch_realtime_from_kline(codes, original_symbols):
            yield item
    
    async def _fetch_realtime_push2(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """通过 push2 实时行情接口 (可能被反爬拦截)"""
        fields = 'f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f53,f57,f58,f59,f60,f12,f13,f14'
        
        for i in range(0, len(codes), 50):
            batch = codes[i:i+50]
            batch_orig = original_symbols[i:i+50]
            
            params = {
                'secid': ','.join(batch),
                'fields': fields,
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                'fltt': '2',
                'invt': '2',
                '_': str(int(time.time() * 1000)),
            }
            
            resp = await self.client.get(EM_API['realtime'], params=params, timeout=10)
            data = resp.json()
            
            if data.get('rc') != 0 or not data.get('data', {}).get('diff'):
                continue
            
            for item in data['data']['diff']:
                em_code = f"{item.get('f12')}.{item.get('f13')}"
                symbol = from_em_symbol(em_code)
                
                if symbol not in batch_orig:
                    continue
                
                payload = {
                    'name': item.get('f14', ''),
                    'price': item.get('f43', 0) / 100 if item.get('f43') else 0,
                    'open': item.get('f46', 0) / 100 if item.get('f46') else 0,
                    'high': item.get('f44', 0) / 100 if item.get('f44') else 0,
                    'low': item.get('f45', 0) / 100 if item.get('f45') else 0,
                    'pre_close': item.get('f47', 0) / 100 if item.get('f47') else 0,
                    'volume': item.get('f48', 0),
                    'amount': item.get('f49', 0),
                    'change_pct': item.get('f51', 0) / 100 if item.get('f51') else 0,
                    'change_amt': item.get('f52', 0) / 100 if item.get('f52') else 0,
                    'turnover': item.get('f53', 0) / 100 if item.get('f53') else 0,
                    'pe_ttm': item.get('f57', 0) / 100 if item.get('f57') else 0,
                    'pb': item.get('f58', 0) / 100 if item.get('f58') else 0,
                    'total_mv': item.get('f59', 0),
                    'circ_mv': item.get('f60', 0),
                }
                
                yield FinanceData(
                    source='eastmoney',
                    data_type='quote',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload=payload
                )
    
    async def _fetch_realtime_from_kline(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """降级方案：从日K线获取最新一根数据作为实时行情"""
        fields = 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
        
        for code, symbol in zip(codes, original_symbols):
            params = {
                'secid': code,
                'fields1': 'f1,f2,f3,f4,f5,f6',
                'fields2': fields,
                'klt': '101',
                'fqt': '1',
                'beg': '0',
                'end': '20500101',
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                '_': str(int(time.time() * 1000)),
            }
            
            try:
                resp = await self.client.get(EM_API['kline'], params=params)
                data = resp.json()
                
                if data.get('rc') != 0 or not data.get('data', {}).get('klines'):
                    continue
                
                klines = data['data']['klines']
                if not klines:
                    continue
                
                last_line = klines[-1]
                parts = last_line.split(',')
                if len(parts) >= 11:
                    payload = {
                        'name': data.get('data', {}).get('name', ''),
                        'date': parts[0],
                        'open': float(parts[1]) if parts[1] else 0,
                        'close': float(parts[2]) if parts[2] else 0,
                        'high': float(parts[3]) if parts[3] else 0,
                        'low': float(parts[4]) if parts[4] else 0,
                        'volume': int(parts[5]) if parts[5] else 0,
                        'amount': float(parts[6]) if parts[6] else 0,
                        'amplitude': float(parts[7]) if parts[7] else 0,
                        'change_pct': float(parts[8]) if parts[8] else 0,
                        'change_amt': float(parts[9]) if parts[9] else 0,
                        'turnover': float(parts[10]) if parts[10] else 0,
                        'price': float(parts[2]) if parts[2] else 0,
                    }
                    
                    yield FinanceData(
                        source='eastmoney',
                        data_type='quote',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload=payload,
                        meta={'fallback': 'kline_last_bar'}
                    )
            except Exception as e:
                yield FinanceData(
                    source='eastmoney',
                    data_type='quote',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'code': code}
                )
    
    async def _fetch_realtime_from_sina(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """通过新浪财经接口获取实时行情 (备用方案)
        新浪需要 Referer 头否则返回 403
        """
        sina_codes = []
        code_to_symbol = {}
        for code, symbol in zip(codes, original_symbols):
            if symbol.endswith('.SH'):
                sina_code = f'sh{symbol.split(".")[0]}'
            elif symbol.endswith('.SZ'):
                sina_code = f'sz{symbol.split(".")[0]}'
            else:
                continue
            sina_codes.append(sina_code)
            code_to_symbol[sina_code] = symbol
        
        if not sina_codes:
            return
        
        url = f'https://hq.sinajs.cn/list={",".join(sina_codes)}'
        
        try:
            # 新浪需要 Referer 头
            resp = await self.client.get(url, timeout=10, headers={
                'Referer': 'https://finance.sina.com.cn/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            })
            resp.encoding = 'gbk'  # 新浪使用 GBK 编码
            
            for line in resp.text.split('\n'):
                if not line.strip():
                    continue
                match = re.match(r'var hq_str_(\w+)="(.*)"', line)
                if not match:
                    continue
                
                sina_code = match.group(1)
                data_str = match.group(2)
                parts = data_str.split(',')
                
                if len(parts) < 32:
                    continue
                
                symbol = code_to_symbol.get(sina_code)
                if not symbol:
                    continue
                
                payload = {
                    'name': parts[0],
                    'price': float(parts[3]) if parts[3] else 0,
                    'open': float(parts[2]) if parts[2] else 0,
                    'high': float(parts[4]) if parts[4] else 0,
                    'low': float(parts[5]) if parts[5] else 0,
                    'pre_close': float(parts[1]) if parts[1] else 0,
                    'volume': int(parts[8]) if parts[8] else 0,  # 新浪成交量在手位置
                    'amount': float(parts[9]) if parts[9] else 0,
                    'date': parts[30],
                    'time': parts[31],
                    'change_pct': ((float(parts[3]) - float(parts[1])) / float(parts[1]) * 100) if parts[1] and parts[3] else 0,
                }
                
                yield FinanceData(
                    source='eastmoney',
                    data_type='quote',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload=payload,
                    meta={'fallback': 'sina'}
                )
        except Exception:
            pass
    
    async def _fetch_kline(self, codes: List[str], original_symbols: List[str], period: str, start: str, end: str, adj: str) -> AsyncIterator[FinanceData]:
        """获取历史 K 线
        period: 1=1分, 5=5分, 15=15分, 30=30分, 60=60分, 101=日线, 102=周线, 103=月线
        adj: 0=不复权, 1=前复权, 2=后复权
        优先使用东方财富接口，失败时降级到 AKShare
        """
        fields = 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
        
        for code, symbol in zip(codes, original_symbols):
            params = {
                'secid': code,
                'fields1': 'f1,f2,f3,f4,f5,f6',
                'fields2': fields,
                'klt': period,
                'fqt': adj,
                'beg': start,
                'end': end,
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                '_': str(int(time.time() * 1000)),
            }
            
            try:
                resp = await self.client.get(EM_API['kline'], params=params, timeout=15)
                data = resp.json()
                
                if data.get('rc') != 0 or not data.get('data', {}).get('klines'):
                    raise ValueError("No klines data")
                
                klines = data['data']['klines']
                kline_data = []
                
                for line in klines:
                    parts = line.split(',')
                    if len(parts) >= 11:
                        kline_data.append({
                            'date': parts[0],
                            'open': float(parts[1]) if parts[1] else 0,
                            'close': float(parts[2]) if parts[2] else 0,
                            'high': float(parts[3]) if parts[3] else 0,
                            'low': float(parts[4]) if parts[4] else 0,
                            'volume': int(parts[5]) if parts[5] else 0,
                            'amount': float(parts[6]) if parts[6] else 0,
                            'amplitude': float(parts[7]) if parts[7] else 0,
                            'change_pct': float(parts[8]) if parts[8] else 0,
                            'change_amt': float(parts[9]) if parts[9] else 0,
                            'turnover': float(parts[10]) if parts[10] else 0,
                        })
                
                yield FinanceData(
                    source='eastmoney',
                    data_type='kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow(),
                    payload={
                        'period': period,
                        'adjust': adj,
                        'count': len(kline_data),
                        'data': kline_data
                    },
                    meta={'code': code, 'start': start, 'end': end}
                )
            except Exception:
                # 东方财富失败，降级到 AKShare
                async for item in self._fetch_kline_from_akshare(symbol, period, start, end, adj):
                    yield item
    
    async def _fetch_kline_from_akshare(self, symbol: str, period: str, start: str, end: str, adj: str) -> AsyncIterator[FinanceData]:
        """通过新浪历史K线接口获取数据 (备用方案)
        新浪K线API: money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
        """
        try:
            # 转换代码格式：600000.SH -> sh600000
            if symbol.endswith('.SH'):
                sina_code = f'sh{symbol.split(".")[0]}'
            elif symbol.endswith('.SZ'):
                sina_code = f'sz{symbol.split(".")[0]}'
            else:
                sina_code = symbol
            
            # 转换周期：101=日线 -> 240, 102=周线 -> 1680, 103=月线 -> 7200
            scale_map = {'101': '240', '102': '1680', '103': '7200',
                         '1': '1', '5': '5', '15': '15', '30': '30', '60': '60'}
            scale = scale_map.get(period, '240')
            
            # 计算数据长度（最多 1023 根）
            datalen = '1023'
            
            url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
            params = {
                'symbol': sina_code,
                'scale': scale,
                'ma': 'no',
                'datalen': datalen,
            }
            
            resp = await self.client.get(url, params=params, timeout=15, headers={
                'Referer': 'https://finance.sina.com.cn/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            })
            
            import json as _json
            raw_data = _json.loads(resp.text)
            
            if not raw_data:
                return
            
            # 按日期过滤
            start_date = f"{start[:4]}-{start[4:6]}-{start[6:8]}" if len(start) == 8 else start
            end_date = f"{end[:4]}-{end[4:6]}-{end[6:8]}" if len(end) == 8 else end
            
            kline_data = []
            for item in raw_data:
                day = item.get('day', '')
                if start_date and day < start_date:
                    continue
                if end_date and day > end_date:
                    continue
                kline_data.append({
                    'date': day,
                    'open': float(item.get('open', 0)),
                    'close': float(item.get('close', 0)),
                    'high': float(item.get('high', 0)),
                    'low': float(item.get('low', 0)),
                    'volume': int(item.get('volume', 0)),
                    'amount': 0,  # 新浪K线不提供成交额
                    'amplitude': 0,
                    'change_pct': 0,
                    'change_amt': 0,
                    'turnover': 0,
                })
            
            yield FinanceData(
                source='eastmoney',
                data_type='kline',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={
                    'period': period,
                    'adjust': adj,
                    'count': len(kline_data),
                    'data': kline_data
                },
                meta={'fallback': 'sina_kline', 'start': start, 'end': end}
            )
        except Exception as e:
            yield FinanceData(
                source='eastmoney',
                data_type='kline',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'error': str(e)},
                meta={'fallback': 'sina_kline_failed'}
            )
    
    async def _fetch_financial(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取财务报表 (需解析网页或使用 HSF10 接口)"""
        for code, symbol in zip(codes, original_symbols):
            try:
                # 使用 HSF10 财务分析接口
                params = {
                    'code': code.split('.')[1],
                    'type': '0',  # 0=主要指标, 1=利润表, 2=资产负债表, 3=现金流量表
                    'reportDateType': '0',
                    'endDate': '',
                    '_': str(int(time.time() * 1000)),
                }
                
                for report_type, type_val in [('indicator', '0'), ('income', '1'), ('balancesheet', '2'), ('cashflow', '3')]:
                    params['type'] = type_val
                    resp = await self.client.get(EM_API['financial'], params=params)
                    data = resp.json()
                    
                    if data.get('Data'):
                        yield FinanceData(
                            source='eastmoney',
                            data_type='financial',
                            symbol=symbol,
                            timestamp=datetime.utcnow(),
                            payload=data['Data'],
                            meta={'report_type': report_type}
                        )
            except Exception as e:
                yield FinanceData(
                    source='eastmoney',
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
                params = {
                    'code': code.split('.')[1],
                    '_': str(int(time.time() * 1000)),
                }
                resp = await self.client.get(EM_API['dividend'], params=params)
                data = resp.json()
                
                if data.get('Data'):
                    yield FinanceData(
                        source='eastmoney',
                        data_type='dividend',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload=data['Data']
                    )
            except Exception:
                pass
    
    async def _fetch_shareholder(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取股本结构/股东信息"""
        for code, symbol in zip(codes, original_symbols):
            try:
                params = {
                    'code': code.split('.')[1],
                    '_': str(int(time.time() * 1000)),
                }
                resp = await self.client.get(EM_API['shareholder'], params=params)
                data = resp.json()
                
                if data.get('Data'):
                    yield FinanceData(
                        source='eastmoney',
                        data_type='shareholder',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload=data['Data']
                    )
            except Exception:
                pass
    
    async def _fetch_lhb(self) -> AsyncIterator[FinanceData]:
        """获取龙虎榜"""
        try:
            params = {
                'pn': '1',
                'pz': '50',
                'po': '1',
                'np': '1',
                'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                'fltt': '2',
                'invt': '2',
                'fid': 'f3',
                'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048',
                '_': str(int(time.time() * 1000)),
            }
            resp = await self.client.get(EM_API['lhb'], params=params)
            data = resp.json()
            
            if data.get('rc') == 0 and data.get('data', {}).get('diff'):
                for item in data['data']['diff']:
                    em_code = f"{item.get('f12')}.{item.get('f13')}"
                    symbol = from_em_symbol(em_code)
                    
                    yield FinanceData(
                        source='eastmoney',
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
            params = {
                'fields1': 'f1,f2,f3,f4',
                'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64',
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                'klt': '101',
                'fqt': '0',
                'secid': '1.000001',  # 沪股通
                '_': str(int(time.time() * 1000)),
            }
            resp = await self.client.get(EM_API['northbound'], params=params)
            data = resp.json()
            
            if data.get('rc') == 0 and data.get('data', {}).get('klines'):
                yield FinanceData(
                    source='eastmoney',
                    data_type='northbound',
                    symbol='SH_HSGT',
                    timestamp=datetime.utcnow(),
                    payload={'klines': data['data']['klines']}
                )
        except Exception:
            pass
    
    async def _fetch_capital_flow(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取个股资金流向"""
        for code, symbol in zip(codes, original_symbols):
            try:
                params = {
                    'secid': code,
                    'fields1': 'f1,f2,f3,f7',
                    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
                    'klt': '101',
                    'fqt': '0',
                    'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                    '_': str(int(time.time() * 1000)),
                }
                resp = await self.client.get(EM_API['capital_flow'], params=params)
                data = resp.json()
                
                if data.get('rc') == 0 and data.get('data', {}).get('klines'):
                    yield FinanceData(
                        source='eastmoney',
                        data_type='capital_flow',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload={'klines': data['data']['klines']}
                    )
            except Exception:
                pass
    
    async def _fetch_block_flow(self) -> AsyncIterator[FinanceData]:
        """获取板块资金流向"""
        try:
            params = {
                'pn': '1',
                'pz': '50',
                'po': '1',
                'np': '1',
                'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                'fltt': '2',
                'invt': '2',
                'fid': 'f62',
                'fs': 'm:90+t:2+f:!50',  # 概念板块
                '_': str(int(time.time() * 1000)),
            }
            resp = await self.client.get(EM_API['block_flow'], params=params)
            data = resp.json()
            
            if data.get('rc') == 0 and data.get('data', {}).get('diff'):
                yield FinanceData(
                    source='eastmoney',
                    data_type='block_flow',
                    symbol='CONCEPT',
                    timestamp=datetime.utcnow(),
                    payload={'blocks': data['data']['diff']}
                )
        except Exception:
            pass
    
    async def _fetch_research(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取个股研报"""
        for code, symbol in zip(codes, original_symbols):
            try:
                params = {
                    'industryCode': '*',
                    'rating': '*',
                    'ratingChange': '*',
                    'beginTime': '2024-01-01',
                    'endTime': datetime.now().strftime('%Y-%m-%d'),
                    'pageNo': '1',
                    'pageSize': '20',
                    'code': code.split('.')[1],
                    '_': str(int(time.time() * 1000)),
                }
                resp = await self.client.get(EM_API['research'], params=params)
                data = resp.json()
                
                if data.get('data'):
                    yield FinanceData(
                        source='eastmoney',
                        data_type='research',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload=data['data']
                    )
            except Exception:
                pass
    
    async def close(self):
        await self.client.aclose()


# 便捷函数
async def create_scraper(proxy: str = None, use_cdp: bool = False, cdp_port: int = 9222) -> EastmoneyScraper:
    """创建东方财富抓取器实例"""
    return EastmoneyScraper(proxy=proxy, use_cdp=use_cdp, cdp_port=cdp_port)