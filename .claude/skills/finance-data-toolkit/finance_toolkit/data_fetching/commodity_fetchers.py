# -*- coding: utf-8 -*-
"""
大宗商品数据同步获取模块
数据源: AKShare（黄金/原油/期货）、FRED（宏观商品指数）
支持: 黄金现货、原油期货、美元指数、LME金属等
"""

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


# ============== 黄金/贵金属 ==============

def fetch_gold_quote(source: str = 'akshare') -> List[FinanceData]:
    """获取黄金现货行情

    Args:
        source: 数据源 (akshare)

    Returns:
        List[FinanceData]: 黄金行情数据
    """
    if source == 'akshare' and HAS_AKSHARE:
        try:
            # 上海黄金交易所
            df = ak.meta_meta_data()
            gold_records = []
            for _, row in df.iterrows():
                name = str(row.get('name', ''))
                if '黄金' in name or 'GOLD' in name.upper():
                    try:
                        gold_records.append({
                            'symbol': name,
                            'price': float(row.get('price', row.get('最新价', 0)) or 0),
                            'change_pct': float(row.get('change_pct', row.get('涨跌幅', 0)) or 0),
                            'high': float(row.get('high', row.get('最高价', 0)) or 0),
                            'low': float(row.get('low', row.get('最低价', 0)) or 0),
                            'volume': float(row.get('volume', row.get('成交量', 0)) or 0),
                            'source': 'shfe',
                        })
                    except (ValueError, TypeError):
                        continue

            if gold_records:
                return [_build_commodity_data('gold_quote', gold_records)]
        except Exception as e:
            logger.warning(f"AKShare 黄金数据获取失败: {e}")

    # 回退：通过 AKShare 的金价接口
    if HAS_AKSHARE:
        try:
            df = ak.gold_spot()
            records = []
            for _, row in df.iterrows():
                try:
                    records.append({
                        'symbol': row.get('品种', ''),
                        'price': float(row.get('卖出价', row.get('最新价', 0)) or 0),
                        'buy_price': float(row.get('买入价', 0) or 0),
                        'high': float(row.get('最高价', 0) or 0),
                        'low': float(row.get('最低价', 0) or 0),
                        'time': row.get('时间', ''),
                        'source': 'gold_spot',
                    })
                except (ValueError, TypeError):
                    continue
            if records:
                return [_build_commodity_data('gold_quote', records)]
        except Exception as e:
            logger.warning(f"黄金现货接口失败: {e}")

    return []


def fetch_gold_kline(symbol: str = 'AU99.99', start: str = '20240101',
                     end: str = None, source: str = 'akshare') -> List[FinanceData]:
    """获取黄金历史K线

    Args:
        symbol: 合约代码
        start: 开始日期 YYYYMMDD
        end: 结束日期 YYYYMMDD
        source: 数据源
    """
    if end is None:
        end = datetime.now().strftime('%Y%m%d')

    if HAS_AKSHARE:
        try:
            df = ak.futures_gene_kline(
                symbol=symbol,
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
                        'source': 'akshare',
                    })
                return [_build_commodity_data('gold_kline', records, symbol=symbol)]
        except Exception as e:
            logger.warning(f"黄金K线获取失败: {e}")

    return []


# ============== 原油/能源 ==============

def fetch_crude_oil_quote(source: str = 'akshare') -> List[FinanceData]:
    """获取原油期货行情

    Args:
        source: 数据源

    Returns:
        List[FinanceData]: 原油行情
    """
    if HAS_AKSHARE:
        try:
            df = ak.futures_main_sina(symbol='SC')
            if df is not None and not df.empty:
                records = []
                for _, row in df.iterrows():
                    records.append({
                        'symbol': 'SC',
                        'name': '原油（INE）',
                        'price': float(row.get('price', row.get('最新价', 0)) or 0),
                        'change_pct': float(row.get('change_pct', row.get('涨跌幅', 0)) or 0),
                        'high': float(row.get('high', row.get('最高价', 0)) or 0),
                        'low': float(row.get('low', row.get('最低价', 0)) or 0),
                        'volume': float(row.get('volume', row.get('成交量', 0)) or 0),
                        'open_interest': float(row.get('open_interest', row.get('持仓量', 0)) or 0),
                        'source': 'ine',
                    })
                return [_build_commodity_data('crude_oil_quote', records)]
        except Exception as e:
            logger.warning(f"原油期货获取失败: {e}")

    # 回退：纽约商品交易所（NYMEX）WTI 和布伦特
    if HAS_HTTPX:
        try:
            async def _fetch():
                async with httpx.AsyncClient(timeout=20) as client:
                    # WTI 原油
                    resp = await client.get(
                        'https://query1.finance.yahoo.com/v8/finance/chart/CL=F',
                        params={'interval': '1d', 'range': '1d'},
                        timeout=20,
                    )
                    data = resp.json()
                    meta = data.get('chart', {}).get('result', [{}])[0].get('meta', {})
                    wti = {
                        'symbol': 'WTI',
                        'name': 'WTI 原油期货',
                        'price': float(meta.get('regularMarketPrice', 0)),
                        'change_pct': float(meta.get('chartPreviousClose', 0)),
                        'high': float(meta.get('regularMarketDayHigh', 0)),
                        'low': float(meta.get('regularMarketDayLow', 0)),
                        'source': 'yahoo',
                    }

                    # 布伦特原油
                    resp2 = await client.get(
                        'https://query1.finance.yahoo.com/v8/finance/chart/COCL=F',
                        params={'interval': '1d', 'range': '1d'},
                        timeout=20,
                    )
                    data2 = resp2.json()
                    meta2 = data2.get('chart', {}).get('result', [{}])[0].get('meta', {})
                    brent = {
                        'symbol': 'BRENT',
                        'name': '布伦特原油期货',
                        'price': float(meta2.get('regularMarketPrice', 0)),
                        'change_pct': float(meta2.get('chartPreviousClose', 0)),
                        'high': float(meta2.get('regularMarketDayHigh', 0)),
                        'low': float(meta2.get('regularMarketDayLow', 0)),
                        'source': 'yahoo',
                    }
                    return [_build_commodity_data('crude_oil_quote', [wti, brent])]
            import asyncio
            return [asyncio.run(_fetch())]
        except Exception as e:
            logger.warning(f"Yahoo 原油获取失败: {e}")

    return []


def fetch_nymex_wti_quote() -> List[FinanceData]:
    """获取 WTI 原油实时行情（Yahoo Finance）"""
    if not HAS_HTTPX:
        return []

    try:
        async def _fetch():
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    'https://query1.finance.yahoo.com/v8/finance/chart/CL=F',
                    params={'interval': '1d', 'range': '1d'},
                    timeout=20,
                )
                data = resp.json()
                meta = data.get('chart', {}).get('result', [{}])[0].get('meta', {})
                record = {
                    'symbol': 'WTI',
                    'name': 'WTI 原油期货',
                    'price': float(meta.get('regularMarketPrice', 0)),
                    'change': float(meta.get('regularMarketChange', 0)),
                    'change_pct': float(meta.get('regularMarketChangePercent', 0)),
                    'high': float(meta.get('regularMarketDayHigh', 0)),
                    'low': float(meta.get('regularMarketDayLow', 0)),
                    'open': float(meta.get('regularMarketOpen', 0)),
                    'prev_close': float(meta.get('chartPreviousClose', 0)),
                    'source': 'yahoo',
                }
                return [_build_commodity_data('nymex_wti', [record])]
        import asyncio
        return [asyncio.run(_fetch())]
    except Exception as e:
        logger.warning(f"NYMEX WTI 获取失败: {e}")
        return []


# ============== 美元指数 ==============

def fetch_dxy_quote() -> List[FinanceData]:
    """获取美元指数行情"""
    if not HAS_HTTPX:
        return []

    try:
        async def _fetch():
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    'https://query1.finance.yahoo.com/v8/finance/chart/DXY=-',
                    params={'interval': '1d', 'range': '1d'},
                    timeout=20,
                )
                data = resp.json()
                meta = data.get('chart', {}).get('result', [{}])[0].get('meta', {})
                record = {
                    'symbol': 'DXY',
                    'name': '美元指数',
                    'price': float(meta.get('regularMarketPrice', 0)),
                    'change': float(meta.get('regularMarketChange', 0)),
                    'change_pct': float(meta.get('regularMarketChangePercent', 0)),
                    'high': float(meta.get('regularMarketDayHigh', 0)),
                    'low': float(meta.get('regularMarketDayLow', 0)),
                    'source': 'yahoo',
                }
                return [_build_commodity_data('dxy_quote', [record])]
        import asyncio
        return [asyncio.run(_fetch())]
    except Exception as e:
        logger.warning(f"美元指数获取失败: {e}")
        return []


# ============== LME 金属 ==============

def fetch_lme_metal_quote(metal: str = ' copper') -> List[FinanceData]:
    """获取 LME 金属行情

    Args:
        metal: 金属类型，如 'copper', 'aluminum', 'zinc', 'lead', 'nickel', 'tin'
    """
    if not HAS_AKSHARE:
        return []

    try:
        df = ak.futures_lme_daily()
        if df is not None and not df.empty:
            metal_upper = metal.strip().upper()
            filtered = df[df['品种'].str.contains(metal_upper, case=False, na=False)]
            records = []
            for _, row in filtered.head(5).iterrows():
                try:
                    records.append({
                        'symbol': row.get('品种', ''),
                        'price': float(row.get('价格', 0) or 0),
                        'change_pct': float(row.get('涨跌幅', 0) or 0),
                        'date': row.get('日期', ''),
                        'source': 'lme',
                    })
                except (ValueError, TypeError):
                    continue
            return [_build_commodity_data(f'lme_{metal}', records)]
    except Exception as e:
        logger.warning(f"LME {metal} 获取失败: {e}")

    return []


# ============== 通用辅助函数 ==============

def _build_commodity_data(data_type: str, records: List[Dict], symbol: str = '*') -> FinanceData:
    """构建FinanceData对象"""
    return FinanceData(
        source=records[0].get('source', 'commodity') if records else 'commodity',
        data_type=data_type,
        symbol=symbol,
        timestamp=datetime.utcnow().isoformat(),
        payload={'records': records, 'count': len(records)},
    )


# ============== 统一入口类 ==============

class CommodityFetcher:
    """大宗商品数据获取器"""

    def get_gold_quote(self, source: str = 'akshare') -> List[FinanceData]:
        return fetch_gold_quote(source)

    def get_gold_kline(self, symbol: str = 'AU99.99', start: str = '20240101',
                       end: str = None) -> List[FinanceData]:
        return fetch_gold_kline(symbol, start, end)

    def get_crude_oil_quote(self, source: str = 'akshare') -> List[FinanceData]:
        return fetch_crude_oil_quote(source)

    def get_wti_quote(self) -> List[FinanceData]:
        return fetch_nymex_wti_quote()

    def get_dxy_quote(self) -> List[FinanceData]:
        return fetch_dxy_quote()

    def get_lme_quote(self, metal: str = 'copper') -> List[FinanceData]:
        return fetch_lme_metal_quote(metal)

    def get_all(self) -> Dict[str, List[FinanceData]]:
        return {
            'gold': self.get_gold_quote(),
            'crude_oil': self.get_crude_oil_quote(),
            'wti': self.get_wti_quote(),
            'dxy': self.get_dxy_quote(),
            'lme_copper': self.get_lme_quote('copper'),
        }


commodity_fetcher = CommodityFetcher()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger.info("测试大宗商品数据获取...")

    logger.info("\n1. 黄金行情...")
    golds = fetch_gold_quote()
    for g in golds[:3]:
        logger.info(f"  {g.symbol}: {g.payload}")

    logger.info("\n2. 原油期货...")
    oils = fetch_crude_oil_quote()
    for o in oils[:3]:
        logger.info(f"  {o.symbol}: {o.payload}")

    logger.info("\n3. WTI 原油...")
    wti = fetch_nymex_wti_quote()
    for w in wti:
        logger.info(f"  {w.payload}")

    logger.info("\n4. 美元指数...")
    dxy = fetch_dxy_quote()
    for d in dxy:
        logger.info(f"  {d.payload}")
