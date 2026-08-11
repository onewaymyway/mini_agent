# -*- coding: utf-8 -*-
"""
JSON 解析工具
提供通用的 JSON/JSONP 解析功能
"""

import json
import re
from typing import Dict, List, Any, Optional


def parse_json_response(text: str) -> Dict:
    """
    解析 JSON 响应
    
    Args:
        text: JSON 字符串
    
    Returns:
        Dict: 解析后的数据
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        return {'error': str(e), 'raw': text[:500]}


def parse_eastmoney_json(text: str) -> Dict:
    """
    解析东方财富 API JSON 响应
    
    东方财富 API 返回格式: {"securo": {...}, "data": {...}}
    """
    data = parse_json_response(text)
    
    # 提取 data 字段
    if 'data' in data:
        return data['data']
    
    return data


def parse_sina_jsonp(text: str) -> List[Dict]:
    """
    解析新浪财经 JSONP 数据
    
    格式: var hq_str_xxx="field1,field2,...";
    """
    results = []
    
    # 匹配 var hq_str_xxx="..."; 格式
    pattern = r'var\s+hq_str_(\w+)\s*=\s*"([^"]*)";'
    matches = re.findall(pattern, text)
    
    for code, data_str in matches:
        fields = data_str.split(',') if data_str else []
        if fields and fields[0]:
            results.append({
                'code': code,
                'fields': fields,
                'raw': data_str,
            })
    
    return results


def parse_jsonp_text(text: str) -> Dict:
    """
    解析通用 JSONP 数据
    
    支持格式:
    - var data = {...};
    - callback({...});
    - ({...});
    """
    patterns = [
        r'var\s+\w+\s*=\s*(\{.*?\});',
        r'\w+\s*\(\s*(\{.*?\})\s*\);',
        r'\(\s*(\{.*?\})\s*\);',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            json_str = match.group(1)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                continue
    
    return {}


def parse_akshare_df(df) -> List[Dict]:
    """
    将 pandas DataFrame 转换为字典列表
    
    Args:
        df: pandas DataFrame
    
    Returns:
        List[Dict]: 数据列表
    """
    try:
        import pandas as pd
        if isinstance(df, pd.DataFrame):
            return df.to_dict(orient='records')
    except ImportError:
        pass
    
    return []


def extract_eastmoney_fields(data: Dict, fields: List[str]) -> Dict:
    """
    从东方财富数据中提取指定字段
    
    Args:
        data: 东方财富 API 返回的数据
        fields: 需要提取的字段名列表
    
    Returns:
        Dict: 提取的字段值
    """
    result = {}
    for field in fields:
        # 尝试直接访问
        if field in data:
            result[field] = data[field]
        # 尝试嵌套访问
        elif 'data' in data and field in data['data']:
            result[field] = data['data'][field]
    return result


def parse_kline_data(klines: List[str]) -> List[Dict]:
    """
    解析 K 线数据
    
    格式: "date,open,close,high,low,volume,amount,amplitude"
    """
    records = []
    for kl in klines:
        parts = kl.split(',')
        if len(parts) >= 7:
            try:
                records.append({
                    'date': parts[0],
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': int(parts[5]),
                    'amount': float(parts[6]),
                    'amplitude': float(parts[7]) if len(parts) > 7 else 0,
                })
            except (ValueError, IndexError):
                continue
    return records


def parse_quote_data(fields: List[str]) -> Dict:
    """
    解析行情数据
    
    新浪格式: name,open,pre_close,price,high,low,volume,amount,...
    """
    if len(fields) < 10:
        return {}
    
    try:
        return {
            'name': fields[0],
            'open': float(fields[1]) if fields[1] else 0,
            'pre_close': float(fields[2]) if fields[2] else 0,
            'price': float(fields[3]) if fields[3] else 0,
            'high': float(fields[4]) if fields[4] else 0,
            'low': float(fields[5]) if fields[5] else 0,
            'volume': int(float(fields[6])) if fields[6] else 0,
            'amount': float(fields[7]) if fields[7] else 0,
            'date': fields[8] if len(fields) > 8 else '',
            'time': fields[9] if len(fields) > 9 else '',
        }
    except (ValueError, IndexError):
        return {}
