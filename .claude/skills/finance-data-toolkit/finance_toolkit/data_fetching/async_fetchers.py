# -*- coding: utf-8 -*-
"""
异步数据获取器

使用 httpx.AsyncClient 实现非阻塞异步抓取，支持高并发场景。

使用示例：
    from finance_toolkit.data_fetching.async_fetchers import (
        async_fetch_realtime_quote,
        async_fetch_kline,
        async_fetch_multiple_stocks,
    )
    
    # 单个异步调用
    data = await async_fetch_realtime_quote(['600000.SH'], source='akshare')
    
    # 批量并发
    tasks = [async_fetch_realtime_quote([sym]) for sym in ['600000.SH', '000001.SZ', '600519.SH']]
    results = await asyncio.gather(*tasks)
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# 尝试导入依赖
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    logger.warning("httpx 未安装，异步功能不可用。请运行：pip install httpx")

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False


@dataclass
class AsyncFinanceData:
    """异步版统一金融数据契约"""
    source: str
    data_type: str
    symbol: str
    timestamp: str
    payload: Dict[str, Any]
    raw: Optional[Dict] = None
    meta: Optional[Dict] = None
    
    def to_dict(self) -> dict:
        return asdict(self)


# ============== 工具函数 ==============

def to_standard_symbol(code: str) -> str:
    """转换为标准格式：600000 -> 600000.SH"""
    code = code.strip()
    if '.' in code:
        return code.upper()
    if code.startswith(('60', '68', '90')):
        return f'{code}.SH'
    else:
        return f'{code}.SZ'


def to_sina_symbol(code: str) -> str:
    """转换为新浪格式：600000.SH -> sh600000"""
    code = code.split('.')[0]
    if code.startswith(('60', '68', '90')):
        return f'sh{code}'
    else:
        return f'sz{code}'


# ============== 异步 HTTP 客户端 ==============

class AsyncHTTPClient:
    """异步 HTTP 客户端封装"""
    
    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        retry_backoff: List[float] = None,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff or [1, 2, 5]
        self._client: Optional[httpx.AsyncClient] = None
    
    async def get(self, url: str, params: Dict = None, headers: Dict = None) -> Dict:
        """异步 GET 请求，带重试"""
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装")
        
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                response = await self._client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
                
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"请求超时 (尝试 {attempt + 1}/{self.max_retries}): {url}")
                
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"HTTP 错误 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                
            except Exception as e:
                last_error = e
                logger.warning(f"请求失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
            
            if attempt < self.max_retries - 1:
                wait_time = self.retry_backoff[min(attempt, len(self.retry_backoff) - 1)]
                await asyncio.sleep(wait_time)
        
        raise last_error
    
    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()


# ============== 异步实时行情 ==============

async def async_fetch_realtime_quote(
    symbols: List[str],
    source: str = 'akshare',
    use_threading: bool = True
) -> List[AsyncFinanceData]:
    """
    异步获取实时行情
    
    Args:
        symbols: 股票代码列表
        source: 数据源 (akshare/eastmoney/sina)
        use_threading: 是否使用线程池运行同步的 akshare 调用
    
    Returns:
        实时行情列表
    """
    symbols = [to_standard_symbol(s) for s in symbols]
    
    if source == 'akshare' and HAS_AKSHARE:
        # AKShare 目前没有原生异步 API，使用线程池包装
        loop = asyncio.get_event_loop()
        
        def _fetch_akshare():
            try:
                df = ak.stock_zh_a_spot_em()
                results = []
                for _, row in df[df['代码'].apply(lambda x: f"{x}.SZ" if x.startswith(('0','3')) else f"{x}.SH").isin(symbols)].iterrows():
                    results.append(AsyncFinanceData(
                        source='akshare',
                        data_type='quote',
                        symbol=row['代码'] + ('.SZ' if row['代码'].startswith(('0','3')) else '.SH'),
                        timestamp=datetime.utcnow().isoformat(),
                        payload={
                            'open': float(row['今开']),
                            'high': float(row['最高']),
                            'low': float(row['最低']),
                            'close': float(row['最新价']),
                            'pre_close': float(row['昨收']),
                            'volume': int(row['成交量']),
                            'amount': float(row['成交额']),
                            'change_pct': float(row['涨跌幅']),
                            'change_amt': float(row['涨跌额']),
                            'turnover': float(row['换手率']),
                            'pe_ttm': float(row['市盈率 - 动态']) if row['市盈率 - 动态'] else None,
                            'pb': float(row['市净率']) if row['市净率'] else None,
                            'total_mv': float(row['总市值']),
                            'circ_mv': float(row['流通市值']),
                        },
                        meta={'fetched_at': datetime.utcnow().isoformat()}
                    ))
                return results
            except Exception as e:
                logger.error(f"AKShare 获取失败：{e}")
                return []
        
        if use_threading:
            # 在线程池运行阻塞的 akshare 调用
            result = await loop.run_in_executor(None, _fetch_akshare)
            return result
        else:
            return _fetch_akshare()
    
    elif source == 'sina':
        return await _async_fetch_sina_quote(symbols)
    
    elif source == 'eastmoney':
        return await _async_fetch_eastmoney_quote(symbols)
    
    else:
        raise ValueError(f"不支持的数据源：{source}")


async def _async_fetch_sina_quote(symbols: List[str]) -> List[AsyncFinanceData]:
    """异步获取新浪实时行情"""
    if not HAS_HTTPX:
        raise ImportError("httpx 未安装")
    
    results = []
    
    # 新浪 API 支持批量查询
    symbol_list = [to_sina_symbol(s) for s in symbols]
    url = "https://hq.sinajs.cn/list=" + ",".join(symbol_list)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn',
    }
    
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            # 解析响应
            lines = response.text.strip().split('\n')
            for line in lines:
                if not line.strip():
                    continue
                
                # 格式：var hq_str_sh600000="名称，开盘，昨收，当前价..."
                parts = line.split('="')
                if len(parts) != 2:
                    continue
                
                symbol_code = parts[0].replace('var hq_str_', '')
                values = parts[1].strip('"').split(',')
                
                if len(values) < 32:
                    continue
                
                symbol = to_standard_symbol(symbol_code)
                
                results.append(AsyncFinanceData(
                    source='sina',
                    data_type='quote',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={
                        'name': values[0],
                        'open': float(values[1]),
                        'pre_close': float(values[2]),
                        'close': float(values[3]),
                        'high': float(values[4]),
                        'low': float(values[5]),
                        'volume': int(values[8]),
                        'amount': float(values[9]),
                        'buy': float(values[11]) if values[11] else 0,
                        'sell': float(values[12]) if values[12] else 0,
                    },
                    meta={'fetched_at': datetime.utcnow().isoformat()}
                ))
                
        except Exception as e:
            logger.error(f"新浪行情获取失败：{e}")
    
    return results


async def _async_fetch_eastmoney_quote(symbols: List[str]) -> List[AsyncFinanceData]:
    """异步获取东方财富实时行情"""
    if not HAS_HTTPX:
        raise ImportError("httpx 未安装")
    
    results = []
    
    # 东方财富 API
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    
    # 构建 secid 列表
    def to_secid(symbol: str) -> str:
        code = symbol.split('.')[0]
        prefix = '1' if symbol.endswith('.SH') else '0'
        return f"{prefix}.{code}"
    
    secids = [to_secid(s) for s in symbols]
    
    params = {
        'secid': ','.join(secids),
        'fields': 'f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f57,f58,f60,f61,f170',
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'fltt': '2',
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.eastmoney.com/',
    }
    
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if 'data' not in data or 'diff' not in data['data']:
                return results
            
            for item in data['data']['diff']:
                if not item or item.get('f62') == 0:  # 停牌或无效数据
                    continue
                
                # 从 secid 还原 symbol
                secid = item.get('f60', '')  # 这个字段包含 secid 信息
                if '.' in secid:
                    prefix, code = secid.split('.')
                    market = 'SH' if prefix == '1' else 'SZ'
                    symbol = f"{code}.{market}"
                else:
                    continue
                
                results.append(AsyncFinanceData(
                    source='eastmoney',
                    data_type='quote',
                    symbol=symbol,
                    timestamp=datetime.utcnow().isoformat(),
                    payload={
                        'close': float(item.get('f43', 0)),
                        'open': float(item.get('f46', 0)),
                        'high': float(item.get('f44', 0)),
                        'low': float(item.get('f45', 0)),
                        'volume': int(item.get('f47', 0)),
                        'amount': float(item.get('f48', 0)),
                        'change_pct': float(item.get('f170', 0)),
                        'turnover': float(item.get('f49', 0)),
                        'pe_ttm': float(item.get('f51', 0)) if item.get('f51') else None,
                        'pb': float(item.get('f52', 0)) if item.get('f52') else None,
                        'total_mv': float(item.get('f60', 0)),
                        'circ_mv': float(item.get('f61', 0)),
                    },
                    meta={'fetched_at': datetime.utcnow().isoformat()}
                ))
                
        except Exception as e:
            logger.error(f"东方财富行情获取失败：{e}")
    
    return results


# ============== 异步 K 线数据 ==============

async def async_fetch_kline(
    symbol: str,
    period: str = 'daily',
    start: str = None,
    end: str = None,
    source: str = 'sina',
    adjust: str = 'qfq'
) -> List[AsyncFinanceData]:
    """
    异步获取 K 线数据
    
    Args:
        symbol: 股票代码
        period: 周期 (daily/weekly/monthly/1m/5m/15m/30m/60m)
        start: 开始日期 YYYYMMDD
        end: 结束日期 YYYYMMDD
        source: 数据源 (sina/eastmoney)
        adjust: 复权类型 (qfq 前复权/hfq 后复权/none 不复权)
    """
    symbol = to_standard_symbol(symbol)
    
    if not start:
        start = (datetime.now() - timedelta(days=365*5)).strftime('%Y%m%d')
    if not end:
        end = datetime.now().strftime('%Y%m%d')
    
    if source == 'sina':
        return await _async_fetch_sina_kline(symbol, period, start, end, adjust)
    elif source == 'eastmoney':
        return await _async_fetch_eastmoney_kline(symbol, period, start, end, adjust)
    else:
        raise ValueError(f"不支持的数据源：{source}")


async def _async_fetch_sina_kline(
    symbol: str,
    period: str,
    start: str,
    end: str,
    adjust: str
) -> List[AsyncFinanceData]:
    """异步获取新浪 K 线"""
    if not HAS_HTTPX:
        raise ImportError("httpx 未安装")
    
    results = []
    symbol_code = to_sina_symbol(symbol)
    
    # 周期映射
    period_map = {
        'daily': 'd',
        'weekly': 'w',
        'monthly': 'm',
        '1m': '1',
        '5m': '5',
        '15m': '15',
        '30m': '30',
        '60m': '60',
    }
    period_code = period_map.get(period, 'd')
    
    # 复权映射
    adjust_map = {
        'qfq': '1',
        'hfq': '2',
        'none': '0',
    }
    adjust_code = adjust_map.get(adjust, '0')
    
    url = f"https://money2.sina.com.cn/api/kline/{symbol_code}"
    params = {
        'period': period_code,
        'adjust': adjust_code,
        'start': start,
        'end': end,
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn',
    }
    
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if 'klines' not in data:
                return results
            
            for kl in data['klines']:
                # 格式：[日期，开盘，最高，最低，收盘，成交量，成交额]
                if len(kl) < 7:
                    continue
                
                results.append(AsyncFinanceData(
                    source='sina',
                    data_type='kline',
                    symbol=symbol,
                    timestamp=kl[0],
                    payload={
                        'date': kl[0],
                        'open': float(kl[1]),
                        'high': float(kl[2]),
                        'low': float(kl[3]),
                        'close': float(kl[4]),
                        'volume': int(kl[5]),
                        'amount': float(kl[6]) if len(kl) > 6 else 0,
                    },
                    meta={'fetched_at': datetime.utcnow().isoformat()}
                ))
                
        except Exception as e:
            logger.error(f"新浪 K 线获取失败 ({symbol}): {e}")
    
    return results


async def _async_fetch_eastmoney_kline(
    symbol: str,
    period: str,
    start: str,
    end: str,
    adjust: str
) -> List[AsyncFinanceData]:
    """异步获取东方财富 K 线"""
    if not HAS_HTTPX:
        raise ImportError("httpx 未安装")
    
    results = []
    
    # 周期映射
    period_map = {
        'daily': '101',
        'weekly': '102',
        'monthly': '103',
        '1m': '1',
        '5m': '5',
        '15m': '15',
        '30m': '30',
        '60m': '60',
    }
    klt = period_map.get(period, '101')
    
    # 复权映射
    adjust_map = {
        'qfq': '1',
        'hfq': '2',
        'none': '0',
    }
    fqt = adjust_map.get(adjust, '1')  # 默认前复权
    
    # 构建 secid
    code = symbol.split('.')[0]
    prefix = '1' if symbol.endswith('.SH') else '0'
    secid = f"{prefix}.{code}"
    
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        'secid': secid,
        'klt': klt,
        'fqt': fqt,
        'beg': start,
        'end': end,
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58',
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.eastmoney.com/',
    }
    
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if 'data' not in data or 'klines' not in data['data']:
                return results
            
            for kl in data['data']['klines']:
                # 格式："2024-01-01,10.5,10.8,10.3,10.7,1000000,10500000.5,2.5"
                parts = kl.split(',')
                if len(parts) < 7:
                    continue
                
                results.append(AsyncFinanceData(
                    source='eastmoney',
                    data_type='kline',
                    symbol=symbol,
                    timestamp=parts[0],
                    payload={
                        'date': parts[0],
                        'open': float(parts[1]),
                        'close': float(parts[2]),
                        'high': float(parts[3]),
                        'low': float(parts[4]),
                        'volume': int(parts[5]),
                        'amount': float(parts[6]),
                        'amplitude': float(parts[7]) if len(parts) > 7 else 0,
                    },
                    meta={'fetched_at': datetime.utcnow().isoformat()}
                ))
                
        except Exception as e:
            logger.error(f"东方财富 K 线获取失败 ({symbol}): {e}")
    
    return results


# ============== 批量并发获取 ==============

async def async_fetch_multiple_stocks(
    symbols: List[str],
    data_type: str = 'quote',
    **kwargs
) -> Dict[str, List[AsyncFinanceData]]:
    """
    批量并发获取多只股票数据
    
    Args:
        symbols: 股票代码列表
        data_type: 数据类型 ('quote' 或 'kline')
        **kwargs: 传递给对应 fetch 函数的参数
    
    Returns:
        {symbol: [data1, data2, ...], ...}
    """
    if data_type == 'quote':
        tasks = [async_fetch_realtime_quote([sym], **kwargs) for sym in symbols]
    elif data_type == 'kline':
        tasks = [async_fetch_kline(sym, **kwargs) for sym in symbols]
    else:
        raise ValueError(f"不支持的数据类型：{data_type}")
    
    # 并发执行
    results_list = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 整理结果
    results = {}
    for sym, result in zip(symbols, results_list):
        if isinstance(result, Exception):
            logger.error(f"获取 {sym} 数据失败：{result}")
            results[sym] = []
        else:
            results[sym] = result
    
    return results


# ============== 便捷函数 ==============

async def fetch_with_fallback(
    symbols: List[str],
    data_type: str = 'quote',
    sources: List[str] = None,
    **kwargs
) -> List[AsyncFinanceData]:
    """
    带降级策略的数据获取
    
    按优先级尝试多个数据源，当前源失败时自动切换到下一个。
    
    Args:
        symbols: 股票代码列表
        data_type: 数据类型
        sources: 数据源优先级列表，默认 ['akshare', 'eastmoney', 'sina']
    """
    if sources is None:
        sources = ['akshare', 'eastmoney', 'sina']
    
    all_results = []
    errors = {}
    
    for source in sources:
        try:
            logger.info(f"尝试数据源：{source}")
            
            if data_type == 'quote':
                results = await async_fetch_realtime_quote(symbols, source=source, **kwargs)
            elif data_type == 'kline':
                results = await async_fetch_kline(symbols[0], source=source, **kwargs) if symbols else []
            else:
                raise ValueError(f"不支持的数据类型：{data_type}")
            
            if results:
                logger.info(f"数据源 {source} 成功获取 {len(results)} 条数据")
                all_results.extend(results)
                return all_results
            else:
                errors[source] = "No data returned"
                
        except Exception as e:
            logger.warning(f"数据源 {source} 失败：{e}")
            errors[source] = str(e)
    
    # 所有源都失败
    if errors:
        logger.error(f"所有数据源均失败：{errors}")
    
    return all_results
