# -*- coding: utf-8 -*-
"""
HTML 解析工具
提供通用的 HTML 表格、文本、JSONP 解析功能
"""

import re
from typing import List, Dict, Any, Optional


def safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数"""
    if value is None or value == '' or value == '-':
        return default
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """安全转换为整数"""
    if value is None or value == '' or value == '-':
        return default
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return default


def extract_field(html: str, field_name: str, default: str = '') -> str:
    """从 HTML 中提取指定字段的值"""
    patterns = [
        rf'{field_name}[：:](\s*[^<]+)',
        rf'<label[^>]*>{field_name}[：:]?\s*</label>\s*<span[^>]*>([^<]+)</span>',
        rf'<td[^>]*>{field_name}[：:]?\s*</td>\s*<td[^>]*>([^<]+)</td>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return default


def parse_html_table(html: str, max_rows: int = 100) -> List[Dict[str, str]]:
    """
    解析 HTML 表格数据
    
    Args:
        html: HTML 字符串
        max_rows: 最大行数
    
    Returns:
        List[Dict]: 表格数据列表，每行一个字典
    """
    results = []
    
    # 提取所有表格行
    row_pattern = r'<tr[^>]*>(.*?)</tr>'
    cell_pattern = r'<td[^>]*>(.*?)</td>|<th[^>]*>(.*?)</th>'
    
    rows = re.findall(row_pattern, html, re.DOTALL)
    
    for row in rows[:max_rows]:
        cells = re.findall(cell_pattern, row, re.DOTALL)
        if cells:
            # 合并 td 和 th 的内容
            row_data = []
            for cell in cells:
                content = cell[0] if cell[0] else cell[1]
                # 清理 HTML 标签
                content = re.sub(r'<[^>]+>', '', content).strip()
                row_data.append(content)
            
            if row_data:
                results.append({'cells': row_data})
    
    return results


def parse_html_text(html: str, tag: str = 'td', max_results: int = 50) -> List[str]:
    """
    从 HTML 中提取指定标签的文本内容
    
    Args:
        html: HTML 字符串
        tag: 标签名称 (td/th/span/div 等)
        max_results: 最大结果数
    
    Returns:
        List[str]: 文本内容列表
    """
    pattern = rf'<{tag}[^>]*>(.*?)</{tag}>'
    matches = re.findall(pattern, html, re.DOTALL)
    
    results = []
    for match in matches[:max_results]:
        # 清理 HTML 标签
        text = re.sub(r'<[^>]+>', '', match).strip()
        if text:
            results.append(text)
    
    return results


def parse_html_jsonp(html: str) -> Dict:
    """
    解析 HTML 中的 JSONP 数据
    
    支持格式:
    - var data = {...};
    - callback({...});
    - ({...});
    
    Args:
        html: 包含 JSONP 的 HTML 字符串
    
    Returns:
        Dict: 解析后的 JSON 数据
    """
    # 尝试多种 JSONP 格式
    patterns = [
        r'var\s+\w+\s*=\s*(\{.*?\});',
        r'\w+\s*\(\s*(\{.*?\})\s*\);',
        r'\(\s*(\{.*?\})\s*\);',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            json_str = match.group(1)
            try:
                import json
                return json.loads(json_str)
            except json.JSONDecodeError:
                continue
    
    return {}


def parse_eastmoney_table(html: str) -> List[Dict[str, Any]]:
    """
    解析东方财富表格数据
    
    东方财富表格通常有固定的列结构，此函数尝试自动识别列名
    """
    results = []
    
    # 提取表头
    header_pattern = r'<thead[^>]*>.*?<tr[^>]*>(.*?)</tr>.*?</thead>'
    header_match = re.search(header_pattern, html, re.DOTALL)
    headers = []
    if header_match:
        header_cells = re.findall(r'<th[^>]*>(.*?)</th>', header_match.group(1), re.DOTALL)
        headers = [re.sub(r'<[^>]+>', '', h).strip() for h in header_cells]
    
    # 提取数据行
    row_pattern = r'<tbody[^>]*>(.*?)</tbody>'
    tbody_match = re.search(row_pattern, html, re.DOTALL)
    if tbody_match:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody_match.group(1), re.DOTALL)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            cell_texts = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            
            if headers:
                # 使用表头作为键
                row_dict = {}
                for i, header in enumerate(headers):
                    if i < len(cell_texts):
                        row_dict[header] = cell_texts[i]
                results.append(row_dict)
            else:
                results.append({'cells': cell_texts})
    
    return results


def parse_sina_data(text: str) -> List[Dict[str, Any]]:
    """
    解析新浪数据格式
    
    新浪数据格式: var hq_str_xxx="field1,field2,...";
    """
    results = []
    
    # 匹配 var hq_str_xxx="..."; 格式
    pattern = r'var\s+hq_str_(\w+)\s*=\s*"([^"]*)";'
    matches = re.findall(pattern, text)
    
    for code, data_str in matches:
        fields = data_str.split(',') if data_str else []
        if fields and fields[0]:  # 有效数据
            results.append({
                'code': code,
                'fields': fields,
                'raw': data_str,
            })
    
    return results


def parse_eastmoney_api(text: str) -> Dict:
    """
    解析东方财富 API 返回的 JSON 数据
    
    东方财富 API 通常返回: {"data": {"diff": [...]}}
    """
    import json
    try:
        data = json.loads(text)
        if data.get('data') and data['data'].get('diff'):
            return data['data']['diff']
        return data
    except json.JSONDecodeError:
        return {}


def clean_html(text: str) -> str:
    """清理 HTML 标签"""
    return re.sub(r'<[^>]+>', '', text).strip()


def extract_numbers(text: str) -> List[float]:
    """从文本中提取所有数字"""
    return [float(x) for x in re.findall(r'-?\d+\.?\d*', text)]
