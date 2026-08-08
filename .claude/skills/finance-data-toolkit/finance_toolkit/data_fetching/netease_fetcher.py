# -*- coding: utf-8 -*-
"""
网易财经数据抓取模块
提供实时行情数据
"""

import re
import json
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


def _parse_163_jsonp(text: str) -> Optional[Dict]:
    """解析网易财经JSONP格式数据"""
    # 格式: jsonpgc({"000001": {...}, "600000": {...}})
    match = re.search(r'jsonpgc\((.*)\)', text, re.DOTALL)
    if not match:
        return None
    
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


@retry_with_backoff(max_retries=3, backoff_factors=[1, 2, 5])
def _fetch_163_quotes(codes: List[str]) -> str:
    """获取网易财经实时行情（带重试）"""
    if not HAS_HTTPX:
        raise ImportError("httpx未安装")
    
    url = f"https://api.money.126.net/data/feed/{','.join(codes)}.money"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://money.163.com/'
    }
    
    with httpx.Client(timeout=10) as client:
        resp = client.get(url, headers=headers)
        resp.encoding = 'utf-8'
        return resp.text


def fetch_163_quote(symbols: List[str], source: str = 'netease') -> List[FinanceData]:
    """获取网易财经实时行情
    
    Args:
        symbols: 股票代码列表，纯数字格式，如 ['000001', '600000']
        source: 数据源标识
    """
    results = []
    
    if not HAS_HTTPX:
        logger.warning("httpx未安装，无法获取网易财经数据")
        return results
    
    try:
        text = _fetch_163_quotes(symbols)
        data = _parse_163_jsonp(text)
        
        if not data:
            logger.warning("网易财经返回数据解析失败")
            return results
        
        for code, info in data.items():
            if not isinstance(info, dict):
                continue
            
            # 转换回标准格式
            std_sym = f'{code}.SH' if code.startswith(('60', '68', '90')) else f'{code}.SZ'
            
            # 构建payload
            payload = {
                'name': info.get('name', ''),
                'code': code,
                'price': float(info.get('price', 0)) if info.get('price') else 0.0,
                'pre_close': float(info.get('last_close', 0)) if info.get('last_close') else 0.0,
                'open': float(info.get('open', 0)) if info.get('open') else 0.0,
                'high': float(info.get('high', 0)) if info.get('high') else 0.0,
                'low': float(info.get('low', 0)) if info.get('low') else 0.0,
                'volume': int(float(info.get('volume', 0))) if info.get('volume') else 0,
                'amount': float(info.get('amount', 0)) if info.get('amount') else 0.0,
                'change': float(info.get('change', 0)) if info.get('change') else 0.0,
                'change_pct': float(info.get('ratio', 0)) if info.get('ratio') else 0.0,
            }
            
            # 验证数据质量
            report = validate_quote_data(payload, std_sym)
            if not report.is_valid:
                logger.warning(f"网易财经数据质量验证失败 [{std_sym}]: {report.issues}")
            
            results.append(FinanceData(
                source=source,
                data_type='quote',
                symbol=std_sym,
                timestamp=datetime.utcnow().isoformat(),
                payload=payload,
                meta={'quality_report': report.to_dict() if report else None}
            ))
    
    except Exception as e:
        logger.error(f"网易财经行情获取失败: {e}")
    
    return results


# 便捷函数
get_163_quote = fetch_163_quote
