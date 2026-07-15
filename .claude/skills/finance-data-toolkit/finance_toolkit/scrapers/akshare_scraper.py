# -*- coding: utf-8 -*-
"""
AKShare 抓取器实现
数据源: AKShare (免费、无需token的Python财经数据库)
支持: 实时行情、历史K线、财务报表、分红配股、龙虎榜、北向资金等
"""

import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False
    ak = None

from ..core import BaseScraper, FinanceData, register_scraper


@register_scraper
class AKShareScraper(BaseScraper):
    """AKShare 数据抓取器"""
    
    @property
    def source_name(self) -> str:
        return 'akshare'
    
    @property
    def supported_types(self) -> List[str]:
        return ['quote', 'kline', 'financial', 'dividend', 'shareholder', 'lhb', 'northbound']
    
    async def health_check(self) -> bool:
        if not HAS_AKSHARE:
            return False
        try:
            # 简单测试连接
            df = ak.stock_zh_a_spot_em()
            return not df.empty
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
        if not HAS_AKSHARE:
            raise RuntimeError("AKShare not installed. Run: pip install akshare")
        
        # 标准化代码格式: 600000.SH -> 600000
        codes = [s.split('.')[0] for s in symbols]
        
        if data_type == 'quote':
            async for item in self._fetch_realtime_quote(codes, symbols):
                yield item
        elif data_type == 'kline':
            period = kwargs.get('period', 'daily')
            adjust = kwargs.get('adjust', 'qfq')
            start_str = start.strftime('%Y%m%d') if start else '20240101'
            end_str = end.strftime('%Y%m%d') if end else datetime.now().strftime('%Y%m%d')
            async for item in self._fetch_kline(codes, symbols, period, start_str, end_str, adjust):
                yield item
        elif data_type == 'financial':
            async for item in self._fetch_financial(codes, symbols):
                yield item
        elif data_type == 'dividend':
            async for item in self._fetch_dividend(codes, symbols):
                yield item
        elif data_type == 'shareholder':
            async for item in self._fetch_shareholder(codes, symbols):
                yield item
        elif data_type == 'lhb':
            async for item in self._fetch_lhb(codes, symbols):
                yield item
        elif data_type == 'northbound':
            async for item in self._fetch_northbound(codes, symbols):
                yield item
        else:
            raise ValueError(f"Unsupported data_type: {data_type}")
    
    async def _fetch_realtime_quote(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取 A 股实时行情 (东方财富接口)"""
        df = ak.stock_zh_a_spot_em()
        
        # 标准化代码格式
        df['symbol'] = df['代码'].apply(lambda x: f"{x}.SZ" if x.startswith(('0','3')) else f"{x}.SH")
        
        for _, row in df[df['symbol'].isin(original_symbols)].iterrows():
            payload = {
                'open': row['今开'],
                'high': row['最高'],
                'low': row['最低'],
                'close': row['最新价'],
                'pre_close': row['昨收'],
                'volume': row['成交量'],
                'amount': row['成交额'],
                'change_pct': row['涨跌幅'],
                'change_amt': row['涨跌额'],
                'turnover': row['换手率'],
                'pe_ttm': row['市盈率-动态'],
                'pb': row['市净率'],
                'total_mv': row['总市值'],
                'circ_mv': row['流通市值']
            }
            yield FinanceData(
                source='akshare',
                data_type='quote',
                symbol=row['symbol'],
                timestamp=datetime.utcnow(),
                payload=payload
            )
    
    async def _fetch_kline(self, codes: List[str], original_symbols: List[str], 
                           period: str, start: str, end: str, adjust: str) -> AsyncIterator[FinanceData]:
        """获取历史 K 线"""
        period_map = {
            'daily': 'daily',
            'weekly': 'weekly',
            'monthly': 'monthly',
            '1m': '1m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '60m': '60m',
        }
        ak_period = period_map.get(period, 'daily')
        
        for code, symbol in zip(codes, original_symbols):
            df = ak.stock_zh_a_hist(
                symbol=code,
                period=ak_period,
                start_date=start,
                end_date=end,
                adjust=adjust
            )
            
            if df.empty:
                continue
            
            # 统一列名
            df = df.rename(columns={
                '日期': 'date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'volume',
                '成交额': 'amount', '振幅': 'amplitude',
                '涨跌幅': 'change_pct', '涨跌额': 'change_amt',
                '换手率': 'turnover'
            })
            
            kline_data = df.to_dict('records')
            
            yield FinanceData(
                source='akshare',
                data_type='kline',
                symbol=symbol,
                timestamp=datetime.utcnow(),
                payload={
                    'period': period,
                    'adjust': adjust,
                    'count': len(kline_data),
                    'data': kline_data
                },
                meta={'code': code, 'start': start, 'end': end}
            )
    
    async def _fetch_financial(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取财务报表"""
        for code, symbol in zip(codes, original_symbols):
            try:
                # 主要财务指标
                df = ak.stock_financial_abstract_ths(symbol=code)
                if not df.empty:
                    yield FinanceData(
                        source='akshare',
                        data_type='financial',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records'),
                        meta={'report_type': 'abstract'}
                    )
            except Exception as e:
                yield FinanceData(
                    source='akshare',
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
                df = ak.stock_fhps_detail_em(symbol=code)
                if not df.empty:
                    yield FinanceData(
                        source='akshare',
                        data_type='dividend',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records')
                    )
            except Exception:
                pass
    
    async def _fetch_shareholder(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取股本结构/股东信息"""
        for code, symbol in zip(codes, original_symbols):
            try:
                df = ak.stock_gdfx_free_holding_analyse_em(symbol=code)
                if not df.empty:
                    yield FinanceData(
                        source='akshare',
                        data_type='shareholder',
                        symbol=symbol,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records')
                    )
            except Exception:
                pass
    
    async def _fetch_lhb(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取龙虎榜"""
        try:
            df = ak.stock_lhb_detail_em()
            if not df.empty:
                # 筛选相关股票
                filtered = df[df['代码'].isin(codes)]
                if not filtered.empty:
                    for _, row in filtered.iterrows():
                        symbol = f"{row['代码']}.SH" if row['代码'].startswith('6') else f"{row['代码']}.SZ"
                        yield FinanceData(
                            source='akshare',
                            data_type='lhb',
                            symbol=symbol,
                            timestamp=datetime.utcnow(),
                            payload=row.to_dict()
                        )
        except Exception:
            pass
    
    async def _fetch_northbound(self, codes: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取北向资金"""
        try:
            df = ak.stock_hsgt_hist_em(symbol='沪股通')
            if not df.empty:
                yield FinanceData(
                    source='akshare',
                    data_type='northbound',
                    symbol='SH_HSGT',
                    timestamp=datetime.utcnow(),
                    payload=df.to_dict('records')
                )
        except Exception:
            pass

    async def close(self):
        """关闭连接/释放资源"""
        pass


# 便捷函数
async def create_scraper(source: str = 'akshare') -> BaseScraper:
    """创建抓取器实例"""
    if source == 'akshare':
        return AKShareScraper()
    raise ValueError(f"Unknown source: {source}")