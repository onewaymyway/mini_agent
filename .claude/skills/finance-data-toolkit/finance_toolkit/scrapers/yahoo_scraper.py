# -*- coding: utf-8 -*-
"""
Yahoo Finance 抓取器实现
数据源: Yahoo Finance (finance.yahoo.com)
支持: 实时行情、历史K线、财务报表、期权链、基本面指标、分红配股
特点: 免费、无需token、全球市场覆盖(美股、港股、A股、加密货币等)、支持分钟级K线
"""

from datetime import datetime, timedelta
from typing import List, Optional, AsyncIterator

import yfinance as yf

from ..core import BaseScraper, FinanceData, register_scraper


# Yahoo Finance 代码格式映射
# A股: 600000.SS (沪市), 000001.SZ (深市)
# 港股: 0700.HK
# 美股: AAPL, TSLA
# 加密货币: BTC-USD, ETH-USD

def to_yahoo_symbol(symbol: str) -> str:
    """转换为 Yahoo Finance 格式
    600000.SH -> 600000.SS
    000001.SZ -> 000001.SZ
    0700.HK -> 0700.HK
    AAPL -> AAPL
    """
    if '.' not in symbol:
        return symbol
    
    code, market = symbol.split('.')
    if market == 'SH':
        return f'{code}.SS'
    elif market == 'SZ':
        return f'{code}.SZ'
    elif market == 'HK':
        return f'{code}.HK'
    else:
        return symbol


def from_yahoo_symbol(yahoo_code: str) -> str:
    """转换回标准格式
    600000.SS -> 600000.SH
    000001.SZ -> 000001.SZ
    0700.HK -> 0700.HK
    """
    if '.' not in yahoo_code:
        return yahoo_code
    
    code, market = yahoo_code.split('.')
    if market == 'SS':
        return f'{code}.SH'
    elif market == 'SZ':
        return f'{code}.SZ'
    elif market == 'HK':
        return f'{code}.HK'
    else:
        return yahoo_code


@register_scraper
class YahooScraper(BaseScraper):
    """Yahoo Finance 数据抓取器"""
    
    def __init__(self, proxy: str = None, timeout: int = 30, **kwargs):
        super().__init__()
        self.proxy = proxy
        self.timeout = timeout
        # yfinance 使用 requests 底层，通过 session 设置代理
        self._session = None
        if proxy:
            self._create_session_with_proxy()
    
    def _create_session_with_proxy(self):
        """创建带代理的 curl_cffi session (yfinance 使用 curl_cffi)"""
        import curl_cffi.requests
        self._session = curl_cffi.requests.Session()
        self._session.proxies = {
            'http': self.proxy,
            'https': self.proxy
        }
        # 设置超时
        self._session.timeout = self.timeout
    
    @property
    def source_name(self) -> str:
        return 'yahoo'
    
    @property
    def supported_types(self) -> List[str]:
        return ['quote', 'kline', 'financial', 'dividend', 'options', 'info', 'holders', 'recommendations', 'calendar']
    
    async def health_check(self) -> bool:
        try:
            ticker = yf.Ticker('AAPL')
            info = ticker.info
            return 'symbol' in info or 'shortName' in info
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
        yahoo_symbols = [to_yahoo_symbol(s) for s in symbols]
        
        # Handle start/end as either datetime or string
        def to_date_str(dt, default):
            if dt is None:
                return default
            if isinstance(dt, str):
                return dt
            return dt.strftime('%Y-%m-%d')
        
        if data_type == 'quote':
            async for item in self._fetch_realtime_quote(yahoo_symbols, symbols):
                yield item
        elif data_type == 'kline':
            period = kwargs.get('period', '1d')  # 1d, 1wk, 1mo, 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h
            interval = kwargs.get('interval', period)
            start_str = to_date_str(start, (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'))
            end_str = to_date_str(end, datetime.now().strftime('%Y-%m-%d'))
            async for item in self._fetch_kline(yahoo_symbols, symbols, period, interval, start_str, end_str):
                yield item
        elif data_type == 'financial':
            async for item in self._fetch_financial(yahoo_symbols, symbols):
                yield item
        elif data_type == 'dividend':
            async for item in self._fetch_dividend(yahoo_symbols, symbols):
                yield item
        elif data_type == 'options':
            async for item in self._fetch_options(yahoo_symbols, symbols):
                yield item
        elif data_type == 'info':
            async for item in self._fetch_info(yahoo_symbols, symbols):
                yield item
        elif data_type == 'holders':
            async for item in self._fetch_holders(yahoo_symbols, symbols):
                yield item
        elif data_type == 'recommendations':
            async for item in self._fetch_recommendations(yahoo_symbols, symbols):
                yield item
        elif data_type == 'calendar':
            async for item in self._fetch_calendar(yahoo_symbols, symbols):
                yield item
        else:
            raise ValueError(f"Unsupported data_type: {data_type}")
    
    async def _fetch_realtime_quote(self, yahoo_symbols: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取实时行情 (使用 fast_info 或 info)"""
        for ysym, sym in zip(yahoo_symbols, original_symbols):
            try:
                ticker = yf.Ticker(ysym, session=self._session)
                # 使用 fast_info 获取快速实时数据
                fast_info = ticker.fast_info
                
                payload = {
                    'symbol': ysym,
                    'close': fast_info.get('lastPrice', 0),
                    'price': fast_info.get('lastPrice', 0),
                    'open': fast_info.get('open', 0),
                    'high': fast_info.get('dayHigh', 0),
                    'low': fast_info.get('dayLow', 0),
                    'pre_close': fast_info.get('previousClose', 0),
                    'volume': fast_info.get('lastVolume', 0),
                    'market_cap': fast_info.get('marketCap', 0),
                    'currency': fast_info.get('currency', 'USD'),
                    'timezone': fast_info.get('timezone', 'UTC'),
                }
                
                # 计算衍生字段
                if payload['pre_close'] > 0:
                    payload['change_amt'] = round(payload['close'] - payload['pre_close'], 4)
                    payload['change_pct'] = round(payload['change_amt'] / payload['pre_close'] * 100, 2)
                
                yield FinanceData(
                    source='yahoo',
                    data_type='quote',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload=payload
                )
            except Exception as e:
                yield FinanceData(
                    source='yahoo',
                    data_type='quote',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'yahoo_symbol': ysym}
                )
    
    async def _fetch_kline(self, yahoo_symbols: List[str], original_symbols: List[str], 
                           period: str, interval: str, start: str, end: str) -> AsyncIterator[FinanceData]:
        """获取历史 K 线
        period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
        interval: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo
        注意: 分钟级数据只能获取最近 7-30 天
        优先使用 start/end 参数，period 作为备选
        """
        for ysym, sym in zip(yahoo_symbols, original_symbols):
            try:
                ticker = yf.Ticker(ysym, session=self._session)
                
                # 使用 history 方法获取 K 线 - 优先使用 start/end
                if start and end:
                    df = ticker.history(
                        start=start,
                        end=end,
                        interval=interval,
                        auto_adjust=True,  # 自动复权
                        prepost=False,     # 不包含盘前盘后
                        actions=False      # 不包含分红拆股
                    )
                else:
                    df = ticker.history(
                        period=period,
                        interval=interval,
                        auto_adjust=True,
                        prepost=False,
                        actions=False
                    )
                
                if df.empty:
                    yield FinanceData(
                        source='yahoo',
                        data_type='kline',
                        symbol=sym,
                        timestamp=datetime.utcnow(),
                        payload={'error': 'Empty data', 'period': period, 'interval': interval},
                        meta={'yahoo_symbol': ysym}
                    )
                    continue
                
                # 重置索引，将日期转为列
                df = df.reset_index()
                
                # 统一列名
                kline_data = []
                for _, row in df.iterrows():
                    kline_data.append({
                        'date': row['Date'].strftime('%Y-%m-%d') if hasattr(row['Date'], 'strftime') else str(row['Date']),
                        'open': float(row['Open']),
                        'high': float(row['High']),
                        'low': float(row['Low']),
                        'close': float(row['Close']),
                        'volume': int(row['Volume']),
                        'amount': float(row['Close'] * row['Volume']),  # 估算成交额
                    })
                
                yield FinanceData(
                    source='yahoo',
                    data_type='kline',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={
                        'period': period,
                        'interval': interval,
                        'count': len(kline_data),
                        'data': kline_data
                    },
                    meta={'yahoo_symbol': ysym, 'start': start, 'end': end}
                )
            except Exception as e:
                yield FinanceData(
                    source='yahoo',
                    data_type='kline',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'yahoo_symbol': ysym}
                )
    
    async def _fetch_financial(self, yahoo_symbols: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取财务报表 (利润表、资产负债表、现金流量表)"""
        for ysym, sym in zip(yahoo_symbols, original_symbols):
            try:
                ticker = yf.Ticker(ysym, session=self._session)
                
                # 获取三大报表
                income_stmt = ticker.income_stmt  # 利润表
                balance_sheet = ticker.balance_sheet  # 资产负债表
                cash_flow = ticker.cashflow  # 现金流量表
                
                # 也可以获取季度数据
                quarterly_income = ticker.quarterly_income_stmt
                quarterly_balance = ticker.quarterly_balance_sheet
                quarterly_cashflow = ticker.quarterly_cashflow
                
                payload = {
                    'annual': {
                        'income_statement': income_stmt.to_dict() if not income_stmt.empty else {},
                        'balance_sheet': balance_sheet.to_dict() if not balance_sheet.empty else {},
                        'cash_flow': cash_flow.to_dict() if not cash_flow.empty else {},
                    },
                    'quarterly': {
                        'income_statement': quarterly_income.to_dict() if not quarterly_income.empty else {},
                        'balance_sheet': quarterly_balance.to_dict() if not quarterly_balance.empty else {},
                        'cash_flow': quarterly_cashflow.to_dict() if not quarterly_cashflow.empty else {},
                    }
                }
                
                yield FinanceData(
                    source='yahoo',
                    data_type='financial',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload=payload,
                    meta={'yahoo_symbol': ysym}
                )
            except Exception as e:
                yield FinanceData(
                    source='yahoo',
                    data_type='financial',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'yahoo_symbol': ysym}
                )
    
    async def _fetch_dividend(self, yahoo_symbols: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取分红配股历史"""
        for ysym, sym in zip(yahoo_symbols, original_symbols):
            try:
                ticker = yf.Ticker(ysym, session=self._session)
                
                # 获取分红历史
                dividends = ticker.dividends
                splits = ticker.splits
                
                dividend_data = []
                if not dividends.empty:
                    for date, amount in dividends.items():
                        dividend_data.append({
                            'date': date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date),
                            'dividend': float(amount),
                            'type': 'cash'
                        })
                
                split_data = []
                if not splits.empty:
                    for date, ratio in splits.items():
                        split_data.append({
                            'date': date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date),
                            'ratio': float(ratio),
                            'type': 'split'
                        })
                
                yield FinanceData(
                    source='yahoo',
                    data_type='dividend',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={
                        'dividends': dividend_data,
                        'splits': split_data
                    },
                    meta={'yahoo_symbol': ysym}
                )
            except Exception as e:
                yield FinanceData(
                    source='yahoo',
                    data_type='dividend',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'yahoo_symbol': ysym}
                )
    
    async def _fetch_options(self, yahoo_symbols: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取期权链数据"""
        for ysym, sym in zip(yahoo_symbols, original_symbols):
            try:
                ticker = yf.Ticker(ysym, session=self._session)
                
                # 获取可用到期日
                expirations = ticker.options
                
                if not expirations:
                    yield FinanceData(
                        source='yahoo',
                        data_type='options',
                        symbol=sym,
                        timestamp=datetime.utcnow(),
                        payload={'error': 'No options available', 'expirations': []},
                        meta={'yahoo_symbol': ysym}
                    )
                    continue
                
                # 默认获取最近的到期日
                exp = expirations[0] if isinstance(expirations, list) else str(expirations)
                
                opt_chain = ticker.option_chain(exp)
                
                calls = opt_chain.calls.to_dict('records') if not opt_chain.calls.empty else []
                puts = opt_chain.puts.to_dict('records') if not opt_chain.puts.empty else []
                
                yield FinanceData(
                    source='yahoo',
                    data_type='options',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={
                        'expiration': exp,
                        'all_expirations': [str(e) for e in expirations],
                        'calls': calls,
                        'puts': puts,
                        'underlying_price': ticker.fast_info.get('last_price', 0)
                    },
                    meta={'yahoo_symbol': ysym}
                )
            except Exception as e:
                yield FinanceData(
                    source='yahoo',
                    data_type='options',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'yahoo_symbol': ysym}
                )
    
    async def _fetch_info(self, yahoo_symbols: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取股票基本信息 (公司概况、行业、估值指标等)"""
        for ysym, sym in zip(yahoo_symbols, original_symbols):
            try:
                ticker = yf.Ticker(ysym, session=self._session)
                info = ticker.info
                
                # 提取关键字段
                key_fields = [
                    'symbol', 'shortName', 'longName', 'currency', 'exchange',
                    'marketCap', 'enterpriseValue', 'trailingPE', 'forwardPE',
                    'priceToBook', 'priceToSalesTrailing12Months', 'enterpriseToRevenue',
                    'enterpriseToEbitda', 'profitMargins', 'operatingMargins',
                    'returnOnEquity', 'returnOnAssets', 'revenueGrowth', 'earningsGrowth',
                    'totalRevenue', 'totalDebt', 'totalCash', 'freeCashflow',
                    'dividendYield', 'payoutRatio', 'beta', '52WeekChange',
                    'fiftyTwoWeekHigh', 'fiftyTwoWeekLow', 'fiftyDayAverage', 'twoHundredDayAverage',
                    'sharesOutstanding', 'floatShares', 'heldPercentInsiders', 'heldPercentInstitutions',
                    'sector', 'industry', 'website', 'longBusinessSummary'
                ]
                
                payload = {k: info.get(k) for k in key_fields if k in info}
                
                yield FinanceData(
                    source='yahoo',
                    data_type='info',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload=payload,
                    meta={'yahoo_symbol': ysym}
                )
            except Exception as e:
                yield FinanceData(
                    source='yahoo',
                    data_type='info',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'yahoo_symbol': ysym}
                )
    
    async def _fetch_holders(self, yahoo_symbols: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取股东信息 (机构持仓、内部人持仓、大股东)"""
        for ysym, sym in zip(yahoo_symbols, original_symbols):
            try:
                ticker = yf.Ticker(ysym, session=self._session)
                
                institutional = ticker.institutional_holders
                major = ticker.major_holders
                insider = ticker.insider_transactions
                
                payload = {
                    'institutional_holders': institutional.to_dict('records') if institutional is not None and not institutional.empty else [],
                    'major_holders': major.to_dict('records') if major is not None and not major.empty else [],
                    'insider_transactions': insider.to_dict('records') if insider is not None and not insider.empty else [],
                }
                
                yield FinanceData(
                    source='yahoo',
                    data_type='holders',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload=payload,
                    meta={'yahoo_symbol': ysym}
                )
            except Exception as e:
                yield FinanceData(
                    source='yahoo',
                    data_type='holders',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'yahoo_symbol': ysym}
                )
    
    async def _fetch_recommendations(self, yahoo_symbols: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取分析师评级/推荐"""
        for ysym, sym in zip(yahoo_symbols, original_symbols):
            try:
                ticker = yf.Ticker(ysym, session=self._session)
                
                recommendations = ticker.recommendations
                upgrades_downgrades = ticker.upgrades_downgrades
                
                payload = {
                    'recommendations': recommendations.to_dict('records') if recommendations is not None and not recommendations.empty else [],
                    'upgrades_downgrades': upgrades_downgrades.to_dict('records') if upgrades_downgrades is not None and not upgrades_downgrades.empty else [],
                }
                
                yield FinanceData(
                    source='yahoo',
                    data_type='recommendations',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload=payload,
                    meta={'yahoo_symbol': ysym}
                )
            except Exception as e:
                yield FinanceData(
                    source='yahoo',
                    data_type='recommendations',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'yahoo_symbol': ysym}
                )
    
    async def _fetch_calendar(self, yahoo_symbols: List[str], original_symbols: List[str]) -> AsyncIterator[FinanceData]:
        """获取财务日历 (财报发布日期、分红日期等)"""
        for ysym, sym in zip(yahoo_symbols, original_symbols):
            try:
                ticker = yf.Ticker(ysym, session=self._session)
                
                calendar = ticker.calendar
                earnings_dates = ticker.earnings_dates
                
                payload = {
                    'calendar': calendar.to_dict() if calendar is not None and not calendar.empty else {},
                    'earnings_dates': earnings_dates.to_dict('records') if earnings_dates is not None and not earnings_dates.empty else [],
                }
                
                yield FinanceData(
                    source='yahoo',
                    data_type='calendar',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload=payload,
                    meta={'yahoo_symbol': ysym}
                )
            except Exception as e:
                yield FinanceData(
                    source='yahoo',
                    data_type='calendar',
                    symbol=sym,
                    timestamp=datetime.utcnow(),
                    payload={'error': str(e)},
                    meta={'yahoo_symbol': ysym}
                )
    
    async def close(self):
        """关闭连接/释放资源"""
        pass


# 便捷函数
async def create_scraper(proxy: str = None) -> YahooScraper:
    """创建 Yahoo Finance 抓取器实例"""
    return YahooScraper(proxy=proxy)