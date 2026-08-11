# -*- coding: utf-8 -*-
"""
股票数据抓取器
支持：实时行情、K线、财务数据、分红、龙虎榜、北向资金、股票基础信息
数据源：AKShare（免费、无需认证）
"""

import logging
from datetime import datetime
from typing import List, Optional, AsyncIterator

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

from ..core import BaseScraper, FinanceData, register_scraper

logger = logging.getLogger(__name__)


@register_scraper
class StockScraper(BaseScraper):
    """股票数据抓取器"""

    @property
    def source_name(self) -> str:
        return 'stock'

    @property
    def supported_types(self) -> List[str]:
        return [
            'quote',           # 实时行情
            'kline',           # K线数据
            'financial',       # 财务报表
            'dividend',        # 分红数据
            'lhb',             # 龙虎榜
            'northbound',      # 北向资金
            'stock_basic',     # 股票基础信息
        ]

    async def health_check(self) -> bool:
        """健康检查"""
        if not HAS_AKSHARE:
            return False
        try:
            # 测试获取少量股票数据
            df = ak.stock_zh_a_spot_em()
            return not df.empty
        except Exception as e:
            logger.error(f"健康检查失败: {e}")
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
        if not HAS_AKSHARE:
            yield FinanceData(
                source='stock',
                data_type=data_type,
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'error': 'akshare 未安装'}
            )
            return

        if data_type == 'quote':
            async for data in self._fetch_quote(symbols, **kwargs):
                yield data
        elif data_type == 'kline':
            async for data in self._fetch_kline(symbols, start, end, **kwargs):
                yield data
        elif data_type == 'financial':
            async for data in self._fetch_financial(symbols, **kwargs):
                yield data
        elif data_type == 'dividend':
            async for data in self._fetch_dividend(symbols, **kwargs):
                yield data
        elif data_type == 'lhb':
            async for data in self._fetch_lhb(symbols, start, end, **kwargs):
                yield data
        elif data_type == 'northbound':
            async for data in self._fetch_northbound(**kwargs):
                yield data
        elif data_type == 'stock_basic':
            async for data in self._fetch_stock_basic(**kwargs):
                yield data
        else:
            yield FinanceData(
                source='stock',
                data_type=data_type,
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'error': f'不支持的数据类型: {data_type}'}
            )

    async def _fetch_quote(self, symbols: List[str], **kwargs) -> AsyncIterator[FinanceData]:
        """获取实时行情"""
        try:
            df = ak.stock_zh_a_spot_em()
            
            results = []
            for _, row in df.iterrows():
                code = str(row.get('代码', ''))
                if not symbols or code in symbols or f'{code}.SH' in symbols or f'{code}.SZ' in symbols:
                    results.append({
                        'symbol': f'{code}.SH' if code.startswith(('60', '68', '90')) else f'{code}.SZ',
                        'name': row.get('名称', ''),
                        'price': float(row.get('最新价', 0) or 0),
                        'change_pct': float(row.get('涨跌幅', 0) or 0),
                        'change_amt': float(row.get('涨跌额', 0) or 0),
                        'open': float(row.get('今开', 0) or 0),
                        'high': float(row.get('最高', 0) or 0),
                        'low': float(row.get('最低', 0) or 0),
                        'pre_close': float(row.get('昨收', 0) or 0),
                        'volume': int(float(row.get('成交量', 0) or 0)),
                        'amount': float(row.get('成交额', 0) or 0),
                        'turnover': float(row.get('换手率', 0) or 0),
                        'pe': float(row.get('市盈率-动态', 0) or 0),
                        'pb': float(row.get('市净率', 0) or 0),
                        'total_mv': float(row.get('总市值', 0) or 0),
                        'circ_mv': float(row.get('流通市值', 0) or 0),
                        'update_time': datetime.utcnow().isoformat(),
                    })
            
            yield FinanceData(
                source='akshare',
                data_type='quote',
                symbol='*' if not symbols else symbols[0],
                timestamp=datetime.utcnow().isoformat(),
                payload={'quotes': results, 'count': len(results)}
            )
        except Exception as e:
            logger.error(f"实时行情获取失败: {e}")
            yield FinanceData(
                source='akshare',
                data_type='quote',
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'error': str(e)}
            )

    async def _fetch_kline(
        self,
        symbols: List[str],
        start: Optional[datetime],
        end: Optional[datetime],
        **kwargs
    ) -> AsyncIterator[FinanceData]:
        """获取K线数据"""
        period = kwargs.get('period', 'daily')
        adjust = kwargs.get('adjust', 'qfq')
        
        for symbol in symbols:
            code = symbol.split('.')[0]
            start_date = start.strftime('%Y%m%d') if start else '20240101'
            end_date = end.strftime('%Y%m%d') if end else datetime.now().strftime('%Y%m%d')
            
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period=period,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust
                )
                
                records = []
                for _, row in df.iterrows():
                    records.append({
                        'date': str(row.get('日期', '')),
                        'open': float(row.get('开盘', 0) or 0),
                        'high': float(row.get('最高', 0) or 0),
                        'low': float(row.get('最低', 0) or 0),
                        'close': float(row.get('收盘', 0) or 0),
                        'volume': int(float(row.get('成交量', 0) or 0)),
                        'amount': float(row.get('成交额', 0) or 0),
                        'amplitude': float(row.get('振幅', 0) or 0),
                        'change_pct': float(row.get('涨跌幅', 0) or 0),
                        'change_amt': float(row.get('涨跌额', 0) or 0),
                        'turnover': float(row.get('换手率', 0) or 0),
                    })
                
                yield FinanceData(
                    source='akshare',
                    data_type='kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={'klines': records, 'count': len(records), 'period': period, 'adjust': adjust}
                )
            except Exception as e:
                logger.error(f"K线获取失败 [{symbol}]: {e}")
                yield FinanceData(
                    source='akshare',
                    data_type='kline',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={'error': str(e)}
                )

    async def _fetch_financial(self, symbols: List[str], **kwargs) -> AsyncIterator[FinanceData]:
        """获取财务报表"""
        report_type = kwargs.get('report_type', '资产负债表')
        
        for symbol in symbols:
            code = symbol.split('.')[0]
            try:
                df = ak.stock_financial_report_sina(stock=code, symbol=report_type)
                
                records = []
                for _, row in df.iterrows():
                    # 处理 pandas Series 和 dict 两种情况
                    if hasattr(row, 'to_dict'):
                        records.append(row.to_dict())
                    else:
                        records.append(dict(row))
                
                yield FinanceData(
                    source='akshare',
                    data_type='financial',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={'records': records, 'count': len(records), 'report_type': report_type}
                )
            except Exception as e:
                logger.error(f"财务报表获取失败 [{symbol}]: {e}")
                yield FinanceData(
                    source='akshare',
                    data_type='financial',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={'error': str(e)}
                )

    async def _fetch_dividend(self, symbols: List[str], **kwargs) -> AsyncIterator[FinanceData]:
        """获取分红数据"""
        for symbol in symbols:
            code = symbol.split('.')[0]
            try:
                df = ak.stock_fhps_detail_em(symbol=code)
                
                records = []
                for _, row in df.iterrows():
                    records.append(row.to_dict())
                
                yield FinanceData(
                    source='akshare',
                    data_type='dividend',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={'records': records, 'count': len(records)}
                )
            except Exception as e:
                logger.error(f"分红数据获取失败 [{symbol}]: {e}")
                yield FinanceData(
                    source='akshare',
                    data_type='dividend',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={'error': str(e)}
                )

    async def _fetch_lhb(
        self,
        symbols: List[str],
        start: Optional[datetime],
        end: Optional[datetime],
        **kwargs
    ) -> AsyncIterator[FinanceData]:
        """获取龙虎榜数据"""
        start_date = start.strftime('%Y%m%d') if start else (datetime.now() - __import__('datetime').timedelta(days=30)).strftime('%Y%m%d')
        end_date = end.strftime('%Y%m%d') if end else datetime.now().strftime('%Y%m%d')
        
        try:
            df = ak.stock_lhb_detail_em(start_date=start_date, end_date=end_date)
            
            results = []
            for _, row in df.iterrows():
                code = str(row.get('代码', ''))
                if not symbols or code in symbols or f'{code}.SH' in symbols or f'{code}.SZ' in symbols:
                    results.append({
                        'date': str(row.get('龙虎榜日期', '')),
                        'symbol': f'{code}.SH' if code.startswith(('60', '68', '90')) else f'{code}.SZ',
                        'name': row.get('名称', ''),
                        'explain': row.get('解释说明', ''),
                        'buy_amount': float(row.get('买入金额', 0) or 0),
                        'sell_amount': float(row.get('卖出金额', 0) or 0),
                        'net_amount': float(row.get('净买入', 0) or 0),
                        'buy_seat': row.get('买入营业部', ''),
                        'sell_seat': row.get('卖出营业部', ''),
                    })
            
            yield FinanceData(
                source='akshare',
                data_type='lhb',
                symbol='*' if not symbols else symbols[0],
                timestamp=datetime.utcnow().isoformat(),
                payload={'records': results, 'count': len(results), 'start_date': start_date, 'end_date': end_date}
            )
        except Exception as e:
            logger.error(f"龙虎榜获取失败: {e}")
            yield FinanceData(
                source='akshare',
                data_type='lhb',
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'error': str(e)}
            )

    async def _fetch_northbound(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取北向资金数据"""
        try:
            # 汇总数据
            df_summary = ak.stock_hsgt_fund_flow_summary_em()
            summary_records = []
            for _, row in df_summary.iterrows():
                summary_records.append(row.to_dict())
            
            yield FinanceData(
                source='akshare',
                data_type='northbound',
                symbol='NORTHBOUND',
                timestamp=datetime.utcnow().isoformat(),
                payload={'summary': summary_records, 'count': len(summary_records)}
            )
        except Exception as e:
            logger.error(f"北向资金汇总获取失败: {e}")
            yield FinanceData(
                source='akshare',
                data_type='northbound',
                symbol='NORTHBOUND',
                timestamp=datetime.utcnow().isoformat(),
                payload={'error': str(e)}
            )

    async def _fetch_stock_basic(self, **kwargs) -> AsyncIterator[FinanceData]:
        """获取股票基础信息"""
        try:
            df = ak.stock_zh_a_spot_em()
            
            results = []
            for _, row in df.iterrows():
                code = str(row.get('代码', ''))
                results.append({
                    'symbol': f'{code}.SH' if code.startswith(('60', '68', '90')) else f'{code}.SZ',
                    'code': code,
                    'name': row.get('名称', ''),
                    'latest_price': float(row.get('最新价', 0) or 0),
                    'change_pct': float(row.get('涨跌幅', 0) or 0),
                    'total_mv': float(row.get('总市值', 0) or 0),
                    'circ_mv': float(row.get('流通市值', 0) or 0),
                })
            
            yield FinanceData(
                source='akshare',
                data_type='stock_basic',
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'stocks': results, 'count': len(results)}
            )
        except Exception as e:
            logger.error(f"股票基础信息获取失败: {e}")
            yield FinanceData(
                source='akshare',
                data_type='stock_basic',
                symbol='*',
                timestamp=datetime.utcnow().isoformat(),
                payload={'error': str(e)}
            )

    async def close(self):
        """关闭资源"""
        pass


# 便捷函数
def create_scraper(source: str = 'stock') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'stock':
        return StockScraper()
    raise ValueError(f"Unknown source: {source}")


# 同步便捷函数

def fetch_stock_quote(symbols: List[str] = None, source: str = 'akshare') -> List[FinanceData]:
    """同步获取股票实时行情"""
    import asyncio
    
    async def _async_fetch():
        scraper = StockScraper()
        results = []
        async for data in scraper.fetch(symbols or [], 'quote'):
            results.append(data)
        return results
    
    return asyncio.run(_async_fetch())


def fetch_stock_kline(
    symbol: str,
    period: str = 'daily',
    start: str = '20240101',
    end: str = None,
    adjust: str = 'qfq',
    source: str = 'akshare'
) -> List[FinanceData]:
    """同步获取股票K线数据"""
    import asyncio
    from datetime import datetime
    
    start_dt = datetime.strptime(start, '%Y%m%d') if start else None
    end_dt = datetime.strptime(end, '%Y%m%d') if end else None
    
    async def _async_fetch():
        scraper = StockScraper()
        results = []
        async for data in scraper.fetch([symbol], 'kline', start=start_dt, end=end_dt, period=period, adjust=adjust):
            results.append(data)
        return results
    
    return asyncio.run(_async_fetch())


def fetch_stock_financial(symbol: str, report_type: str = '资产负债表', source: str = 'akshare') -> List[FinanceData]:
    """同步获取股票财务报表"""
    import asyncio
    
    async def _async_fetch():
        scraper = StockScraper()
        results = []
        async for data in scraper.fetch([symbol], 'financial', report_type=report_type):
            results.append(data)
        return results
    
    return asyncio.run(_async_fetch())


def fetch_stock_dividend(symbol: str, source: str = 'akshare') -> List[FinanceData]:
    """同步获取股票分红数据"""
    import asyncio
    
    async def _async_fetch():
        scraper = StockScraper()
        results = []
        async for data in scraper.fetch([symbol], 'dividend'):
            results.append(data)
        return results
    
    return asyncio.run(_async_fetch())


def fetch_stock_lhb(
    symbols: List[str] = None,
    start_date: str = None,
    end_date: str = None,
    source: str = 'akshare'
) -> List[FinanceData]:
    """同步获取龙虎榜数据"""
    import asyncio
    from datetime import datetime, timedelta
    
    start_dt = datetime.strptime(start_date, '%Y%m%d') if start_date else None
    end_dt = datetime.strptime(end_date, '%Y%m%d') if end_date else None
    
    async def _async_fetch():
        scraper = StockScraper()
        results = []
        async for data in scraper.fetch(symbols or [], 'lhb', start=start_dt, end=end_dt):
            results.append(data)
        return results
    
    return asyncio.run(_async_fetch())


def fetch_stock_northbound(source: str = 'akshare') -> List[FinanceData]:
    """同步获取北向资金数据"""
    import asyncio
    
    async def _async_fetch():
        scraper = StockScraper()
        results = []
        async for data in scraper.fetch([], 'northbound'):
            results.append(data)
        return results
    
    return asyncio.run(_async_fetch())


def fetch_stock_basic(source: str = 'akshare') -> List[FinanceData]:
    """同步获取股票基础信息"""
    import asyncio
    import nest_asyncio

    # 防止在已有事件循环中调用 asyncio.run()
    try:
        nest_asyncio.apply()
    except RuntimeError:
        pass  # 没有事件循环，直接使用 asyncio.run()

    async def _async_fetch():
        scraper = StockScraper()
        results = []
        async for data in scraper.fetch([], 'stock_basic'):
            results.append(data)
        return results

    return asyncio.run(_async_fetch())
