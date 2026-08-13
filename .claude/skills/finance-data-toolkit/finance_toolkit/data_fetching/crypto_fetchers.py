# -*- coding: utf-8 -*-
"""
加密货币数据同步获取模块
数据源: AKShare、CoinGecko（免费、无需 token）、Binance API
支持: 实时行情、历史K线、市值排行、资金流向等
"""

import asyncio
import logging
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

logger = logging.getLogger(__name__)

# 主流加密货币代码映射（AKShare 使用英文ID）
CRYPTO_CODE_MAP = {
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
    'LTC': 'litecoin',
    'AVAX': 'avalanche-2',
    'LINK': 'chainlink',
    'UNI': 'uniswap',
    'ATOM': 'cosmos',
    'NEAR': 'near',
    'APT': 'aptos',
    'SUI': 'sui',
    'PEPE': 'pepe',
    'SHIB': 'shiba-inu',
}


def fetch_crypto_quote(symbols: List[str] = None, source: str = 'akshare') -> List[FinanceData]:
    """获取加密货币实时行情

    Args:
        symbols: 加密货币代码列表，如 ['BTC', 'ETH']，默认获取全部热门币
        source: 数据源 (akshare/coingecko/binance)

    Returns:
        List[FinanceData]: 行情数据
    """
    results = []

    if source == 'akshare' and HAS_AKSHARE:
        try:
            df = ak.crypto_js_spot()
            if df is not None and not df.empty:
                records = _normalize_crypto_records(df, source='akshare')
                results.append(_build_crypto_data('crypto_quote', records))
                return results
        except Exception as e:
            logger.warning(f"AKShare 加密货币行情获取失败: {e}")

    if source == 'coingecko' and HAS_HTTPX:
        try:
            results.extend(_fetch_coingecko_quotes(symbols))
            return results
        except Exception as e:
            logger.warning(f"CoinGecko 行情获取失败: {e}")

    if source == 'binance' and HAS_HTTPX:
        try:
            results.extend(_fetch_binance_quotes())
            return results
        except Exception as e:
            logger.warning(f"Binance 行情获取失败: {e}")

    # 自动回退
    if HAS_AKSHARE:
        try:
            df = ak.crypto_js_spot()
            if df is not None and not df.empty:
                records = _normalize_crypto_records(df, source='akshare')
                results.append(_build_crypto_data('crypto_quote', records))
        except Exception as e:
            logger.warning(f"自动回退 AKShare 失败: {e}")
    elif HAS_HTTPX:
        try:
            results.extend(_fetch_coingecko_quotes(symbols))
        except Exception as e:
            logger.warning(f"自动回退 CoinGecko 失败: {e}")

    return results


def fetch_crypto_kline(symbol: str, start: str = '20240101', end: str = None,
                       source: str = 'akshare') -> List[FinanceData]:
    """获取加密货币历史K线

    Args:
        symbol: 加密货币代码（如 BTC, ETH）
        start: 开始日期，格式 YYYYMMDD
        end: 结束日期，格式 YYYYMMDD，默认今天
        source: 数据源 (akshare/binance)

    Returns:
        List[FinanceData]: K线数据
    """
    if end is None:
        end = datetime.now().strftime('%Y%m%d')

    if source == 'akshare' and HAS_AKSHARE:
        try:
            code = CRYPTO_CODE_MAP.get(symbol.upper(), symbol.lower())
            df = ak.crypto_binance_kline(
                symbol=code,
                period='daily',
                start_date=start,
                end_date=end,
            )
            if df is not None and not df.empty:
                records = []
                for _, row in df.iterrows():
                    records.append({
                        'date': str(row.get('日期', row.index.name)),
                        'open': float(row.get('开盘价', 0) or 0),
                        'high': float(row.get('最高价', 0) or 0),
                        'low': float(row.get('最低价', 0) or 0),
                        'close': float(row.get('收盘价', 0) or 0),
                        'volume': float(row.get('成交量', 0) or 0),
                    })
                return [_build_crypto_data('crypto_kline', records, symbol=symbol)]
        except Exception as e:
            logger.warning(f"AKShare 加密货币K线获取失败: {e}")

    if source == 'binance' and HAS_HTTPX:
        try:
            return _fetch_binance_kline(symbol, start, end)
        except Exception as e:
            logger.warning(f"Binance K线获取失败: {e}")

    return []


def fetch_crypto_rank(page: int = 1, page_size: int = 100,
                      source: str = 'coingecko') -> List[FinanceData]:
    """获取加密货币市值排行

    Args:
        page: 页码
        page_size: 每页数量
        source: 数据源 (coingecko/binance)

    Returns:
        List[FinanceData]: 排行数据
    """
    if source == 'coingecko' and HAS_HTTPX:
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        'https://api.coingecko.com/api/v3/coins/markets',
                        params={
                            'vs_currency': 'usd',
                            'order': 'market_cap_desc',
                            'per_page': page_size,
                            'page': page,
                            'sparkline': 'false',
                            'price_change_percentage': '24h',
                        },
                        timeout=30,
                    )
                    data = resp.json()
                    records = []
                    for item in data:
                        records.append({
                            'rank': item.get('market_cap_rank', 0),
                            'symbol': item.get('symbol', '').upper(),
                            'name': item.get('name', ''),
                            'price': item.get('current_price', 0),
                            'market_cap': item.get('market_cap', 0),
                            'volume_24h': item.get('total_volume', 0),
                            'price_change_24h': item.get('price_change_percentage_24h', 0),
                            'circulating_supply': item.get('circulating_supply', 0),
                            'ath': item.get('ath', 0),
                        })
                    return _build_crypto_data('crypto_rank', records)
            return [asyncio.run(_fetch())]
        except Exception as e:
            logger.warning(f"CoinGecko 排行获取失败: {e}")

    if HAS_AKSHARE:
        try:
            df = ak.crypto_js_spot()
            if df is not None and not df.empty:
                df_sorted = df.sort_values('市值（美元）', ascending=False).head(page_size)
                records = _normalize_crypto_records(df_sorted, source='akshare')
                for i, r in enumerate(records, 1):
                    r['rank'] = i
                return [_build_crypto_data('crypto_rank', records)]
        except Exception as e:
            logger.warning(f"AKShare 排行获取失败: {e}")

    return []


def fetch_crypto_trending(source: str = 'coingecko') -> List[FinanceData]:
    """获取热门加密货币

    Args:
        source: 数据源 (coingecko)

    Returns:
        List[FinanceData]: 热门数据
    """
    if source == 'coingecko' and HAS_HTTPX:
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        'https://api.coingecko.com/api/v3/search/trending',
                        timeout=30,
                    )
                    data = resp.json()
                    items = data.get('coins', [])[:10]
                    trending = []
                    for idx, item in enumerate(items, 1):
                        coin = item.get('item', {})
                        trending.append({
                            'rank': idx,
                            'symbol': coin.get('symbol', '').upper(),
                            'name': coin.get('name', ''),
                            'market_cap_rank': coin.get('market_cap_rank', 0),
                            'data': coin.get('data', {}),
                        })
                    return FinanceData(
                        source='coingecko',
                        data_type='crypto_trending',
                        symbol='*',
                        timestamp=datetime.utcnow().isoformat(),
                        payload={'trending': trending, 'count': len(trending)},
                    )
            return [asyncio.run(_fetch())]
        except Exception as e:
            logger.warning(f"CoinGecko 热门获取失败: {e}")

    return []


def _normalize_crypto_records(df, source: str) -> List[Dict[str, Any]]:
    """标准化AKShare返回的加密货币记录"""
    records = []
    for _, row in df.iterrows():
        try:
            price_str = str(row.get('最新价', row.get('价格', ''))).strip()
            volume_str = str(row.get('24H成交量', row.get('成交量', ''))).strip()
            market_str = str(row.get('市值', '')).strip()
            chg_str = str(row.get('24H涨跌幅', '')).strip().replace('%', '')

            records.append({
                'symbol': row.get('名称', row.get('币种', '')),
                'price': float(price_str) if price_str and price_str != '-' else 0,
                'volume_24h': float(volume_str) if volume_str and volume_str != '-' else 0,
                'market_cap': float(market_str) if market_str and market_str != '-' else 0,
                'price_change_24h': float(chg_str) if chg_str and chg_str != '-' else 0,
                'source': source,
            })
        except (ValueError, TypeError):
            continue
    return records


def _build_crypto_data(data_type: str, records: List[Dict], symbol: str = '*') -> FinanceData:
    """构建FinanceData对象"""
    return FinanceData(
        source=records[0].get('source', 'crypto') if records else 'crypto',
        data_type=data_type,
        symbol=symbol,
        timestamp=datetime.utcnow().isoformat(),
        payload={'records': records, 'count': len(records)},
    )


def _fetch_coingecko_quotes(symbols: List[str] = None) -> List[FinanceData]:
    """从CoinGecko获取行情"""
    async def _run():
        async with httpx.AsyncClient(timeout=30) as client:
            ids = []
            if symbols:
                ids = [CRYPTO_CODE_MAP.get(s.upper(), s.lower()) for s in symbols
                       if s.upper() in CRYPTO_CODE_MAP]
            if not ids:
                ids = list(CRYPTO_CODE_MAP.values())[:30]

            resp = await client.get(
                'https://api.coingecko.com/api/v3/coins/markets',
                params={
                    'vs_currency': 'usd',
                    'ids': ','.join(ids),
                    'order': 'market_cap_desc',
                    'sparkline': 'false',
                    'price_change_percentage': '24h',
                },
                timeout=30,
            )
            data = resp.json()
            records = []
            for item in data:
                records.append({
                    'symbol': item.get('symbol', '').upper(),
                    'name': item.get('name', ''),
                    'price': item.get('current_price', 0),
                    'market_cap': item.get('market_cap', 0),
                    'volume_24h': item.get('total_volume', 0),
                    'price_change_24h': item.get('price_change_percentage_24h', 0),
                    'high_24h': item.get('high_24h', 0),
                    'low_24h': item.get('low_24h', 0),
                    'source': 'coingecko',
                })
            return _build_crypto_data('crypto_quote', records)

    return [asyncio.run(_run())]


def _fetch_binance_quotes() -> List[FinanceData]:
    """从Binance获取行情"""
    async def _run():
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get('https://api.binance.com/api/v3/ticker/24hr', timeout=30)
            data = resp.json()
            records = []
            for item in data:
                sym = item.get('symbol', '')
                if not sym.endswith('USDT') and not sym.endswith('BTC'):
                    continue
                records.append({
                    'symbol': sym.replace('USDT', '').replace('BTC', 'BTC'),
                    'price': float(item.get('lastPrice', 0)),
                    'volume_24h': float(item.get('quoteVolume', 0)),
                    'price_change_24h': float(item.get('priceChangePercent', 0)),
                    'high_24h': float(item.get('highPrice', 0)),
                    'low_24h': float(item.get('lowPrice', 0)),
                    'source': 'binance',
                })
            return _build_crypto_data('crypto_quote', records)

    return [asyncio.run(_run())]


def _fetch_binance_kline(symbol: str, start: str, end: str) -> List[FinanceData]:
    """从Binance获取K线"""
    async def _run():
        async with httpx.AsyncClient(timeout=30) as client:
            sym_upper = symbol.upper()
            resp = await client.get(
                'https://api.binance.com/api/v3/klines',
                params={
                    'symbol': f'{sym_upper}USDT',
                    'interval': '1d',
                    'startTime': datetime.strptime(start, '%Y%m%d').timestamp() * 1000,
                    'endTime': datetime.strptime(end, '%Y%m%d').timestamp() * 1000,
                    'limit': 500,
                },
                timeout=30,
            )
            data = resp.json()
            records = []
            for kline in data:
                records.append({
                    'date': datetime.fromtimestamp(kline[0] / 1000).strftime('%Y-%m-%d'),
                    'open': float(kline[1]),
                    'high': float(kline[2]),
                    'low': float(kline[3]),
                    'close': float(kline[4]),
                    'volume': float(kline[5]),
                    'source': 'binance',
                })
            return _build_crypto_data('crypto_kline', records, symbol=symbol)

    return [asyncio.run(_run())]


class CryptoFetcher:
    """加密货币数据获取器"""

    def get_quote(self, symbols: List[str] = None, source: str = 'akshare') -> List[FinanceData]:
        return fetch_crypto_quote(symbols, source)

    def get_kline(self, symbol: str, start: str = '20240101', end: str = None,
                  source: str = 'akshare') -> List[FinanceData]:
        return fetch_crypto_kline(symbol, start, end, source)

    def get_rank(self, page: int = 1, page_size: int = 100,
                 source: str = 'coingecko') -> List[FinanceData]:
        return fetch_crypto_rank(page, page_size, source)

    def get_trending(self, source: str = 'coingecko') -> List[FinanceData]:
        return fetch_crypto_trending(source)

    def get_all(self, symbols: List[str] = None) -> Dict[str, List[FinanceData]]:
        return {
            'quote': self.get_quote(symbols),
            'rank': self.get_rank(),
            'trending': self.get_trending(),
        }


crypto_fetcher = CryptoFetcher()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger.info("测试加密货币数据获取...")

    logger.info("\n1. 实时行情 (AKShare)...")
    quotes = fetch_crypto_quote(source='akshare')
    for q in quotes[:3]:
        logger.info(f"  {q.symbol}: {q.payload}")

    logger.info("\n2. 市值排行 (CoinGecko)...")
    ranks = fetch_crypto_rank()
    for r in ranks[:3]:
        recs = r.payload.get('records', [])
        if recs:
            logger.info(f"  #{recs[0].get('rank')}: {recs[0].get('symbol')} ${recs[0].get('price')}")
