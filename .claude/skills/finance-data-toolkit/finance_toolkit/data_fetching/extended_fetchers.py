# -*- coding: utf-8 -*-
"""
扩展数据获取模块 - 外汇、加密货币、ETF
"""

from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

from ..core import FinanceData


# ============== 外汇数据 ==============

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


def fetch_forex_quote(symbols: List[str] = None, source: str = 'sina') -> List[FinanceData]:
    """获取外汇实时行情
    
    Args:
        symbols: 货币对列表，如 ['USDCNY', 'EURUSD']，默认获取全部
        source: 数据源 (sina/akshare)
    """
    results = []
    
    if source == 'sina' and HAS_HTTPX:
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=30) as client:
                    if symbols:
                        codes = [SINA_FOREX_CODES.get(s.upper(), s.upper()) for s in symbols]
                    else:
                        codes = list(SINA_FOREX_CODES.values())
                    
                    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
                    headers = {
                        'Referer': 'https://finance.sina.com.cn/',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                    resp = await client.get(url, headers=headers, timeout=30)
                    resp.encoding = 'gbk'
                    
                    quotes = []
                    for line in resp.text.strip().split(';'):
                        if '=' not in line:
                            continue
                        var_part, data_part = line.split('=', 1)
                        code = var_part.strip().replace('var hq_str_', '').replace('"', '')
                        data_str = data_part.strip().strip('"')
                        fields = data_str.split(',')
                        if len(fields) >= 10:
                            try:
                                quotes.append({
                                    'code': code,
                                    'name': fields[0],
                                    'price': float(fields[3]) if fields[3] else 0,
                                    'change_pct': float(fields[9]) if len(fields) > 9 and fields[9] else 0,
                                    'high': float(fields[4]) if fields[4] else 0,
                                    'low': float(fields[5]) if fields[5] else 0,
                                    'date': fields[7] if len(fields) > 7 else '',
                                    'time': fields[8] if len(fields) > 8 else '',
                                })
                            except (ValueError, IndexError):
                                continue
                    
                    return FinanceData(
                        source='forex',
                        data_type='forex_quote',
                        symbol='*',
                        timestamp=datetime.utcnow().isoformat(),
                        payload={'quotes': quotes, 'count': len(quotes)}
                    )
            
            import asyncio
            result = asyncio.run(_fetch())
            results.append(result)
        except Exception as e:
            print(f"外汇行情获取失败: {e}", file=__import__('sys').stderr)
    
    elif source == 'akshare' and HAS_AKSHARE:
        try:
            df = ak.currency_foreign_cnh_spot()
            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())
            
            results.append(FinanceData(
                source='akshare',
                data_type='forex_quote',
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'records': records, 'count': len(records)}
            ))
        except Exception as e:
            print(f"AKShare外汇获取失败: {e}", file=__import__('sys').stderr)
    
    return results


def fetch_cny_rates() -> List[FinanceData]:
    """获取人民币中间价"""
    if not HAS_AKSHARE:
        return []
    
    try:
        df = ak.currency_boc_safe()
        records = []
        for _, row in df.iterrows():
            records.append(row.to_dict())
        
        return [FinanceData(
            source='akshare',
            data_type='forex_cny',
            symbol='*',
            timestamp=datetime.utcnow().isoformat(),
            payload={'records': records, 'count': len(records)}
        )]
    except Exception as e:
        print(f"人民币中间价获取失败: {e}", file=__import__('sys').stderr)
        return []


# ============== 加密货币数据 ==============

CRYPTO_CODES = {
    'BTC': 'bitcoin',
    'ETH': 'ethereum',
    'USDT': 'tether',
    'BNB': 'binancecoin',
    'SOL': 'solana',
    'XRP': 'ripple',
    'ADA': 'cardano',
    'DOGE': 'dogecoin',
    'DOT': 'polkadot',
    'MATIC': 'matic-network',
}


def fetch_crypto_quote(symbols: List[str] = None, source: str = 'akshare') -> List[FinanceData]:
    """获取加密货币实时行情
    
    Args:
        symbols: 加密货币代码列表，如 ['BTC', 'ETH']，默认获取全部
        source: 数据源 (akshare/coingecko)
    """
    results = []
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            df = ak.crypto_js_spot()
            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())
            
            results.append(FinanceData(
                source='akshare',
                data_type='crypto_quote',
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'records': records, 'count': len(records)}
            ))
        except Exception as e:
            print(f"AKShare加密货币获取失败: {e}", file=__import__('sys').stderr)
    
    elif source == 'coingecko' and HAS_HTTPX:
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=30) as client:
                    if symbols:
                        ids = [CRYPTO_CODES.get(s.upper(), s.lower()) for s in symbols if s.upper() in CRYPTO_CODES]
                        if not ids:
                            ids = list(CRYPTO_CODES.values())[:10]
                    else:
                        ids = list(CRYPTO_CODES.values())
                    
                    resp = await client.get(
                        'https://api.coingecko.com/api/v3/coins/markets',
                        params={
                            'vs_currency': 'usd',
                            'ids': ','.join(ids),
                            'order': 'market_cap_desc',
                            'sparkline': 'false',
                            'price_change_percentage': '24h'
                        },
                        timeout=30
                    )
                    
                    data = resp.json()
                    records = []
                    for item in data:
                        records.append({
                            'id': item.get('id', ''),
                            'symbol': item.get('symbol', '').upper(),
                            'name': item.get('name', ''),
                            'price': item.get('current_price', 0),
                            'market_cap': item.get('market_cap', 0),
                            'volume_24h': item.get('total_volume', 0),
                            'price_change_24h': item.get('price_change_percentage_24h', 0),
                        })
                    
                    return FinanceData(
                        source='coingecko',
                        data_type='crypto_quote',
                        symbol='*',
                        timestamp=datetime.utcnow().isoformat(),
                        payload={'records': records, 'count': len(records)}
                    )
            
            import asyncio
            result = asyncio.run(_fetch())
            results.append(result)
        except Exception as e:
            print(f"CoinGecko获取失败: {e}", file=__import__('sys').stderr)
    
    return results


def fetch_crypto_rank() -> List[FinanceData]:
    """获取加密货币市值排行"""
    if not HAS_HTTPX:
        return []
    
    try:
        async def _fetch():
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    'https://api.coingecko.com/api/v3/coins/markets',
                    params={
                        'vs_currency': 'usd',
                        'order': 'market_cap_desc',
                        'per_page': 100,
                        'page': 1,
                        'sparkline': 'false',
                        'price_change_percentage': '24h'
                    },
                    timeout=30
                )
                
                data = resp.json()
                records = []
                for item in data:
                    records.append({
                        'rank': item.get('market_cap_rank', 0),
                        'id': item.get('id', ''),
                        'symbol': item.get('symbol', '').upper(),
                        'name': item.get('name', ''),
                        'price': item.get('current_price', 0),
                        'market_cap': item.get('market_cap', 0),
                        'volume_24h': item.get('total_volume', 0),
                        'price_change_24h': item.get('price_change_percentage_24h', 0),
                    })
                
                return FinanceData(
                    source='coingecko',
                    data_type='crypto_rank',
                    symbol='*',
                    timestamp=datetime.utcnow().isoformat(),
                    payload={'records': records, 'count': len(records)}
                )
        
        import asyncio
        return [asyncio.run(_fetch())]
    except Exception as e:
        print(f"加密货币排行获取失败: {e}", file=__import__('sys').stderr)
        return []


# ============== ETF数据 ==============


def fetch_etf_quote(symbols: List[str] = None, source: str = 'eastmoney') -> List[FinanceData]:
    """获取ETF实时行情
    
    Args:
        symbols: ETF代码列表，如 ['510300', '159915']，默认获取全部
        source: 数据源 (eastmoney/akshare)
    """
    results = []
    
    if source == 'eastmoney' and HAS_HTTPX:
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=30) as client:
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
                        
                        if symbols:
                            quotes = [q for q in quotes if q['code'] in symbols or q['name'] in symbols]
                        
                        return FinanceData(
                            source='eastmoney',
                            data_type='etf_quote',
                            symbol='*',
                            timestamp=datetime.utcnow().isoformat(),
                            payload={'quotes': quotes, 'count': len(quotes)}
                        )
                    return None
            
            import asyncio
            result = asyncio.run(_fetch())
            if result:
                results.append(result)
        except Exception as e:
            print(f"ETF行情获取失败: {e}", file=__import__('sys').stderr)
    
    elif source == 'akshare' and HAS_AKSHARE:
        try:
            df = ak.fund_etf_spot_em()
            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())
            
            results.append(FinanceData(
                source='akshare',
                data_type='etf_quote',
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'records': records, 'count': len(records)}
            ))
        except Exception as e:
            print(f"AKShare ETF获取失败: {e}", file=__import__('sys').stderr)
    
    return results


def fetch_etf_kline(symbol: str, start: str = '20240101', end: str = None) -> List[FinanceData]:
    """获取ETF历史K线"""
    if not HAS_AKSHARE:
        return []
    
    try:
        code = symbol.split('.')[0]
        if end is None:
            end = datetime.now().strftime('%Y%m%d')
        
        df = ak.fund_etf_hist_sina(symbol=code, period='daily', start_date=start, end_date=end, adjust='qfq')
        
        records = []
        for _, row in df.iterrows():
            records.append(row.to_dict())
        
        return [FinanceData(
            source='akshare',
            data_type='etf_kline',
            symbol=symbol,
            timestamp=datetime.utcnow().isoformat(),
            payload={'records': records, 'count': len(records), 'start': start, 'end': end}
        )]
    except Exception as e:
        print(f"ETF K线获取失败: {e}", file=__import__('sys').stderr)
        return []


# ============== 统一入口 ==============

class ExtendedDataFetcher:
    """扩展数据获取器 - 外汇、加密货币、ETF"""
    
    def get_forex_quote(self, symbols: List[str] = None, source: str = 'sina') -> List[FinanceData]:
        return fetch_forex_quote(symbols, source)
    
    def get_cny_rates(self) -> List[FinanceData]:
        return fetch_cny_rates()
    
    def get_crypto_quote(self, symbols: List[str] = None, source: str = 'akshare') -> List[FinanceData]:
        return fetch_crypto_quote(symbols, source)
    
    def get_crypto_rank(self) -> List[FinanceData]:
        return fetch_crypto_rank()
    
    def get_etf_quote(self, symbols: List[str] = None, source: str = 'eastmoney') -> List[FinanceData]:
        return fetch_etf_quote(symbols, source)
    
    def get_etf_kline(self, symbol: str, start: str = '20240101', end: str = None) -> List[FinanceData]:
        return fetch_etf_kline(symbol, start, end)


# 便捷实例
extended_fetcher = ExtendedDataFetcher()
