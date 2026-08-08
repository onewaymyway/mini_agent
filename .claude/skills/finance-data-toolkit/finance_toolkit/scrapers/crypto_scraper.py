# -*- coding: utf-8 -*-
"""
加密货币数据抓取器实现
数据源: AKShare、CoinGecko (免费、无需 token)
支持: 实时行情、历史K线、市值排行、资金流向等
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


# 主流加密货币代码映射
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
    'LTC': 'litecoin',
    'AVAX': 'avalanche-2',
    'LINK': 'chainlink',
}


@register_scraper
@register_scraper
@register_scraper
class CryptoScraper(BaseScraper):
    """加密货币数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'crypto'

    @property
    def supported_types(self) -> List[str]:
        return ['crypto_quote', 'crypto_kline', 'crypto_rank', 'crypto_trending']

    async def health_check(self) -> bool:
        if not HAS_HTTPX:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    'https://api.coingecko.com/api/v3/ping',
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
        if data_type == 'crypto_quote':
            async for data in self._fetch_quotes(symbols, **kwargs):
                yield data
        elif data_type == 'crypto_kline':
            for symbol in symbols:
                async for data in self._fetch_kline(symbol, start, end, **kwargs):
                    yield data
        elif data_type == 'crypto_rank':
            async for data in self._fetch_rank(**kwargs):
                yield data
        elif data_type == 'crypto_trending':
            async for data in self._fetch_trending(**kwargs):
                yield data
        else:
            raise ValueError(f"不支持的数据类型：{data_type}")

    async def _fetch_quotes(self, symbols: List[str], **kwargs) -> AsyncIterator[FinanceData]:
        """获取加密货币实时行情"""
        # 优先使用 AKShare
        if HAS_AKSHARE:
            try:
                df = ak.crypto_js_spot()
                records = []
                for _, row in df.iterrows():
                    records.append(row.to_dict())

                yield FinanceData(
                    source='crypto',
                    data_type='crypto_quote',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )
                return
            except Exception as e:
                print(f"AKShare 加密货币获取失败: {e}", file=__import__('sys').stderr)

        # 降级到 CoinGecko API
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        async with httpx.AsyncClient(timeout=30) as client:
            # 获取主流加密货币行情
            ids = [CRYPTO_CODES.get(s.upper(), s.lower()) for s in symbols if s.upper() in CRYPTO_CODES]
            if not ids:
                ids = list(CRYPTO_CODES.values())[:20]  # 默认获取前20

            try:
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

                if resp.status_code != 200:
                    return

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
                        'high_24h': item.get('high_24h', 0),
                        'low_24h': item.get('low_24h', 0),
                    })

                yield FinanceData(
                    source='crypto',
                    data_type='crypto_quote',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )
            except Exception as e:
                yield FinanceData(
                    source='crypto',
                    data_type='crypto_quote',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_kline(self, symbol: str, start: Optional[datetime], end: Optional[datetime], **kwargs) -> AsyncIterator[FinanceData]:
        """获取加密货币历史K线"""
        if not HAS_AKSHARE:
            raise ImportError("akshare 未安装，请运行：pip install akshare")

        try:
            # 使用 AKShare 获取加密货币历史数据
            code = CRYPTO_CODES.get(symbol.upper(), symbol.lower())
            df = ak.crypto_binance_kline(
                symbol=code,
                period='daily',
                start_date=start.strftime('%Y%m%d') if start else '20240101',
                end_date=end.strftime('%Y%m%d') if end else datetime.now().strftime('%Y%m%d')
            )

            records = []
            for _, row in df.iterrows():
                records.append(row.to_dict())

            yield FinanceData(
                source='crypto',
                data_type='crypto_kline',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'records': records, 'count': len(records)}
            )
        except Exception as e:
            yield FinanceData(
                source='crypto',
                data_type='crypto_kline',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={'error': str(e)}
            )

    async def _fetch_rank(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取加密货币市值排行"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        async with httpx.AsyncClient(timeout=30) as client:
            try:
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

                if resp.status_code != 200:
                    return

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

                yield FinanceData(
                    source='crypto',
                    data_type='crypto_rank',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'records': records, 'count': len(records)}
                )
            except Exception as e:
                yield FinanceData(
                    source='crypto',
                    data_type='crypto_rank',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def _fetch_trending(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取热门加密货币"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装，请运行：pip install httpx")

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    'https://api.coingecko.com/api/v3/search/trending',
                    timeout=30
                )

                if resp.status_code != 200:
                    return

                data = resp.json()
                items = data.get('coins', [])
                trending = []
                for item in items[:10]:
                    coin = item.get('item', {})
                    trending.append({
                        'rank': len(trending) + 1,
                        'id': coin.get('id', ''),
                        'symbol': coin.get('symbol', '').upper(),
                        'name': coin.get('name', ''),
                        'market_cap_rank': coin.get('market_cap_rank', 0),
                        'data': coin.get('data', {})
                    })

                yield FinanceData(
                    source='crypto',
                    data_type='crypto_trending',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'trending': trending, 'count': len(trending)}
                )
            except Exception as e:
                yield FinanceData(
                    source='crypto',
                    data_type='crypto_trending',
                    symbol='*',
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)}
                )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'crypto') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'crypto':
        return CryptoScraper()
    raise ValueError(f"Unknown source: {source}")
