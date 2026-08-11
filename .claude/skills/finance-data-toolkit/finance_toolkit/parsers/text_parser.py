# -*- coding: utf-8 -*-
"""
文本解析工具
提供通用的文本/字符串解析功能
"""

import re
from typing import List, Dict, Any, Optional, Tuple


def parse_sina_text(text: str) -> List[Dict]:
    """
    解析新浪财经文本数据
    
    格式: var hq_str_xxx="field1,field2,...";
    """
    results = []
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


def parse_eastmoney_text(text: str) -> List[Dict]:
    """
    解析东方财富文本数据
    
    支持 HTML 表格和 JSON 格式
    """
    results = []
    
    # 尝试 JSON 解析
    import json
    try:
        data = json.loads(text)
        if data.get('data') and data['data'].get('diff'):
            return data['data']['diff']
        return [data]
    except (json.JSONDecodeError, AttributeError):
        pass
    
    # 尝试 HTML 表格解析
    row_pattern = r'<tr[^>]*>(.*?)</tr>'
    cell_pattern = r'<td[^>]*>(.*?)</td>'
    
    rows = re.findall(row_pattern, text, re.DOTALL)
    for row in rows:
        cells = re.findall(cell_pattern, row, re.DOTALL)
        cell_texts = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if cell_texts:
            results.append({'cells': cell_texts})
    
    return results


def parse_jsonp_text(text: str) -> Dict:
    """
    解析 JSONP 文本
    
    支持多种 JSONP 格式
    """
    patterns = [
        r'var\s+\w+\s*=\s*(\{.*?\});',
        r'\w+\s*\(\s*(\{.*?\})\s*\);',
        r'\(\s*(\{.*?\})\s*\);',
    ]
    
    import json
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            json_str = match.group(1)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                continue
    
    return {}


def parse_number(text: str, default: float = 0.0) -> float:
    """从文本中提取数字"""
    match = re.search(r'-?\d+\.?\d*', text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return default
    return default


def parse_percentage(text: str, default: float = 0.0) -> float:
    """从文本中提取百分比"""
    match = re.search(r'-?\d+\.?\d*\s*%', text)
    if match:
        try:
            return float(match.group().replace('%', '').strip())
        except ValueError:
            return default
    return default


def parse_date(text: str) -> Optional[str]:
    """从文本中提取日期"""
    patterns = [
        r'(\d{4}-\d{2}-\d{2})',
        r'(\d{4}/\d{2}/\d{2})',
        r'(\d{4}年\d{1,2}月\d{1,2}日)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            date_str = match.group(1)
            # 统一格式为 YYYY-MM-DD
            date_str = date_str.replace('/', '-').replace('年', '-').replace('月', '-').replace('日', '')
            return date_str
    
    return None


def parse_time(text: str) -> Optional[str]:
    """从文本中提取时间"""
    match = re.search(r'(\d{2}:\d{2}:\d{2})', text)
    if match:
        return match.group(1)
    return None


def parse_datetime(text: str) -> Optional[Tuple[str, str]]:
    """从文本中提取日期和时间"""
    date = parse_date(text)
    time = parse_time(text)
    if date or time:
        return (date or '', time or '')
    return None


def split_csv_line(line: str) -> List[str]:
    """安全分割 CSV 行（处理引号内的逗号）"""
    fields = []
    current = []
    in_quotes = False
    
    for char in line:
        if char == '"':
            in_quotes = not in_quotes
        elif char == ',' and not in_quotes:
            fields.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    
    fields.append(''.join(current).strip())
    return fields


def normalize_code(code: str) -> str:
    """标准化股票代码格式"""
    code = code.strip().upper()
    
    # 去除常见前缀
    prefixes = ['SH', 'SZ', 'BJ', 'HK', 'US']
    for prefix in prefixes:
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    
    # 补全后缀
    if len(code) == 6:
        if code.startswith(('60', '68', '90')):
            return f'{code}.SH'
        else:
            return f'{code}.SZ'
    
    return code


def normalize_symbol(symbol: str) -> str:
    """标准化交易符号"""
    symbol = symbol.strip().upper()
    
    # 外汇符号标准化
    forex_patterns = {
        'USDCNY': 'USD/CNY',
        'EURCNY': 'EUR/CNY',
        'GBPUSD': 'GBP/USD',
        'USDJPY': 'USD/JPY',
    }
    
    return forex_patterns.get(symbol, symbol)
