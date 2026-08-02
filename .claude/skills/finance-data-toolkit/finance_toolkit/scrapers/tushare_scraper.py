# -*- coding: utf-8 -*-
"""
Tushare Pro 抓取器实现
数据源: Tushare Pro (需 token，专业级金融数据)
支持: 实时行情、历史K线、财务报表、分红配股、股本结构、龙虎榜、北向资金、基金/期货/期权等
"""

from datetime import datetime
from typing import List, Optional, AsyncIterator

try:
    import tushare as ts
    HAS_TUSHARE = True
except ImportError:
    HAS_TUSHARE = False
    ts = None

from ..core import BaseScraper, FinanceData, register_scraper


@register_scraper
class TushareScraper(BaseScraper):
    """Tushare Pro 数据抓取器"""
    
    def __init__(self, token: str = None, **kwargs):
        super().__init__()
        self.token = token
        self.pro = None
        if HAS_TUSHARE and token:
            ts.set_token(token)
            self.pro = ts.pro_api()
    
    @property
    def source_name(self) -> str:
        return 'tushare'
    
    @property
    def supported_types(self) -> List[str]:
        return ['quote', 'kline', 'financial', 'dividend', 'shareholder', 'lhb', 'northbound', 'fund', 'futures', 'option', 'index', 'block']
    
    async def health_check(self) -> bool:
        if not HAS_TUSHARE or not self.pro:
            return False
        try:
            df = self.pro.stock_basic(exchange='', list_status='L', fields='ts_code')
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
        if not HAS_TUSHARE:
            raise RuntimeError("Tushare not installed. Run: pip install tushare")
        if not self.pro:
            raise RuntimeError("Tushare token not configured")
        
        # 标准化代码格式: 600000.SH -> 600000.SH (tushare格式一致)
        codes = [s.upper() for s in symbols]
        
        # Handle start/end as either datetime or string
        def to_date_str(dt, default):
            if dt is None:
                return default
            if isinstance(dt, str):
                return dt
            return dt.strftime('%Y%m%d')
        
        if data_type == 'quote':
            async for item in self._fetch_realtime_quote(codes):
                yield item
        elif data_type == 'kline':
            period = kwargs.get('period', 'D')  # D=日线, W=周线, M=月线, 60=60分钟等
            adj = kwargs.get('adj', 'qfq')  # qfq=前复权, hfq=后复权, None=不复权
            start_str = to_date_str(start, '20240101')
            end_str = to_date_str(end, datetime.now().strftime('%Y%m%d'))
            async for item in self._fetch_kline(codes, period, start_str, end_str, adj):
                yield item
        elif data_type == 'financial':
            async for item in self._fetch_financial(codes):
                yield item
        elif data_type == 'dividend':
            async for item in self._fetch_dividend(codes):
                yield item
        elif data_type == 'shareholder':
            async for item in self._fetch_shareholder(codes):
                yield item
        elif data_type == 'lhb':
            async for item in self._fetch_lhb(codes):
                yield item
        elif data_type == 'northbound':
            async for item in self._fetch_northbound():
                yield item
        elif data_type == 'fund':
            async for item in self._fetch_fund(codes):
                yield item
        elif data_type == 'futures':
            async for item in self._fetch_futures(codes):
                yield item
        elif data_type == 'option':
            async for item in self._fetch_option(codes):
                yield item
        elif data_type == 'index':
            async for item in self._fetch_index(codes):
                yield item
        elif data_type == 'block':
            async for item in self._fetch_block(codes):
                yield item
        else:
            raise ValueError(f"Unsupported data_type: {data_type}")
    
    async def _fetch_realtime_quote(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取实时行情 (需权限)"""
        try:
            # Tushare 实时行情接口
            df = self.pro.realtime_quote(ts_code=','.join(codes))
            
            for _, row in df.iterrows():
                payload = {
                    'open': row.get('open', 0),
                    'high': row.get('high', 0),
                    'low': row.get('low', 0),
                    'close': row.get('price', 0),
                    'pre_close': row.get('pre_close', 0),
                    'volume': row.get('vol', 0),
                    'amount': row.get('amount', 0),
                    'change_pct': row.get('pct_chg', 0),
                    'change_amt': row.get('change', 0),
                    'turnover': row.get('turnover_rate', 0),
                    'pe_ttm': row.get('pe_ttm', 0),
                    'pb': row.get('pb', 0),
                    'total_mv': row.get('total_mv', 0),
                    'circ_mv': row.get('circ_mv', 0),
                }
                yield FinanceData(
                    source='tushare',
                    data_type='quote',
                    symbol=row['ts_code'],
                    timestamp=datetime.utcnow(),
                    payload=payload
                )
        except Exception:
            # 降级：使用 daily 接口获取最新收盘价
            for code in codes:
                try:
                    df = self.pro.daily(ts_code=code, limit=1)
                    if not df.empty:
                        row = df.iloc[0]
                        payload = {
                            'open': row['open'],
                            'high': row['high'],
                            'low': row['low'],
                            'close': row['close'],
                            'pre_close': row['pre_close'],
                            'volume': row['vol'],
                            'amount': row['amount'],
                            'change_pct': row['pct_chg'],
                            'change_amt': row['close'] - row['pre_close'],
                        }
                        yield FinanceData(
                            source='tushare',
                            data_type='quote',
                            symbol=code,
                            timestamp=datetime.utcnow(),
                            payload=payload,
                            meta={'fallback': 'daily'}
                        )
                except Exception:
                    pass
    
    async def _fetch_kline(self, codes: List[str], period: str, start: str, end: str, adj: str) -> AsyncIterator[FinanceData]:
        """获取历史 K 线"""
        period_map = {
            'daily': 'D', 'weekly': 'W', 'monthly': 'M',
            '1m': '1min', '5m': '5min', '15m': '15min', '30m': '30min', '60m': '60min',
        }
        freq = period_map.get(period, 'D')
        
        adj_map = {'qfq': 'qfq', 'hfq': 'hfq', 'none': None}
        adj_factor = adj_map.get(adj, 'qfq')
        
        for code in codes:
            try:
                if freq in ['1min', '5min', '15min', '30min', '60min']:
                    df = self.pro.stk_mins(ts_code=code, freq=freq, start_date=start, end_date=end, adj=adj_factor)
                else:
                    df = self.pro.daily(ts_code=code, start_date=start, end_date=end)
                    if adj_factor and not df.empty:
                        # 获取复权因子
                        adj_df = self.pro.adj_factor(ts_code=code, start_date=start, end_date=end)
                        if not adj_df.empty:
                            df = df.merge(adj_df[['trade_date', 'adj_factor']], on='trade_date', how='left')
                            # 简化：实际复权计算较复杂，这里仅返回原始数据
                
                if df.empty:
                    continue
                
                # 统一列名
                df = df.rename(columns={
                    'trade_date': 'date', 'open': 'open', 'close': 'close',
                    'high': 'high', 'low': 'low', 'vol': 'volume',
                    'amount': 'amount', 'pct_chg': 'change_pct', 'change': 'change_amt',
                })
                
                # 日期格式标准化
                if 'date' in df.columns:
                    df['date'] = df['date'].astype(str)
                
                kline_data = df.to_dict('records')
                
                yield FinanceData(
                    source='tushare',
                    data_type='kline',
                    symbol=code,
                    timestamp=datetime.utcnow(),
                    payload={
                        'period': period,
                        'adjust': adj,
                        'count': len(kline_data),
                        'data': kline_data
                    },
                    meta={'code': code, 'start': start, 'end': end, 'freq': freq}
                )
            except Exception as e:
                yield FinanceData(
                    source='tushare',
                    data_type='kline',
                    symbol=code,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'code': code}
                )
    
    async def _fetch_financial(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取财务报表 (利润表/资产负债表/现金流量表)"""
        for code in codes:
            try:
                # 主要财务指标
                df = self.pro.fina_indicator(ts_code=code)
                if not df.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='financial',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records'),
                        meta={'report_type': 'indicator'}
                    )
                
                # 利润表
                df_income = self.pro.income(ts_code=code)
                if not df_income.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='financial',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df_income.to_dict('records'),
                        meta={'report_type': 'income'}
                    )
                
                # 资产负债表
                df_balance = self.pro.balancesheet(ts_code=code)
                if not df_balance.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='financial',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df_balance.to_dict('records'),
                        meta={'report_type': 'balancesheet'}
                    )
                
                # 现金流量表
                df_cashflow = self.pro.cashflow(ts_code=code)
                if not df_cashflow.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='financial',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df_cashflow.to_dict('records'),
                        meta={'report_type': 'cashflow'}
                    )
            except Exception as e:
                yield FinanceData(
                    source='tushare',
                    data_type='financial',
                    symbol=code,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'code': code}
                )
    
    async def _fetch_dividend(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取分红配股"""
        for code in codes:
            try:
                df = self.pro.dividend(ts_code=code)
                if not df.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='dividend',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records')
                    )
            except Exception:
                pass
    
    async def _fetch_shareholder(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取股本结构/股东信息"""
        for code in codes:
            try:
                # 十大股东
                df = self.pro.top10_holders(ts_code=code)
                if not df.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='shareholder',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records'),
                        meta={'holder_type': 'top10'}
                    )
                
                # 十大流通股东
                df_float = self.pro.top10_floatholders(ts_code=code)
                if not df_float.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='shareholder',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df_float.to_dict('records'),
                        meta={'holder_type': 'float_top10'}
                    )
            except Exception:
                pass
    
    async def _fetch_lhb(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取龙虎榜"""
        try:
            df = self.pro.top_inst()
            if not df.empty:
                filtered = df[df['ts_code'].isin(codes)]
                if not filtered.empty:
                    for _, row in filtered.iterrows():
                        yield FinanceData(
                            source='tushare',
                            data_type='lhb',
                            symbol=row['ts_code'],
                            timestamp=datetime.utcnow(),
                            payload=row.to_dict()
                        )
        except Exception:
            pass
    
    async def _fetch_northbound(self) -> AsyncIterator[FinanceData]:
        """获取北向资金"""
        try:
            df = self.pro.moneyflow_hsgt()
            if not df.empty:
                yield FinanceData(
                    source='tushare',
                    data_type='northbound',
                    symbol='SH_HSGT',
                    timestamp=datetime.utcnow(),
                    payload=df.to_dict('records')
                )
        except Exception:
            pass
    
    async def _fetch_fund(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取基金数据"""
        for code in codes:
            try:
                df = self.pro.fund_basic(ts_code=code)
                if not df.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='fund',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records')
                    )
            except Exception:
                pass
    
    async def _fetch_futures(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取期货数据"""
        for code in codes:
            try:
                df = self.pro.fut_basic(exchange='', ts_code=code)
                if not df.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='futures',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records')
                    )
            except Exception:
                pass
    
    async def _fetch_option(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取期权数据"""
        for code in codes:
            try:
                df = self.pro.opt_basic(exchange='', ts_code=code)
                if not df.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='option',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records')
                    )
            except Exception:
                pass
    
    async def _fetch_index(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取指数数据"""
        for code in codes:
            try:
                df = self.pro.index_daily(ts_code=code)
                if not df.empty:
                    yield FinanceData(
                        source='tushare',
                        data_type='index',
                        symbol=code,
                        timestamp=datetime.utcnow(),
                        payload=df.to_dict('records')
                    )
            except Exception:
                pass
    
    async def _fetch_block(self, codes: List[str]) -> AsyncIterator[FinanceData]:
        """获取板块/概念数据"""
        try:
            df = self.pro.concept()
            if not df.empty:
                yield FinanceData(
                    source='tushare',
                    data_type='block',
                    symbol='CONCEPT',
                    timestamp=datetime.utcnow(),
                    payload=df.to_dict('records')
                )
            
            df_industry = self.pro.index_classify(level='L1', src='SW')
            if not df_industry.empty:
                yield FinanceData(
                    source='tushare',
                    data_type='block',
                    symbol='INDUSTRY_SW',
                    timestamp=datetime.utcnow(),
                    payload=df_industry.to_dict('records')
                )
        except Exception:
            pass
    
    async def close(self):
        """关闭连接"""
        pass


# 便捷函数
async def create_scraper(token: str = None) -> TushareScraper:
    """创建 Tushare 抓取器实例"""
    return TushareScraper(token=token)