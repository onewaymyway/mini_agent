# -*- coding: utf-8 -*-
"""
数据获取器实现
封装各数据源的具体调用逻辑
"""

import sys
import os
import json
import time
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

# 尝试导入可选依赖
try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

try:
    import tushare as ts
    HAS_TUSHARE = True
except ImportError:
    HAS_TUSHARE = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


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

def fetch_realtime_quote(symbols: List[str], source: str = 'akshare') -> List[FinanceData]:
    """获取实时行情
    
    Args:
        symbols: 股票代码列表，支持 600000.SH / 000001.SZ 格式
        source: 数据源 (akshare/eastmoney/sina)
    """
    results = []
    
    if source == 'akshare' and HAS_AKSHARE:
        # AKShare 获取全市场行情再筛选
        try:
            df = ak.stock_zh_a_spot_em()
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
                    results.append(FinanceData(
                        source='akshare',
                        data_type='quote',
                        symbol=std_sym,
                        timestamp=datetime.utcnow().isoformat(),
                        payload=payload
                    ))
        except Exception as e:
            print(f"AKShare获取失败: {e}", file=sys.stderr)
    
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
                print(f"东方财富获取 {sym} 失败：{e}", file=sys.stderr)
    
    elif source == 'sina':
        # 新浪财经实时行情
        import httpx
        for sym in symbols:
            try:
                code = to_sina_symbol(sym)
                url = f"https://hq.sinajs.cn/list={code}"
                headers = {
                    'Referer': 'https://finance.sina.com.cn/',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                with httpx.Client(trust_env=False) as client:
                    resp = client.get(url, headers=headers, timeout=10)
                    resp.encoding = 'gbk'
                    if resp.status_code == 200:
                        # 解析返回：var hq_str_sh600000="9.20,9.20,9.19,9.20,9.19,9.18,9.17,10000,20000,..."
                        text = resp.text
                        if '=' in text:
                            data_str = text.split('="')[1].rstrip('"')
                            parts = data_str.split(',')
                            if len(parts) >= 32:
                                payload = {
                                    'name': parts[0],
                                    'open': float(parts[1]),
                                    'pre_close': float(parts[2]),
                                    'price': float(parts[3]),
                                    'high': float(parts[4]),
                                    'low': float(parts[5]),
                                    'volume': int(parts[8]),
                                    'amount': float(parts[9]),
                                    'buy1': float(parts[11]) if parts[11] else 0,
                                    'sell1': float(parts[16]) if parts[16] else 0,
                                }
                                results.append(FinanceData(
                                    source='sina',
                                    data_type='quote',
                                    symbol=sym,
                                    timestamp=datetime.utcnow().isoformat(),
                                    payload=payload
                                ))
            except Exception as e:
                print(f"新浪获取 {sym} 失败：{e}", file=sys.stderr)
    
    return results


def _fetch_eastmoney_quote(symbol: str) -> Optional[Dict]:
    """通过 browser-cdp 抓取东方财富实时行情"""
    script_path = Path(__file__).parent.parent.parent.parent / 'browser-cdp' / 'fetch_eastmoney_stock.py'
    if not script_path.exists():
        return None
    
    try:
        code = symbol.split('.')[0]
        result = subprocess.run(
            [sys.executable, str(script_path), code, '--headless'],
            capture_output=True, text=True, encoding='utf-8', timeout=120
        )
        if result.returncode == 0:
            # 解析输出中的JSON文件路径
            for line in result.stdout.split('\n'):
                if '数据已保存至:' in line:
                    json_path = line.split('数据已保存至:')[-1].strip()
                    if Path(json_path).exists():
                        with open(json_path, 'r', encoding='utf-8') as f:
                            return json.load(f)
    except Exception as e:
        print(f"东方财富抓取异常: {e}", file=sys.stderr)
    return None


# ============== K线数据 ==============

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
            df = ak.stock_zh_a_hist(
                symbol=code,
                period=period,
                start_date=start,
                end_date=end,
                adjust=adjust
            )
            df = df.rename(columns={
                '日期': 'date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'volume',
                '成交额': 'amount', '振幅': 'amplitude',
                '涨跌幅': 'change_pct', '涨跌额': 'change_amt',
                '换手率': 'turnover'
            })
            return df.to_dict('records')
        except Exception as e:
            print(f"AKShare K线获取失败: {e}", file=sys.stderr)
    
    elif source == 'sina':
        return _fetch_sina_kline(symbol, period, start, end)
    
    return []


def _fetch_sina_kline(symbol: str, period: str = 'daily', start: str = '20240101', end: str = None) -> List[Dict]:
    """新浪财经 K线 API"""
    if not HAS_HTTPX:
        return []
    
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
        end = text.rfind(');')
        json_str = text[idx + 5:end]
        data = json.loads(json_str)
        
        if not data:
            return []
        
        result = []
        for row in data:
            result.append({
                'date': row['day'],
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': int(row['volume']),
            })
        return result
    except Exception as e:
        print(f"新浪K线获取失败: {e}", file=sys.stderr)
        return []


# ============== 财务报表 ==============

def fetch_financial(symbol: str, source: str = 'akshare') -> List[FinanceData]:
    """获取财务报表 (资产负债表、利润表、现金流量表)"""
    results = []
    code = to_akshare_symbol(symbol)
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            # 资产负债表
            df_bs = ak.stock_financial_report_sina(stock=code, symbol='资产负债表')
            # 利润表
            df_is = ak.stock_financial_report_sina(stock=code, symbol='利润表')
            # 现金流量表
            df_cf = ak.stock_financial_report_sina(stock=code, symbol='现金流量表')
            
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
            print(f"财务报表获取失败: {e}", file=sys.stderr)
    
    return results


def fetch_dividend(symbol: str, source: str = 'akshare') -> List[FinanceData]:
    """获取分红配股数据"""
    results = []
    code = to_akshare_symbol(symbol)
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            df = ak.stock_fhps_em(symbol=code)
            for _, row in df.iterrows():
                results.append(FinanceData(
                    source='akshare',
                    data_type='dividend',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload=row.to_dict()
                ))
        except Exception as e:
            print(f"分红数据获取失败: {e}", file=sys.stderr)
    
    return results


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
            
            df = ak.stock_lhb_detail_em(start_date=start_date, end_date=end_date)
            
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
            print(f"龙虎榜获取失败：{e}", file=sys.stderr)
    
    return results


def fetch_northbound(source: str = 'akshare') -> List[FinanceData]:
    """获取北向资金数据"""
    results = []
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            # 使用正确的 API 函数名
            df = ak.stock_hsgt_fund_flow_summary_em()
            for _, row in df.iterrows():
                results.append(FinanceData(
                    source='akshare',
                    data_type='northbound',
                    symbol='NORTHBOUND',
                    timestamp=datetime.utcnow().isoformat(),
                    payload=row.to_dict()
                ))
        except Exception as e:
            print(f"北向资金获取失败：{e}", file=sys.stderr)
    
    return results


def fetch_stock_basic(source: str = 'akshare') -> List[FinanceData]:
    """获取股票基础信息 (代码、名称、上市日期、行业等)"""
    results = []
    
    if source == 'akshare' and HAS_AKSHARE:
        try:
            df = ak.stock_info_a_code_name()
            for _, row in df.iterrows():
                code = row['code']
                std_sym = f'{code}.SH' if code.startswith(('60','68','90')) else f'{code}.SZ'
                results.append(FinanceData(
                    source='akshare',
                    data_type='stock_basic',
                    symbol=std_sym,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={
                        'code': code,
                        'name': row['name'],
                        'exchange': 'SH' if code.startswith(('60','68','90')) else 'SZ',
                    }
                ))
        except Exception as e:
            print(f"股票基础信息获取失败: {e}", file=sys.stderr)
    
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


# 便捷实例
default_fetcher = DataFetcher()


if __name__ == '__main__':
    # 测试
    print("测试实时行情...")
    quotes = fetch_realtime_quote(['600000.SH', '000001.SZ'])
    for q in quotes:
        print(f"{q.symbol}: {q.payload.get('close')} ({q.payload.get('change_pct')}%)")
    
    print("\n测试K线...")
    klines = fetch_kline('600000.SH', period='daily', start='20240101')
    print(f"获取 {len(klines)} 条K线")
    if klines:
        print(f"最新: {klines[-1]}")