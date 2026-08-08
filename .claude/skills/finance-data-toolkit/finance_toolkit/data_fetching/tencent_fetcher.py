# -*- coding: utf-8 -*-
"""
腾讯财经数据抓取模块
提供实时行情和历史K线数据
"""

import re
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from ..core import FinanceData
from ..resilience import retry_with_backoff
from ..validation import validate_quote_data

logger = logging.getLogger(__name__)


def _tencent_code_to_url(code: str) -> str:
    """将股票代码转换为腾讯财经URL格式"""
    code = code.strip().split('.')[0]
    if code.startswith(('60', '68', '90')):
        return f'sh{code}'
    else:
        return f'sz{code}'


def _parse_tencent_quote(text: str) -> Optional[Dict]:
    """解析腾讯财经返回的行情数据"""
    # 格式: v_sh600000="1~浦发银行~600000~10.50~10.45~10.48~10.52~1000000~500000~500000~10.49~1000~10.50~2000~10.52~10.55~10.48~10.50~100000000~20240101150000~-0.50~-4.55~0.50~12.5~105000000000~95000000000~..."
    if '~' not in text:
        return None
    
    fields = text.split('~')
    if len(fields) < 32:
        return None
    
    try:
        return {
            'name': fields[1],
            'code': fields[2],
            'price': float(fields[3]) if fields[3] else 0.0,
            'pre_close': float(fields[4]) if fields[4] else 0.0,
            'open': float(fields[5]) if fields[5] else 0.0,
            'volume': int(float(fields[6])) if fields[6] else 0,
            'buy_price': float(fields[7]) if fields[7] else 0.0,
            'sell_price': float(fields[8]) if fields[8] else 0.0,
            'high': float(fields[14]) if len(fields) > 14 and fields[14] else 0.0,
            'low': float(fields[15]) if len(fields) > 15 and fields[15] else 0.0,
            'price_1': float(fields[16]) if len(fields) > 16 and fields[16] else 0.0,
            'amount': float(fields[18]) if len(fields) > 18 and fields[18] else 0.0,
            'date': fields[20] if len(fields) > 20 else '',
            'time': fields[21] if len(fields) > 21 else '',
            'change_pct': float(fields[31]) if len(fields) > 31 and fields[31] else 0.0,
            'change_amt': float(fields[32]) if len(fields) > 32 and fields[32] else 0.0,
            'turnover': float(fields[33]) if len(fields) > 33 and fields[33] else 0.0,
            'pe': float(fields[39]) if len(fields) > 39 and fields[39] else 0.0,
            'total_mv': float(fields[44]) if len(fields) > 44 and fields[44] else 0.0,
            'circ_mv': float(fields[45]) if len(fields) > 45 and fields[45] else 0.0,
        }
    except (ValueError, IndexError):
        return None


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_tencent_quotes(codes: List[str]) -> str:
    """获取腾讯财经实时行情（带重试）"""
    if not HAS_HTTPX:
        raise ImportError("httpx未安装")
    
    url_codes = [_tencent_code_to_url(c) for c in codes]
    url = f"https://qt.gtimg.cn/q={','.join(url_codes)}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.qq.com/'
    }
    
    with httpx.Client(timeout=10) as client:
        resp = client.get(url, headers=headers)
        resp.encoding = 'gbk'
        return resp.text


def fetch_tencent_quote(symbols: List[str], source: str = 'tencent') -> List[FinanceData]:
    """获取腾讯财经实时行情
    
    Args:
        symbols: 股票代码列表，如 ['600000.SH', '000001.SZ']
        source: 数据源标识
    """
    results = []
    
    if not HAS_HTTPX:
        logger.warning("httpx未安装，无法获取腾讯财经数据")
        return results
    
    try:
        text = _fetch_tencent_quotes(symbols)
        
        for line in text.strip().split(';\n'):
            if not line.startswith('v_'):
                continue
            
            # 提取代码
            match = re.search(r'v_(\w+)="', line)
            if not match:
                continue
            
            url_code = match.group(1)
            # 提取数据部分
            data_match = re.search(r'="(.+)"', line)
            if not data_match:
                continue
            
            data_str = data_match.group(1)
            parsed = _parse_tencent_quote(data_str)
            
            if parsed:
                # 转换回标准格式
                std_code = url_code[2:]  # 去掉 sh/sz 前缀
                std_sym = f'{std_code}.SH' if std_code.startswith(('60', '68', '90')) else f'{std_code}.SZ'
                
                # 验证数据质量
                report = validate_quote_data(parsed, std_sym)
                if not report.is_valid:
                    logger.warning(f"腾讯财经数据质量验证失败 [{std_sym}]: {report.issues}")
                
                results.append(FinanceData(
                    source=source,
                    data_type='quote',
                    symbol=std_sym,
                    timestamp=datetime.utcnow().isoformat(),
                    payload=parsed,
                    meta={'quality_report': report.to_dict() if report else None}
                ))
    
    except Exception as e:
        logger.error(f"腾讯财经行情获取失败: {e}")
    
    return results


def fetch_tencent_kline(symbol: str, period: str = 'day', start: str = '', end: str = '') -> List[Dict]:
    """获取腾讯财经历史K线数据
    
    Args:
        symbol: 股票代码，如 '600000.SH'
        period: K线周期 (day/week/month)
        start: 开始日期，格式 YYYYMMDD
        end: 结束日期，格式 YYYYMMDD
    """
    if not HAS_HTTPX:
        return []
    
    url_code = _tencent_code_to_url(symbol)
    param = f"{url_code},{period},{start},{end}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_daydata&param={param}"
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.qq.com/'
        }
        
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=headers)
            resp.encoding = 'utf-8'
            text = resp.text
        
        # 提取JSON数据
        match = re.search(r'"days":\[(.*?)\]', text, re.DOTALL)
        if not match:
            return []
        
        import json
        days_data = json.loads('[' + match.group(1) + ']')
        
        # 日期过滤
        result = []
        for row in days_data:
            day = row.get('day', '')
            if start and day < start:
                continue
            if end and day > end:
                continue
            
            result.append({
                'date': day,
                'open': float(row.get('open', 0)),
                'close': float(row.get('close', 0)),
                'high': float(row.get('high', 0)),
                'low': float(row.get('low', 0)),
                'volume': int(row.get('volume', 0)),
                'amount': float(row.get('amount', 0)),
            })
        
        return result
    
    except Exception as e:
        logger.error(f"腾讯财经K线获取失败: {e}")
        return []


# 便捷函数
get_tencent_quote = fetch_tencent_quote
get_tencent_kline = fetch_tencent_kline
