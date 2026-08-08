# -*- coding: utf-8 -*-
"""
数据获取器实现
封装各数据源的具体调用逻辑
"""

import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

# 导入统一的重试机制
from ..resilience import retry_with_backoff

# 尝试导入可选依赖
try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

try:
    import tushare as ts  # noqa: F401
    HAS_TUSHARE = True
except ImportError:
    HAS_TUSHARE = False

try:
    import httpx  # noqa: F401
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

logger = logging.getLogger(__name__)

# 导入数据验证模块
from ..validation import validate_quote_data, validate_kline_data, QualityReport

# 导入新增数据源
from .tencent_fetcher import fetch_tencent_quote, fetch_tencent_kline
from .netease_fetcher import fetch_163_quote

# 保留 retry_akshare 作为别名，保持向后兼容
retry_akshare = retry_with_backoff

# HTTP 客户端连接池配置
if HAS_HTTPX:
    # 模块级共享客户端，带连接池
    _http_client = httpx.Client(
        timeout=httpx.Timeout(10.0, connect=5.0),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=30.0
        ),
        trust_env=False
    )
    
    def get_http_client():
        """获取共享 HTTP 客户端"""
        return _http_client
    
    def close_http_client():
        """关闭共享 HTTP 客户端"""
        global _http_client
        if _http_client and not _http_client.is_closed:
            _http_client.close()
            _http_client = None
else:
    _http_client = None
    
    def get_http_client():
        """获取共享 HTTP 客户端"""
        return None
    
    def close_http_client():
        """关闭共享 HTTP 客户端"""
        pass


@dataclass
class FinanceData:
    """统一金融数据契约"""
    source: str
    data_type: str
    symbol: str
    timestamp: str
    payload: Dict[str, Any]
    raw: Optional[Dict] = None
    meta: Optional[Dict] = None

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def to_standard_symbol(code: str) -> str:
    """转换为标准格式: 600000 -> 600000.SH"""
    code = code.strip()
    if '.' in code:
        return code.upper()
    if code.startswith(('60', '68', '90')):
        return f'{code}.SH'
    else:
        return f'{code}.SZ'


def to_akshare_symbol(code: str) -> str:
    """转换为AKShare格式: 600000.SH -> 600000"""
    return code.split('.')[0]


def to_sina_symbol(code: str) -> str:
    """转换为新浪格式: 600000.SH -> sh600000"""
    code = code.split('.')[0]
    if code.startswith(('60', '68', '90')):
        return f'sh{code}'
    else:
        return f'sz{code}'


def to_eastmoney_symbol(code: str) -> str:
    """转换为东方财富格式: 600000.SH -> sh600000"""
    return to_sina_symbol(code)


# ============== 实时行情 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_realtime():
    """内部函数：获取 AKShare 全市场实时行情（带重试）"""
    return ak.stock_zh_a_spot_em()

def fetch_realtime_quote(symbols: List[str], source: str = 'akshare') -> List[FinanceData]:
    """获取实时行情
    
    Args:
        symbols: 股票代码列表，支持 600000.SH / 000001.SZ 格式
        source: 数据源 (akshare/eastmoney/sina/tencent/netease)
    """
    results = []
    
    if source == 'tencent' and HAS_HTTPX:
        # 提取纯数字代码
        codes = [s.split('.')[0] for s in symbols]
        results.extend(fetch_tencent_quote(codes))
    
    elif source == 'netease' and HAS_HTTPX:
        # 网易财经使用纯数字代码
        codes = [s.split('.')[0] for s in symbols]
        results.extend(fetch_163_quote(codes))
    
    elif source == 'akshare' and HAS_AKSHARE:
        # AKShare 获取全市场行情再筛选
        try:
            df = _fetch_akshare_realtime()
            if df is None:
                logger.warning("AKShare 返回空数据")
            else:
                df['symbol'] = df['代码'].apply(lambda x: f'{x}.SH' if x.startswith(('60','68','90')) else f'{x}.SZ')
            
            for sym in symbols:
                std_sym = to_standard_symbol(sym)
                row = df[df['symbol'] == std_sym]
                if not row.empty:
                    r = row.iloc[0]
                    payload = {
                        'open': r['今开'],
                        'high': r['最高'],
                        'low': r['最低'],
                        'close': r['最新价'],
                        'pre_close': r['昨收'],
                        'volume': r['成交量'],
                        'amount': r['成交额'],
                        'change_pct': r['涨跌幅'],
                        'change_amt': r['涨跌额'],
                        'turnover': r['换手率'],
                        'pe_ttm': r['市盈率-动态'],
                        'pb': r['市净率'],
                        'total_mv': r['总市值'],
                        'circ_mv': r['流通市值'],
                    }
                    # 验证数据质量
                    report = validate_quote_data(payload, std_sym)
                    if not report.is_valid:
                        logger.warning(f"数据质量验证失败 [{std_sym}]: {report.issues}")
                    
                    results.append(FinanceData(
                        source='akshare',
                        data_type='quote',
                        symbol=std_sym,
                        timestamp=datetime.utcnow().isoformat(),
                        payload=payload,
                        meta={'quality_report': report.to_dict() if report else None}
                    ))
        except Exception as e:
            logger.error(f"AKShare获取失败: {e}")
    
    elif source == 'eastmoney':
        # 东方财富通过 browser-cdp 抓取
        for sym in symbols:
            try:
                data = _fetch_eastmoney_quote(sym)
                if data:
                    results.append(FinanceData(
                        source='eastmoney',
                        data_type='quote',
                        symbol=sym,
                        timestamp=datetime.utcnow().isoformat(),
                        payload=data
                    ))
            except Exception as e:
                logger.error(f"东方财富获取 {sym} 失败：{e}")
    
    elif source == 'sina':
        # 新浪财经实时行情
        client = get_http_client()
        if client:
            for sym in symbols:
                try:
                    code = to_sina_symbol(sym)
                    url = f"https://hq.sinajs.cn/list={code}"
                    headers = {
                        'Referer': 'https://finance.sina.com.cn/',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                    resp = client.get(url, headers=headers, timeout=10)
                    resp.encoding = 'gbk'
                    if resp.status_code == 200:
                        # 解析返回：var hq_str_sh600000="名称，今开，昨收，现价，最高，最低，成交量，成交额，日期，时间，..."
                        # 字段索引：0=名称，1=今开，2=昨收，3=现价，4=最高，5=最低，6=成交量，7=成交额，8=日期，9=时间
                        text = resp.text
                        if '=' in text:
                            data_str = text.split('="')[1].rstrip('"')
                            parts = data_str.split(',')
                            if len(parts) >= 10:
                                try:
                                    payload = {
                                        'name': parts[0],
                                        'open': float(parts[1]) if parts[1] else 0.0,
                                        'pre_close': float(parts[2]) if parts[2] else 0.0,
                                        'price': float(parts[3]) if parts[3] else 0.0,
                                        'high': float(parts[4]) if parts[4] else 0.0,
                                        'low': float(parts[5]) if parts[5] else 0.0,
                                        'volume': int(float(parts[6])) if parts[6] else 0,
                                        'amount': float(parts[7]) if parts[7] else 0.0,
                                    }
                                except (ValueError, IndexError):
                                    logger.warning(f"新浪数据解析失败 {sym}: {parts[:5]}")
                                    continue
                                results.append(FinanceData(
                                    source='sina',
                                    data_type='quote',
                                    symbol=sym,
                                    timestamp=datetime.utcnow().isoformat(),
                                    payload=payload
                                ))
                except Exception as e:
                    logger.error(f"新浪获取 {sym} 失败：{e}")
    
    return results


def _fetch_eastmoney_quote(symbol: str) -> Optional[Dict]:
    """通过 browser-cdp 抓取东方财富实时行情"""
    try:
        from .eastmoney_fetcher import fetch_stock_data
        finance_data = fetch_stock_data(symbol, headless=True)
        return finance_data.to_dict()
    except Exception as e:
        logger.error(f"东方财富抓取异常: {e}")
        return None


# ============== K线数据 ==============

@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_kline(code: str, period: str, start: str, end: str, adjust: str):
    """内部函数：获取 AKShare K线数据（带重试）"""
    return ak.stock_zh_a_hist(
        symbol=code,
        period=period,
        start_date=start,
        end_date=end,
        adjust=adjust
    )

def fetch_kline(
    symbol: str,
    period: str = 'daily',
    start: str = '20240101',
    end: str = None,
    adjust: str = 'qfq',
    source: str = 'akshare'
) -> List[Dict]:
    """获取历史K线数据
    
    Args:
        symbol: 股票代码 (600000.SH)
        period: daily/weekly/monthly/1m/5m/15m/30m/60m
        start: 开始日期 (YYYYMMDD)
        end: 结束日期 (YYYYMMDD)
        adjust: qfq/hfq/不复权
        source: akshare/sina
    """
    if end is None:
        end = datetime.now().strftime('%Y%m%d')
    
    code = to_akshare_symbol(symbol)
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            df = _fetch_akshare_kline(code, period, start, end, adjust)
            df = df.rename(columns={
                '日期': 'date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'volume',
                '成交额': 'amount', '振幅': 'amplitude',
                '涨跌幅': 'change_pct', '涨跌额': 'change_amt',
                '换手率': 'turnover'
            })
            # 验证 K 线数据质量
            records = df.to_dict('records')
            if records:
                report = validate_kline_data(records, symbol)
                if not report.is_valid:
                    logger.warning(f"K线数据质量验证失败 [{symbol}]: {report.issues}")
            return records
        except Exception as e:
            logger.error(f"AKShare K线获取失败: {e}")
    
    elif source == 'sina':
        return _fetch_sina_kline(symbol, period, start, end)
    
    return []


def _fetch_sina_kline(symbol: str, period: str = 'daily', start: str = '20240101', end: str = None) -> List[Dict]:
    """新浪财经 K线 API"""
    # urllib is standard lib, skip HAS_HTTPX check
    # if not HAS_HTTPX:
    #     return []
    
    if end is None:
        end = datetime.now().strftime('%Y%m%d')
    
    sina_symbol = to_sina_symbol(symbol)
    # 映射 period 到新浪 scale 参数
    scale_map = {'1m': '5', '5m': '5', '15m': '15', '30m': '30', '60m': '60', 'daily': '240', 'weekly': '1200', 'monthly': '7200'}
    scale = scale_map.get(period, '240')
    datalen = 1023
    
    url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData?symbol={sina_symbol}&scale={scale}&ma=no&datalen={datalen}"
    
    try:
        import urllib.request
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.sina.com.cn/',
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode('utf-8', errors='ignore')
        
        idx = text.find('var=(')
        if idx < 0:
            return []
        end_idx = text.rfind(');')
        json_str = text[idx + 5:end_idx]
        data = json.loads(json_str)
        
        if not data:
            return []
        
        # 日期范围过滤
        result = []
        for row in data:
            row_date = row['day'][:10]  # 格式："2024-01-01 00:00:00" -> "2024-01-01"
            row_date_fmt = row_date.replace('-', '')  # 转为 "20240101"
            
            if row_date_fmt < start or row_date_fmt > end:
                continue
                
            result.append({
                'date': row_date,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': int(row['volume']),
            })
        return result
    except Exception as e:
        logger.error(f"新浪K线获取失败: {e}")
        return []


# ============== 财务报表 ==============

@retry_akshare(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_financial_report(code: str, report_type: str):
    """内部函数：获取 AKShare 财务报表（带重试）"""
    return ak.stock_financial_report_sina(stock=code, symbol=report_type)

def fetch_financial(symbol: str, source: str = 'akshare') -> List[FinanceData]:
    """获取财务报表 (资产负债表、利润表、现金流量表)"""
    results = []
    code = to_akshare_symbol(symbol)
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            # 资产负债表
            df_bs = _fetch_akshare_financial_report(code, '资产负债表')
            # 利润表
            df_is = _fetch_akshare_financial_report(code, '利润表')
            # 现金流量表
            df_cf = _fetch_akshare_financial_report(code, '现金流量表')
            
            for df, report_type in [(df_bs, 'balance_sheet'), (df_is, 'income_statement'), (df_cf, 'cash_flow')]:
                if not df.empty:
                    for _, row in df.iterrows():
                        payload = row.to_dict()
                        payload['report_type'] = report_type
                        results.append(FinanceData(
                            source='akshare',
                            data_type='financial',
                            symbol=symbol,
                            timestamp=datetime.utcnow().isoformat(),
                            payload=payload
                        ))
        except Exception as e:
            logger.error(f"财务报表获取失败: {e}")
    
    return results


@retry_akshare(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_dividend(code: str):
    """内部函数：获取 AKShare 分红数据（带重试）"""
    return ak.stock_fhps_detail_em(symbol=code)

def fetch_dividend(symbol: str, source: str = 'akshare') -> List[FinanceData]:
    """获取分红配股数据"""
    results = []
    code = to_akshare_symbol(symbol)
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            # 使用 stock_fhps_detail_em 获取单只股票的分红详情
            df = _fetch_akshare_dividend(code)
            for _, row in df.iterrows():
                results.append(FinanceData(
                    source='akshare',
                    data_type='dividend',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload=row.to_dict()
                ))
        except Exception as e:
            logger.error(f"分红数据获取失败: {e}")
    
    return results


@retry_akshare(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_lhb(start_date: str, end_date: str):
    """内部函数：获取 AKShare 龙虎榜数据（带重试）"""
    return ak.stock_lhb_detail_em(start_date=start_date, end_date=end_date)

def fetch_lhb(symbol: str = None, start_date: str = None, end_date: str = None, source: str = 'akshare') -> List[FinanceData]:
    """获取龙虎榜数据
    
    Args:
        symbol: 股票代码（可选，如果提供则获取该股的龙虎榜）
        start_date: 开始日期，格式 YYYYMMDD
        end_date: 结束日期，格式 YYYYMMDD
        source: 数据源
    """
    results = []
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            # 如果没有提供日期，使用最近 30 天
            if not end_date:
                end_date = datetime.now().strftime('%Y%m%d')
            if not start_date:
                start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
            
            df = _fetch_akshare_lhb(start_date, end_date)
            
            # 如果指定了股票代码，过滤数据
            if symbol:
                code = to_akshare_symbol(symbol)
                # 尝试匹配股票代码（不同 API 返回的列名可能不同）
                code_col = None
                for col in ['股票代码', '代码', 'symbol', 'stock_code']:
                    if col in df.columns:
                        code_col = col
                        break
                
                if code_col:
                    df = df[df[code_col] == code]
            
            for _, row in df.iterrows():
                results.append(FinanceData(
                    source='akshare',
                    data_type='lhb',
                    symbol=symbol or 'ALL',
                    timestamp=datetime.utcnow().isoformat(),
                    payload=row.to_dict()
                ))
        except Exception as e:
            logger.error(f"龙虎榜获取失败：{e}")
    
    return results


@retry_akshare(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_northbound():
    """内部函数：获取 AKShare 北向资金汇总数据（带重试）"""
    return ak.stock_hsgt_fund_flow_summary_em()

@retry_akshare(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_northbound_hist(symbol: str = '沪股通'):
    """内部函数：获取 AKShare 北向资金历史数据（带重试）"""
    return ak.stock_hsgt_hist_em(symbol=symbol)

@retry_akshare(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_northbound_hold(symbol: str):
    """内部函数：获取 AKShare 个股北向持仓数据（带重试）"""
    # 使用 stock_hsgt_individual_em 获取个股北向持仓详情
    return ak.stock_hsgt_individual_em(symbol=symbol)

def fetch_northbound(symbol: str = None, source: str = 'akshare') -> List[FinanceData]:
    """获取北向资金数据
    
    Args:
        symbol: 股票代码 (如 '600519.SH') 或板块名称 ('沪股通', '深股通')
        source: 数据源
    """
    results = []
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            if symbol:
                # 如果提供了股票代码，获取个股北向持仓
                code = to_akshare_symbol(symbol)
                df = _fetch_akshare_northbound_hold(code)
                for _, row in df.iterrows():
                    results.append(FinanceData(
                        source='akshare',
                        data_type='northbound_hold',
                        symbol=symbol,
                        timestamp=datetime.utcnow().isoformat(),
                        payload=row.to_dict()
                    ))
            else:
                # 获取北向资金汇总数据
                df = _fetch_akshare_northbound()
                for _, row in df.iterrows():
                    results.append(FinanceData(
                        source='akshare',
                        data_type='northbound',
                        symbol='NORTHBOUND',
                        timestamp=datetime.utcnow().isoformat(),
                        payload=row.to_dict()
                    ))
        except Exception as e:
            logger.error(f"北向资金获取失败：{e}")
    
    return results


@retry_akshare(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_akshare_stock_basic():
    """内部函数：获取 AKShare 股票基础信息（带重试）"""
    return ak.stock_zh_a_spot_em()

def fetch_stock_basic(source: str = 'akshare') -> List[FinanceData]:
    """获取股票基础信息 (代码、名称、上市日期、行业等)"""
    results = []
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            # stock_info_a_code_name 依赖 openpyxl，换用不依赖的接口
            # 使用 stock_zh_a_spot_em 获取基础信息（包含代码和名称）
            df = _fetch_akshare_stock_basic()
            for _, row in df.iterrows():
                code = row['代码']
                std_sym = f'{code}.SH' if code.startswith(('60','68','90')) else f'{code}.SZ'
                results.append(FinanceData(
                    source='akshare',
                    data_type='stock_basic',
                    symbol=std_sym,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={
                        'code': code,
                        'name': row['名称'],
                        'exchange': 'SH' if code.startswith(('60','68','90')) else 'SZ',
                    }
                ))
        except Exception as e:
            logger.error(f"股票基础信息获取失败: {e}")
    
    return results


# ============== 统一异步接口 ==============

class DataFetcher:
    """统一数据获取器 - 支持同步和异步调用"""
    
    def __init__(self, default_source: str = 'akshare'):
        self.default_source = default_source
    
    def get_quote(self, symbols: List[str], source: str = None) -> List[FinanceData]:
        return fetch_realtime_quote(symbols, source or self.default_source)
    
    def get_kline(self, symbol: str, **kwargs) -> List[Dict]:
        return fetch_kline(symbol, **kwargs)
    
    def get_financial(self, symbol: str, source: str = None) -> List[FinanceData]:
        return fetch_financial(symbol, source or self.default_source)
    
    def get_dividend(self, symbol: str, source: str = None) -> List[FinanceData]:
        return fetch_dividend(symbol, source or self.default_source)
    
    def get_lhb(self, symbol: str, source: str = None) -> List[FinanceData]:
        return fetch_lhb(symbol, source or self.default_source)
    
    def get_northbound(self, source: str = None) -> List[FinanceData]:
        return fetch_northbound(source or self.default_source)
    
    def get_stock_basic(self, source: str = None) -> List[FinanceData]:
        return fetch_stock_basic(source or self.default_source)

    def get_fund(self, symbol: str, data_type: str = 'nav', source: str = None) -> List[FinanceData]:
        return fetch_fund(symbol, data_type, source or self.default_source)

    def get_bond(self, symbol: str, data_type: str = 'yield', source: str = None) -> List[FinanceData]:
        return fetch_bond(symbol, data_type, source or self.default_source)

    def get_futures(self, symbol: str, data_type: str = 'quote', source: str = None) -> List[FinanceData]:
        return fetch_futures(symbol, data_type, source or self.default_source)

    def get_index(self, symbol: str, data_type: str = 'quote', source: str = None) -> List[FinanceData]:
        return fetch_index(symbol, data_type, source or self.default_source)

    def get_macro(self, data_type: str = 'gdp', source: str = None) -> List[FinanceData]:
        return fetch_macro(data_type, source or self.default_source)


# ============== 基金数据 ==============

def fetch_fund(symbol: str, data_type: str = 'nav', source: str = 'fund') -> List[FinanceData]:
    """获取基金数据

    Args:
        symbol: 基金代码 (如 '159915')
        data_type: 数据类型 (nav/holdings/rank/info/history)
        source: 数据源
    """
    results = []

    try:
        from ..scrapers.fund_scraper import FundScraper
        if FundScraper:
            scraper = FundScraper()
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def _fetch():
                    async for data in scraper.fetch([symbol], data_type):
                        results.append(data)
                loop.run_until_complete(_fetch())
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"基金数据获取失败: {e}")

    return results


# ============== 债券数据 ==============

def fetch_bond(symbol: str, data_type: str = 'yield', source: str = 'bond') -> List[FinanceData]:
    """获取债券数据

    Args:
        symbol: 债券代码 (如 '127045')
        data_type: 数据类型 (yield/quote/convertible/info)
        source: 数据源
    """
    results = []

    try:
        from ..scrapers.bond_scraper import BondScraper
        if BondScraper:
            scraper = BondScraper()
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def _fetch():
                    async for data in scraper.fetch([symbol], data_type):
                        results.append(data)
                loop.run_until_complete(_fetch())
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"债券数据获取失败: {e}")

    return results


# ============== 期货数据 ==============

def fetch_futures(symbol: str, data_type: str = 'quote', source: str = 'futures') -> List[FinanceData]:
    """获取期货数据

    Args:
        symbol: 期货代码 (如 'CU2401')
        data_type: 数据类型 (quote/kline/position/info)
        source: 数据源
    """
    results = []

    try:
        from ..scrapers.futures_scraper import FuturesScraper
        if FuturesScraper:
            scraper = FuturesScraper()
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def _fetch():
                    async for data in scraper.fetch([symbol], data_type):
                        results.append(data)
                loop.run_until_complete(_fetch())
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"期货数据获取失败: {e}")

    return results


# ============== 指数数据 ==============

def fetch_index(symbol: str, data_type: str = 'quote', source: str = 'index') -> List[FinanceData]:
    """获取指数数据

    Args:
        symbol: 指数代码 (如 '000001')
        data_type: 数据类型 (quote/kline/constituents/info)
        source: 数据源
    """
    results = []

    try:
        from ..scrapers.index_scraper import IndexScraper
        if IndexScraper:
            scraper = IndexScraper()
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def _fetch():
                    async for data in scraper.fetch([symbol], data_type):
                        results.append(data)
                loop.run_until_complete(_fetch())
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"指数数据获取失败: {e}")

    return results


# ============== 宏观经济数据 ==============

def fetch_macro(data_type: str = 'gdp', source: str = 'macro') -> List[FinanceData]:
    """获取宏观经济数据

    Args:
        data_type: 数据类型 (gdp/cpi/pmi/interest_rate/exchange_rate/money_supply)
        source: 数据源
    """
    results = []

    try:
        from ..scrapers.macro_scraper import MacroScraper
        if MacroScraper:
            scraper = MacroScraper()
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def _fetch():
                    async for data in scraper.fetch([], data_type):
                        results.append(data)
                loop.run_until_complete(_fetch())
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"宏观经济数据获取失败: {e}")

    return results


# 便捷实例
default_fetcher = DataFetcher()


if __name__ == '__main__':
    # 测试
    logger.info("测试实时行情...")
    quotes = fetch_realtime_quote(['600000.SH', '000001.SZ'])
    for q in quotes:
        logger.info(f"{q.symbol}: {q.payload.get('close')} ({q.payload.get('change_pct')}%)")
    
    logger.info("\n测试K线...")
    klines = fetch_kline('600000.SH', period='daily', start='20240101')
    logger.info(f"获取 {len(klines)} 条K线")
    if klines:
        logger.info(f"最新: {klines[-1]}")